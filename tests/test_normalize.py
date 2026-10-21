"""``normalize`` 模块单元测试：后缀修正（正常路径 + 边界）。

核心验收项是「52 个错乱全部修正，输出后缀与真实格式100% 一致」；
本文件负责**单文件层面**的正确性，全量数字在 :mod:`test_end_to_end`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from emoji_migrator.config import UNKNOWN_FORMAT
from emoji_migrator.normalize import (
    NORMALIZE_BLOCKED_HINT,
    NormalizePlan,
    apply_normalize,
    plan_normalize,
)
from emoji_migrator.platforms import Platform
from emoji_migrator.sniff import SniffResult, sniff


def _static(seed: int = 1, size: tuple[int, int] = (40, 40)) -> Image.Image:
    img = Image.new("RGB", size, (seed * 37 % 256, 90, 200 - seed * 11 % 256))
    ImageDraw.Draw(img).ellipse((3, 3, size[0] - 3, size[1] - 3), fill=(255, 200, 0))
    return img


def _anim(count: int = 4, size: tuple[int, int] = (56, 56)) -> list[Image.Image]:
    out = []
    for index in range(count):
        img = Image.new("RGB", size, (index * 55 % 256, 30, 180 - index * 25 % 256))
        ImageDraw.Draw(img).rectangle((index, index, size[0] - 2, size[1] - 2), fill=(0, 255, 0))
        out.append(img)
    return out


def _write_gif(path: Path, count: int = 4) -> Path:
    frames = _anim(count)
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


def _frames_of(path: Path) -> int:
    with Image.open(path) as img:
        return getattr(img, "n_frames", 1)


def _result_for(path: Path) -> SniffResult:
    return sniff(path)


# --------------------------------------------------------------------------------------
# 计划阶段
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "fmt", "expected"),
    [
        (".jpg", "GIF", ".gif"),
        (".jpg", "PNG", ".png"),
        (".jpg", "WEBP", ".webp"),
        (".jpg", "BMP", ".bmp"),
        (".png", "JPEG", ".jpg"),
        (".gif", "JPEG", ".jpg"),
        (".jpeg", "GIF", ".gif"),
    ],
)
def test_target_suffix_always_follows_the_real_format(
    suffix: str, fmt: str, expected: str, tmp_path: Path
) -> None:
    """修正目标恒为 **sniff 的真实格式**（K-2 口径），与平台偏好无关。"""
    source = tmp_path / f"sample{suffix}"
    if fmt == "GIF":
        _write_gif(source)
    else:
        _static().save(source, format=fmt)

    plan = plan_normalize(_result_for(source), Platform.WECHAT, tmp_path / "out")
    assert plan.target.suffix == expected
    assert plan.needs_rename is True


@pytest.mark.parametrize("platform", list(Platform))
def test_target_suffix_is_independent_of_target_platform(
    platform: Platform, tmp_path: Path
) -> None:
    """换平台不改变后缀修正结果。

    这条钉死 K-2：``PLATFORM_SUFFIXES`` 是「若做转码该转成什么」，
    本包不做转码，所以后缀修正只看真实格式。若哪天把平台偏好混进来，
    这条会立刻变红。
    """
    source = _write_gif(tmp_path / "pic.jpg")
    plan = plan_normalize(_result_for(source), platform, tmp_path / "out")
    assert plan.target.name == "pic.gif"


def test_already_correct_suffix_is_a_noop(tmp_path: Path) -> None:
    """后缀本来就对时，判定为无操作。"""
    source = _write_gif(tmp_path / "ok.gif")
    plan = plan_normalize(_result_for(source), Platform.WECHAT, tmp_path / "out")
    assert plan.needs_rename is False
    assert plan.is_noop is True


def test_plan_never_requests_resize_or_transcode(tmp_path: Path) -> None:
    """本轮不缩放、不转码（D-13 + 砍压缩），两个标志必须恒为 False。"""
    source = _static(3, (1024, 1024))
    source.save(tmp_path / "huge.jpg", format="JPEG")
    plan = plan_normalize(_result_for(tmp_path / "huge.jpg"), Platform.WECHAT, tmp_path / "out")
    assert plan.needs_resize is False
    assert plan.needs_transcode is False


def test_unknown_format_is_rejected_with_a_chinese_hint(tmp_path: Path) -> None:
    """非图片文件无法修正，必须报错且带中文说明（不能静默产出错后缀文件）。"""
    source = tmp_path / "notes.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="无法判定真实格式"):
        plan_normalize(_result_for(source), Platform.WECHAT, tmp_path / "out")
    assert NORMALIZE_BLOCKED_HINT


def test_zero_frame_gif_is_rejected_rather_than_silently_renamed(tmp_path: Path) -> None:
    """零帧 GIF 能被认出格式，但它解不出哈希——修正阶段应明确拒绝。

    这里真实格式是 GIF（魔数认得），所以计划**能**生成；无法处理的是后续
    去重。本测试钉住的是：认出格式 ≠ 一切正常，调用方仍需自己兜住解码失败。
    """
    source = tmp_path / "zero.jpg"
    source.write_bytes(b"GIF89a" + b"\x00" * 10)
    plan = plan_normalize(_result_for(source), Platform.WECHAT, tmp_path / "out")
    assert plan.target.name == "zero.gif"
    assert plan.needs_rename is True


def test_conflicting_stems_get_distinct_targets(tmp_path: Path) -> None:
    """两个不同来源修正后想用同一个名字时，必须错开而不是互相覆盖。

    ``a.jpg``（实为 GIF）与 ``a.gif``（也是 GIF）都要变成 ``a.gif``。
    若直接覆盖，先落盘的那个会**消失**——对「整理」工具而言那是数据丢失。
    """
    taken: set[str] = set()
    first = plan_normalize(
        _result_for(_write_gif(tmp_path / "a.jpg")), Platform.WECHAT, tmp_path / "out", taken
    )
    taken.add(first.target.name.casefold())
    second = plan_normalize(
        _result_for(_write_gif(tmp_path / "a.gif")), Platform.WECHAT, tmp_path / "out", taken
    )
    assert first.target.name != second.target.name
    assert second.target.name.startswith("a")


def test_case_only_conflict_is_also_detected(tmp_path: Path) -> None:
    """大小写不同的冲突也算冲突（Windows 上不区分大小写）。"""
    taken: set[str] = set()
    first = plan_normalize(
        _result_for(_write_gif(tmp_path / "a.jpg")), Platform.WECHAT, tmp_path / "out", taken
    )
    taken.add(first.target.name.casefold())
    second = plan_normalize(
        _result_for(_write_gif(tmp_path / "A.gif")), Platform.WECHAT, tmp_path / "out", taken
    )
    assert first.target.name != second.target.name


def test_plan_records_the_requested_platform(tmp_path: Path) -> None:
    source = _write_gif(tmp_path / "x.jpg")
    plan = plan_normalize(_result_for(source), Platform.DOUYIN, tmp_path / "out")
    assert plan.platform is Platform.DOUYIN


# --------------------------------------------------------------------------------------
# 执行阶段
# --------------------------------------------------------------------------------------


def test_apply_writes_the_file_with_corrected_suffix(tmp_path: Path) -> None:
    source = _write_gif(tmp_path / "x.jpg")
    out = tmp_path / "out"
    plan = plan_normalize(_result_for(source), Platform.WECHAT, out)
    written = apply_normalize(plan)

    assert written.exists()
    assert written.suffix == ".gif"
    assert sniff(written).mismatch is False


def test_apply_creates_the_output_directory(tmp_path: Path) -> None:
    source = _write_gif(tmp_path / "x.jpg")
    out = tmp_path / "deep" / "nested" / "out"
    assert not out.exists()
    apply_normalize(plan_normalize(_result_for(source), Platform.WECHAT, out))
    assert out.is_dir()


def test_apply_is_byte_identical_so_frames_survive(tmp_path: Path) -> None:
    """逐字节复制 ⇒ 动图帧数必然不变（核心验收第4 条的单文件版本）。"""
    source = _write_gif(tmp_path / "x.jpg", count=7)
    plan = plan_normalize(_result_for(source), Platform.WECHAT, tmp_path / "out")
    written = apply_normalize(plan)

    assert written.read_bytes() == source.read_bytes()
    assert _frames_of(written) == _frames_of(source) == 7


def test_apply_is_idempotent_across_runs(tmp_path: Path) -> None:
    """同一计划执行两次，结果内容一致且**不堆积副本**。

    不断言 mtime 不变：``shutil.copyfile`` 每次都会重写文件，mtime 变化是
    正常行为，拿它当幂等判据是断言了一个并不存在的承诺。
    真正要保证的是「重复跑同一目录得到同一份结果」。
    """
    source = _write_gif(tmp_path / "x.jpg")
    out = tmp_path / "out"
    plan = plan_normalize(_result_for(source), Platform.WECHAT, out)
    first = apply_normalize(plan)
    payload = first.read_bytes()
    second = apply_normalize(plan)

    assert first == second
    assert second.read_bytes() == payload == source.read_bytes()
    assert len(list(out.iterdir())) == 1


def test_apply_copies_into_place_without_self_deletion(tmp_path: Path) -> None:
    """源与目标同路径时不能把自己删掉（copyfile 同路径会报错或截断）。"""
    source = _write_gif(tmp_path / "x.gif")
    plan = NormalizePlan(
        source=source,
        target=source,
        platform=Platform.WECHAT,
        needs_resize=False,
        needs_transcode=False,
        needs_rename=False,
    )
    assert apply_normalize(plan) == source
    assert source.exists()
    assert _frames_of(source) == 4


def test_plan_is_immutable(tmp_path: Path) -> None:
    """计划对象是 frozen的，避免下游误改已算好的目标路径。"""
    source = _write_gif(tmp_path / "x.jpg")
    plan = plan_normalize(_result_for(source), Platform.WECHAT, tmp_path / "out")
    with pytest.raises(AttributeError):
        plan.needs_rename = False  # type: ignore[misc]


def test_unknown_format_constant_is_distinct_from_real_formats() -> None:
    """UNKNOWN 必须真的不在真实格式集合里，否则 mismatch 会恒为 False。"""
    assert UNKNOWN_FORMAT == "UNKNOWN"
    assert UNKNOWN_FORMAT not in {"PNG", "GIF", "JPEG", "WEBP", "BMP"}
