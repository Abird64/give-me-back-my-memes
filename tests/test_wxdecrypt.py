"""微信表情解密（:mod:`emoji_migrator.wxdecrypt`）测试。

**用合成数据**（``tmp_path`` + Pillow 现造图片 + 现场 AES 加密），
**不碰真实微信文件、不依赖真实密钥**——真机验证已在 RUN-012/-014 单独做过，
这里守的是**逻辑不变量**。

守护三条线：
1. **密钥派生口径**（IV=key、bytes.fromhex）——错一个就全线解不开；
2. **容器切分按结构**（不是按字节搜）——错了会静默产出损坏切片；
3. **失败要报错，不产出半截结果**（``DetectError``）。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from PIL import Image

from emoji_migrator import wxdecrypt

SEED = 1234567890
WXID = "wxid_example_ab12"


def _key() -> bytes:
    return wxdecrypt.derive_key(SEED, WXID)


def _encrypt(plain: bytes, key: bytes | None = None) -> bytes:
    """用与解密**同一口径**加密（IV = key），并补 PKCS7 填充。"""
    k = key or _key()
    pad = 16 - len(plain) % 16
    padded = plain + bytes([pad]) * pad
    return AES.new(k, AES.MODE_CBC, k).encrypt(padded)


def _make_png(
    size: tuple[int, int] = (32, 32),
    colour: tuple[int, int, int] = (200, 30, 30),
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _make_gif(frames: int = 3, size: tuple[int, int] = (24, 24)) -> bytes:
    buf = io.BytesIO()
    imgs = [Image.new("RGB", size, (30 * (i + 1), 80, 200)) for i in range(frames)]
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _make_jpeg(size: tuple[int, int] = (28, 28)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 190, 60)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_wxgf() -> bytes:
    """构造 wxgf：4 字节 magic + 裸 H.265 流（内容随意，只要以 magic 开头）。"""
    return wxdecrypt.WXGF_MAGIC + b"\x00\x00\x00\x01\x40\x01" + b"\xff\xff\xff" * 20


# -- 密钥派生 ------------------------------------------------------------------


def test_derive_key_matches_probe_value() -> None:
    """钉死 A-1 实测值（RUN-20261020-012）。改公式即变红。"""
    assert _key().hex() == "704d9fa4e0c71f3bf0714e7183638b22"


def test_derive_key_is_md5_digest_not_hex_string() -> None:
    """⚠️ 必须用 ``bytes.fromhex``（真实摘要），不是 ``hexdigest()[:16]``。

    后者取的是十六进制**字符串**的 16 个字符当字节——两种解释完全不同的 key。
    """
    import hashlib

    digest = hashlib.md5(f"{SEED}{WXID}EMOTICON".encode()).hexdigest()
    assert wxdecrypt.derive_key(SEED, WXID) == bytes.fromhex(digest)[:16]
    assert wxdecrypt.derive_key(SEED, WXID) != digest[:16].encode()


def test_derive_key_is_16_bytes() -> None:
    """AES-128 需要 16 字节 key。"""
    assert len(_key()) == 16


def test_derive_key_differs_per_wxid() -> None:
    """不同 wxid 必须派生出不同 key（否则串号）。"""
    assert wxdecrypt.derive_key(SEED, "wxid_other") != _key()


def test_derive_key_differs_per_seed() -> None:
    """不同 seed 必须派生出不同 key。"""
    assert wxdecrypt.derive_key(SEED + 1, WXID) != _key()


def test_derive_key_matches_wxscan_deriver() -> None:
    """⚠️ **防两处漂移**：``wxscan.SeedDeriver`` 与本模块**必须算出同一个 key**。

    两处独立实现同一公式（叶子不得互相 import）；一旦漂移，
    症状是「seed 扫到了却解不开」——极难查。"""
    from emoji_migrator.wxscan import SeedDeriver

    assert wxdecrypt.derive_key(SEED, WXID) == SeedDeriver(wxid=WXID).key_for(SEED)


# -- 解密 ---------------------------------------------------------------------


def test_roundtrip_png() -> None:
    """PNG 往返：加密 → 解密 → 字节完全一致（去填充后）。"""
    png = _make_png()
    out = wxdecrypt.decrypt_bytes(_key(), _encrypt(png))
    assert out == png


def test_roundtrip_gif_preserves_animation() -> None:
    """GIF 往返后**仍是多帧动图**（动图存活是本产品存在的核心理由）。"""
    gif = _make_gif(frames=4)
    out = wxdecrypt.decrypt_bytes(_key(), _encrypt(gif))
    with Image.open(io.BytesIO(out)) as im:
        assert getattr(im, "n_frames", 1) == 4


def test_roundtrip_jpeg() -> None:
    jpg = _make_jpeg()
    assert wxdecrypt.decrypt_bytes(_key(), _encrypt(jpg)) == jpg


def test_decrypt_with_wrong_key_yields_unknown_format() -> None:
    """错key → 解出随机数据 → 必须抛 ``DetectError``，**不产出假图片**。"""
    wrong = bytes(16)
    with pytest.raises(wxdecrypt.DetectError):
        wxdecrypt.decrypt_file(wrong, _tmp_with(_encrypt(_make_png())))


def test_decrypt_rejects_short_file() -> None:
    """短于一个 AES 块的文件 → 明确报错。"""
    with pytest.raises(wxdecrypt.DetectError, match="太短"):
        wxdecrypt.decrypt_bytes(_key(), b"\x00" * 8)


def test_decrypt_rejects_non_block_aligned() -> None:
    """密文长度非 16 倍数 → 明确报错（文件损坏或截断）。"""
    with pytest.raises(wxdecrypt.DetectError, match="不是 16 的倍数"):
        wxdecrypt.decrypt_bytes(_key(), b"\x00" * 20)


def _tmp_with(data: bytes, tmp_path: Path | None = None):
    """把字节写进临时文件（``decrypt_file`` 需要路径）。"""
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp()) / "x.bin"
    d.write_bytes(data)
    return d


# -- PKCS7 填充 ----------------------------------------------------------------


@pytest.mark.parametrize("pad", [1, 2, 5, 16])
def test_unpad_strips_valid_padding(pad: int) -> None:
    """合法填充被正确去掉（实测微信文件 pad 值有多种，含 16=整块）。"""
    raw = b"X" * 16
    assert wxdecrypt.unpad_pkcs7(raw + bytes([pad]) * pad) == raw


def test_unpad_keeps_invalid_padding_as_is() -> None:
    """**填充不合法时原样返回而非抛异常**——实测存在恰好对齐的文件。

    抛异常会把「本来能读」的文件变成失败；而魔数校验本就足以兜底。
    """
    data = b"abc" + bytes([0x00])
    assert wxdecrypt.unpad_pkcs7(data) == data


def test_unpad_handles_empty() -> None:
    assert wxdecrypt.unpad_pkcs7(b"") == b""


# -- 格式判定 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_make_png(), wxdecrypt.FORMAT_PNG),
        (_make_gif(), wxdecrypt.FORMAT_GIF),
        (_make_jpeg(), wxdecrypt.FORMAT_JPEG),
        (_make_wxgf(), wxdecrypt.FORMAT_WXGF),
    ],
)
def test_detect_format(payload: bytes, expected: str) -> None:
    assert wxdecrypt.detect_format(payload) == expected


def test_detect_webp_requires_riff_and_webp() -> None:
    """RIFF 还可能是 AVI/WAV，仅4 字节不足以判定 → 必须再看 8~12 字节。"""
    assert wxdecrypt.detect_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == wxdecrypt.FORMAT_WEBP
    with pytest.raises(wxdecrypt.DetectError):
        wxdecrypt.detect_format(b"RIFF\x00\x00\x00\x00AVI LIST")


def test_detect_rejects_unknown() -> None:
    """不认得的格式必须抛错（不猜、不产出半截结果）。"""
    with pytest.raises(wxdecrypt.DetectError, match="不是已知图片格式"):
        wxdecrypt.detect_format(b"\x00" * 32)


@pytest.mark.parametrize("fmt", ["png", "gif", "jpg", "webp", "wxgf"])
def test_target_suffix_covers_all_formats(fmt: str) -> None:
    """五种格式都有目标后缀（wxgf 按动图导出为 .gif）。"""
    assert wxdecrypt.target_suffix_for(fmt)


def test_target_suffix_rejects_unknown() -> None:
    with pytest.raises(wxdecrypt.DetectError):
        wxdecrypt.target_suffix_for("bmp")


# -- 容器切分 ------------------------------------------------------------------


def test_split_png_container() -> None:
    """多个 PNG 首尾相接 → 逐个切出，且每片都能被 Pillow 打开。"""
    parts = [_make_png(size=(16 + i, 16), colour=(200, 10 * i, 30)) for i in range(4)]
    slices = wxdecrypt.split_container(b"".join(parts))
    assert len(slices) == 4
    for original, piece in zip(parts, slices, strict=True):
        assert piece.data == original
        assert piece.fmt == wxdecrypt.FORMAT_PNG


def test_split_gif_container() -> None:
    """多个 GIF 容器 → 逐个切出（这条守护的是**块结构解析**而非字节搜）。"""
    parts = [_make_gif(frames=2 + i, size=(16, 16)) for i in range(3)]
    slices = wxdecrypt.split_container(b"".join(parts))
    assert len(slices) == 3
    for piece in slices:
        assert piece.fmt == wxdecrypt.FORMAT_GIF
        with Image.open(io.BytesIO(piece.data)) as im:
            assert getattr(im, "n_frames", 1) >= 2


def test_split_mixed_container() -> None:
    """PNG / GIF / JPEG 混合容器 → 按各自结构切，顺序保持。"""
    parts = [_make_png(), _make_gif(), _make_jpeg()]
    slices = wxdecrypt.split_container(b"".join(parts))
    assert [s.fmt for s in slices] == [
        wxdecrypt.FORMAT_PNG,
        wxdecrypt.FORMAT_GIF,
        wxdecrypt.FORMAT_JPEG,
    ]


def test_split_gif_with_trailer_like_byte_in_lzw_data() -> None:
    """⚠️ **核心守护**：GIF 的 LZW 数据里可能含 ``0x3B``（trailer 字节）。

    实测踩过：某整包里 ``0x3B`` 出现 18982 次而 ``GIF8``只有 24 个，
    按字节搜会在第 1796 字节误切，切出的碎片连 Pillow 都打不开。

    这里构造一个**压缩数据里含 0x3B** 的真 GIF 来钉死块结构解析。
    """
    # 噪点图LZW 压缩后大概率含 0x3B
    import random

    random.seed(20261020)
    img = Image.new("RGB", (64, 64))
    img.putdata([
        (random.randrange(256), random.randrange(256), random.randrange(256))
        for _ in range(64 * 64)
    ])
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    single = buf.getvalue()
    assert single.count(b"\x3b") > 1, "构造的 GIF 压缩数据里应含多个 0x3B"

    # 两段拼接：若按「找第一个 0x3B」切，会在第一段中间就断开
    slices = wxdecrypt.split_container(single + single)
    assert len(slices) == 2, "必须按块结构切到真正的 trailer"
    for piece in slices:
        with Image.open(io.BytesIO(piece.data)) as im:
            im.seek(max(getattr(im, "n_frames", 1), 1) - 1)


def test_split_container_stops_at_garbage_tail() -> None:
    """尾部有非图片数据时停止（不硬切、不报错）。"""
    parts = [_make_png(), _make_png()]
    slices = wxdecrypt.split_container(b"".join(parts) + b"\x00" * 64)
    assert len(slices) == 2


def test_split_container_on_wxgf_returns_nothing() -> None:
    """wxgf 无自终止标记 → 切不出（返回空），**不猜边界**。"""
    assert wxdecrypt.split_container(_make_wxgf()) == []


def test_split_empty_container() -> None:
    assert wxdecrypt.split_container(b"") == []


def test_split_names_are_sequential_and_suffixed() -> None:
    """切片命名带序号且后缀与格式匹配（避免同名覆盖）。"""
    parts = [_make_png(), _make_gif(), _make_jpeg()]
    slices = wxdecrypt.split_container(b"".join(parts))
    names = [s.name for s in slices]
    assert names == ["store_0001.png", "store_0002.gif", "store_0003.jpg"]
    assert len(set(names)) == len(names), "命名必须唯一"


def test_decrypted_file_suffix_property() -> None:
    """``DecryptedFile.suffix`` 走映射表（wxgf → .gif）。"""
    assert wxdecrypt.DecryptedFile(b"x", wxdecrypt.FORMAT_WXGF, "a").suffix == ".gif"


def test_detect_error_is_value_error_subclass() -> None:
    """``DetectError`` 是 ``ValueError`` 子类（调用方按 ValueError 兜即可）。"""
    assert issubclass(wxdecrypt.DetectError, ValueError)


# -- 合规：模块不得含被禁 API 字面量 --------------------------------------------


def test_no_forbidden_api_literals_in_this_module() -> None:
    """本模块**不复述**被禁 API 名（否则合规扫描会把它当成使用）。

    与 ``test_compliance`` 的全仓扫描同源，这里做模块级自证。
    """
    import test_compliance as tc

    source = Path(__file__).resolve().parents[1] / "src" / "emoji_migrator" / "wxdecrypt.py"
    text = source.read_text(encoding="utf-8")
    hits = [api for api in tc.FORBIDDEN_APIS if api in text]
    assert not hits, f"模块内出现被禁 API 字面量：{hits}"

