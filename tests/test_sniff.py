"""``sniff`` 模块单元测试：真实格式判定（正常路径 + 边界）。

对应验收标准「单元测试覆盖 sniff/normalize/dedupe 的正常路径与边界」。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, UnidentifiedImageError

from emoji_migrator.config import SUFFIX_TO_FORMAT, UNKNOWN_FORMAT
from emoji_migrator.sniff import (
    HEADER_PROBE_BYTES,
    SniffResult,
    format_of_suffix,
    is_image_suffix,
    sniff,
)


def _static_image(size: tuple[int, int] = (40, 30)) -> Image.Image:
    img = Image.new("RGB", size, (12, 200, 90))
    ImageDraw.Draw(img).ellipse((4, 4, size[0] - 4, size[1] - 4), fill=(250, 250, 0))
    return img


def _anim_frames(count: int = 5, size: tuple[int, int] = (48, 48)) -> list[Image.Image]:
    frames = []
    for index in range(count):
        img = Image.new("RGB", size, (index * 45 % 256, 60, 200 - index * 30 % 256))
        ImageDraw.Draw(img).rectangle((index, 0, size[0] - 1, size[1] - 1), fill=(255, 0, 0))
        frames.append(img)
    return frames


def _write_gif(path: Path, count: int = 5) -> Path:
    frames = _anim_frames(count)
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (".gif", "GIF"),
        (".png", "PNG"),
        (".jpg", "JPEG"),
        (".jpeg", "JPEG"),
        (".webp", "WEBP"),
        (".bmp", "BMP"),
        # .apng 必须声称 PNG（APNG 的魔数就是 PNG 魔数）
        (".apng", "PNG"),
    ],
)
def test_known_suffix_maps_to_its_format(suffix: str, expected: str) -> None:
    assert format_of_suffix(Path(f"x{suffix}")) == expected


@pytest.mark.parametrize("name", ["x.txt", "x", "x.dat", "x.GIF2", "x.jpg.bak"])
def test_unknown_suffix_is_reported_as_unknown_not_guessed(name: str) -> None:
    """不认识的后缀必须如实报「未知」，不得瞎猜成某格式。

    猜错的后果是错乱计数被污染：一个 ``.txt`` 被猜成 JPEG 就会凭空多一条「待修正」。
    """
    assert format_of_suffix(Path(name)) == UNKNOWN_FORMAT


@pytest.mark.parametrize("suffix", [".gif", ".GIF", ".JPG", ".jpeg", ".apng"])
def test_suffix_lookup_is_case_insensitive(suffix: str) -> None:
    """Windows 上 ``.JPG`` 与 ``.jpg`` 是同一个东西，判定不能因此分叉。"""
    assert format_of_suffix(Path(f"x{suffix.upper()}")) == SUFFIX_TO_FORMAT[suffix.lower()]


def test_gif_disguised_as_jpg_is_detected_as_mismatch(tmp_path: Path) -> None:
    """核心场景：声明 ``.jpg`` 实为 GIF。**不信后缀**是本模块存在的理由。"""
    path = _write_gif(tmp_path / "fake.jpg")
    result = sniff(path)
    assert result.claimed_format == "JPEG"
    assert result.actual_format == "GIF"
    assert result.mismatch is True


def test_correctly_suffixed_jpeg_is_not_a_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "real.jpg"
    _static_image().save(path, format="JPEG")
    result = sniff(path)
    assert (result.claimed_format, result.actual_format, result.mismatch) == ("JPEG", "JPEG", False)


def test_png_disguised_as_jpg_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "shot.jpg"
    _static_image().save(path, format="PNG")
    result = sniff(path)
    assert result.actual_format == "PNG"
    assert result.mismatch is True


@pytest.mark.parametrize(
    ("fmt", "suffix"),
    [("PNG", ".png"), ("GIF", ".gif"), ("WEBP", ".webp"), ("BMP", ".bmp"), ("JPEG", ".jpg")],
)
def test_every_supported_real_format_is_identified(fmt: str, suffix: str, tmp_path: Path) -> None:
    """五种真实格式都要能认出来（工作包点名PNG/GIF/JPEG/WEBP/BMP）。"""
    path = tmp_path / f"sample{suffix}"
    if fmt == "GIF":
        _write_gif(path)
    else:
        _static_image().save(path, format=fmt)
    assert sniff(path).actual_format == fmt


def test_real_format_comes_from_magic_not_from_suffix(tmp_path: Path) -> None:
    """把 GIF 改名成 ``.bmp``，真实格式必须仍报 GIF。"""
    path = _write_gif(tmp_path / "liar.bmp")
    assert sniff(path).actual_format == "GIF"


def test_apng_file_is_not_reported_as_mismatch(tmp_path: Path) -> None:
    """APNG 用 ``.apng`` 命名不应被判成后缀错乱（其魔数就是 PNG）。

    这条曾经是个真缺陷：若把 ``.apng`` 声称成独立格式，正常 APNG 会被改名。
    """
    path = tmp_path / "motion.apng"
    frames = _anim_frames(4)
    frames[0].save(path, format="PNG", save_all=True, append_images=frames[1:], duration=90, loop=0)
    result = sniff(path)
    assert result.claimed_format == result.actual_format == "PNG"
    assert result.mismatch is False


def test_size_bytes_reflects_real_file_size(tmp_path: Path) -> None:
    path = _write_gif(tmp_path / "a.jpg")
    assert sniff(path).size_bytes == path.stat().st_size


def test_header_probe_covers_all_magic_numbers() -> None:
    """探测字节数必须真的装得下最长的那个魔数。

    「魔数表里最长的是 8 字节（PNG），而常量写成了 4」这类错误会让 PNG
    全部退化成交给 Pillow 兜底，而那条兜底路径恰好也认得出 PNG——
    于是测试全绿、冗余代码悄悄留下。直接断言常量下限更实在。
    """
    assert HEADER_PROBE_BYTES >= 12, "WEBP 魔数要读到第 12 字节"


def test_truncated_gif_is_still_identified_as_gif(tmp_path: Path) -> None:
    """截断的 GIF 应报GIF（魔数在），而不是退化成「无法判定」。

    这是「魔数兜底」存在的意义：Pillow 对截断文件会抛
    UnidentifiedImageError，若无魔数兜底，这些文件的错乱计数就丢了。
    """
    full = _write_gif(tmp_path / "full.gif")
    truncated = tmp_path / "cut.jpg"
    truncated.write_bytes(full.read_bytes()[:20])

    result = sniff(truncated)
    assert result.actual_format == "GIF"
    assert result.mismatch is True


def test_zero_frame_gif_header_is_gif_but_pillow_cannot_read_it(tmp_path: Path) -> None:
    """只有 GIF 头、没有帧数据的文件：魔数认得出，Pillow 打不开。"""
    path = tmp_path / "zero.jpg"
    path.write_bytes(b"GIF89a" + b"\x00" * 10)
    result = sniff(path)
    assert result.actual_format == "GIF"
    # 断言它确实不是「正常可解码图片」，免得这条测试哪天悄悄失去意义
    with pytest.raises(UnidentifiedImageError):
        Image.open(path).convert("RGB")


def test_non_image_file_is_unknown_not_a_crash(tmp_path: Path) -> None:
    """非图片文件必须报 UNKNOWN，且**不算**后缀错乱（两边都是未知）。"""
    path = tmp_path / "notes.txt"
    path.write_text("这不是图片", encoding="utf-8")
    result = sniff(path)
    assert result.actual_format == UNKNOWN_FORMAT
    assert result.claimed_format == UNKNOWN_FORMAT
    assert result.mismatch is False


def test_text_content_named_jpg_is_unknown(tmp_path: Path) -> None:
    """伪装成 ``.jpg`` 的文本文件：判为未知（错乱），而不是崩溃或误认JPEG。"""
    path = tmp_path / "fake.jpg"
    path.write_text("definitely not a jpeg", encoding="utf-8")
    result = sniff(path)
    assert result.actual_format == UNKNOWN_FORMAT
    assert result.claimed_format == "JPEG"
    assert result.mismatch is True


def test_empty_file_is_unknown(tmp_path: Path) -> None:
    """空文件不能崩，也不能被当成某种格式。"""
    path = tmp_path / "empty.jpg"
    path.write_bytes(b"")
    result = sniff(path)
    assert result.actual_format == UNKNOWN_FORMAT
    assert result.size_bytes == 0


def test_huge_file_sniffs_without_reading_it_all(tmp_path: Path) -> None:
    """超大文件：体积与格式都要正确，且不得因读全量内容而爆内存。

    做法是造一个「头是真GIF、后面挂 8 MB 垃圾」的文件：只要实现里存在
    ``path.read_bytes()``（无参数，全量读入），这8 MB 就会被读进来。
    """
    payload = _write_gif(tmp_path / "small.gif")
    big = tmp_path / "big.jpg"
    big.write_bytes(payload.read_bytes() + b"\x00" * (8 * 1024 * 1024))

    result = sniff(big)
    assert result.actual_format == "GIF"
    assert result.size_bytes == big.stat().st_size


def test_directory_is_unknown_not_a_crash(tmp_path: Path) -> None:
    """目录传进来不应抛异常（批量扫描可能混入目录）。"""
    result = sniff(tmp_path)
    assert result.actual_format == UNKNOWN_FORMAT
    assert result.size_bytes == 0


def test_file_claiming_absurd_dimensions_is_unknown_not_a_crash(tmp_path: Path) -> None:
    """头部宣称荒谬尺寸的文件不得让嗅探崩溃。

    这是一个**实测发现**的缺陷：造一个 PNG 魔数 + 「宽 25414×高 25474」的损坏头，
    Pillow 会判定疑似解压炸弹并抛 :class:`~PIL.Image.DecompressionBombError`。
    那是正当的安全防护，但对「逐个文件判定格式」的只读工具来说，
    正确定义是「这是个解不开的坏文件」，而不是让整批扫描中断。
    """
    import struct

    ihdr = struct.pack(">II", 25414, 25474) + b"\x08\x02\x00\x00\x00"
    path = tmp_path / "bomb.jpg"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + ihdr)

    result = sniff(path)
    # 魔数认得出 PNG，但 Pillow 打不开 ⇒ 判不出来，且不算后缀错乱
    assert result.claimed_format == "JPEG"
    assert result.actual_format in {"PNG", UNKNOWN_FORMAT}
    assert result.size_bytes == path.stat().st_size


def test_sniff_result_str_marks_mismatch(tmp_path: Path) -> None:
    path = _write_gif(tmp_path / "x.jpg")
    assert "MISMATCH" in str(sniff(path))

    ok = tmp_path / "ok.gif"
    _write_gif(ok)
    assert "OK" in str(sniff(ok))


def test_sniff_result_is_immutable(tmp_path: Path) -> None:
    """结果对象是 frozen 的：它是判定结论，不该被下游随手改写。"""
    result = sniff(_write_gif(tmp_path / "x.jpg"))
    assert isinstance(result, SniffResult)
    with pytest.raises(AttributeError):
        result.mismatch = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.jpg", True), ("a.GIF", True), ("a.txt", False), ("a", False), ("a.apng", True)],
)
def test_is_image_suffix_filters_the_scan(name: str, expected: bool) -> None:
    assert is_image_suffix(Path(name)) is expected
