"""微信表情解密：把密文还原成常规图片 + ``PersistStore`` 容器切分（EM-003）。

⚠️ **不 import 任何包内模块**（``AGENTS.md`` §3 叶子约束）：
密钥派生用 :func:`emoji_migrator.wxscan.SeedDeriver`的**同款公式**，
但本模块**自己实现**一遍并在注释里指向来源——避免叶子之间互相依赖。
（如实测发现两处公式漂移，应改的是把本模块升为编排层，而不是让叶子 import 叶子。）

**解密口径**（A-1 实证，RUN-20261020-012；2588/2588 文件 100% 解出）：

- ``AES-128-CBC + PKCS7``，**IV = key 本身**（⚠️ 不是全 0）；
- ``key = bytes.fromhex(md5(f"{seed}{wxid}EMOTICON").hexdigest())[:16]``；
- 明文首 4 字节是**格式标识**：``\\x89PNG`` / ``GIF8`` / ``\\xff\\xd8\\xff`` /
  ``wxgf`` / ``RIFF``（微信私有容器）。

**wxgf 是微信私有格式**：4 字节 magic 之后是**裸 H.265 流**，
需交给 ffmpeg 转码（转GIF 保留完整动图，不取静态首帧）。

**容器切分**（``PersistStore`` 整包）：一个文件里首尾相接着多个表情，
按**结构**切分而非按magic 搜索——压缩数据里可能偶然出现魔数字节，
按搜索切会产生损坏切片。切分点：
PNG→``IEND`` 块结束、JPEG→``EOI``（``FFD9``）、GIF→trailer ``0x3B``。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "WXGF_MAGIC",
    "FORMAT_PNG",
    "FORMAT_GIF",
    "FORMAT_JPEG",
    "FORMAT_WXGF",
    "FORMAT_WEBP",
    "DetectError",
    "DecryptedFile",
    "derive_key",
    "unpad_pkcs7",
    "decrypt_bytes",
    "decrypt_file",
    "detect_format",
    "iter_container_slices",
    "split_container",
    "target_suffix_for",
]

WXGF_MAGIC = b"wxgf"

FORMAT_PNG = "png"
FORMAT_GIF = "gif"
FORMAT_JPEG = "jpg"
FORMAT_WXGF = "wxgf"
FORMAT_WEBP = "webp"

#: 格式 → 导出后缀。wxgf 转码后统一是 GIF（它本质是动图）。
_SUFFIX_BY_FORMAT = {
    FORMAT_PNG: ".png",
    FORMAT_GIF: ".gif",
    FORMAT_JPEG: ".jpg",
    FORMAT_WEBP: ".webp",
    FORMAT_WXGF: ".gif",
}


class DetectError(ValueError):
    """明文首部不符合任何已知图片格式——报错，**不产出半截结果**。

    这条很重要：拿不准格式时若「猜一个」或「直接写文件」，
    产出的就是**损坏的表情文件**，用户看不出问题在哪。
    """


@dataclass(frozen=True)
class DecryptedFile:
    """一个解密产物：数据 + 格式 + 目标文件名。"""

    data: bytes
    fmt: str
    name: str

    @property
    def suffix(self) -> str:
        return target_suffix_for(self.fmt)


def derive_key(seed: int, wxid: str) -> bytes:
    """派生 AES key（**与 :mod:`emoji_migrator.wxscan` 同款公式**）。

    ⚠️ 两处口径必须完全一致，不一致的后果是「扫到 seed 也解不开」——
    所以这里独立实现一份并在测试里断言**两者结果相同**
    （见 ``tests/test_wxdecrypt.py`` 的密钥一致性用例）。
    """
    digest = hashlib.md5(f"{seed}{wxid}EMOTICON".encode()).hexdigest()
    # ⚠️ 必须 bytes.fromhex（真实 md5 摘要），不是 hexdigest()[:16]
    # （那是十六进制**字符串**当字节用——RUN-012 前一轮就栽在这）。
    return bytes.fromhex(digest)[:16]


def unpad_pkcs7(data: bytes) -> bytes:
    """去PKCS7 填充。

    ⚠️ **填充不合法时原样返回**而非抛异常：微信文件实测 ``pad=2`` 等多种值，
    且部分文件可能无填充（恰好对齐）。抛异常会让「本来能读」的文件变成失败。
    返回原值 + 调用方靠魔数校验兜底（魔数不对会走 :class:`DetectError`）。
    """
    if not data:
        return data
    pad = data[-1]
    if 1 <= pad <= 16 and data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data


def decrypt_bytes(key: bytes, data: bytes) -> bytes:
    """AES-128-CBC 解密，**IV = key**（⚠️ 不是全 0）。"""
    from Crypto.Cipher import AES

    if len(data) < 16:
        raise DetectError("文件太短，不足一个 AES 块（16 字节）")
    if len(data) % 16 != 0:
        # 密文长度必须是 16 的倍数；不是说明文件损坏或截断。
        raise DetectError(f"密文长度 {len(data)} 不是 16 的倍数（文件可能已损坏）")
    plain = AES.new(key, AES.MODE_CBC, key).decrypt(data)
    return unpad_pkcs7(plain)


def detect_format(plain: bytes) -> str:
    """从明文首部判定格式；不认得就抛 :class:`DetectError`。

    判据只认 **≥3 字节的强特征**（``wxgf`` 是 4 字节）——
    2 字节特征在任何密钥候选下都会误命中（RUN-012 的教训）。
    """
    head = plain[:8]
    if head.startswith(b"\x89PNG"):
        return FORMAT_PNG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return FORMAT_GIF
    if head.startswith(b"\xff\xd8\xff"):
        return FORMAT_JPEG
    if head.startswith(WXGF_MAGIC):
        return FORMAT_WXGF
    if head.startswith(b"RIFF") and plain[8:12] == b"WEBP":
        return FORMAT_WEBP
    raise DetectError(
        f"解密结果不是已知图片格式（首 8 字节 {plain[:8].hex()}）"
        "——密钥可能不匹配，或微信已改算法"
    )


def decrypt_file(key: bytes, source: Path, name: str | None = None) -> DecryptedFile:
    """解密单个文件，返回 :class:`DecryptedFile`（**不落盘**）。"""
    plain = decrypt_bytes(key, source.read_bytes())
    fmt = detect_format(plain)
    return DecryptedFile(data=plain, fmt=fmt, name=name or source.name)


def target_suffix_for(fmt: str) -> str:
    """格式 → 目标文件后缀（wxgf 按动图导出为 ``.gif``）。"""
    try:
        return _SUFFIX_BY_FORMAT[fmt]
    except KeyError:
        raise DetectError(f"未知格式：{fmt}") from None


# ---- PersistStore 容器切分 ----------------------------------------------------

#: PNG 的 IEND 块结束标记（``IEND`` + CRC32，共 4 字节固定尾部）
_PNG_IEND_TAIL = b"\x00\x00\x00\x00IEND\xaeB`\x82"
#: GIF 的 trailer 就是结尾的 ``0x3B``（单字节）
_GIF_TRAILER = b"\x3b"
#: JPEG 的 EOI 标记
_JPEG_EOI = b"\xff\xd9"


def _iter_slices(
    data: bytes, start: int, end_markers: tuple[tuple[bytes, int], ...]
) -> Iterator[tuple[int, int, bytes]]:
    """从 ``start`` 起按结束标记切分，产出 ``(切片起点, 切片终点, 魔数)``。

    ⚠️ **按结构切，不按魔数搜索**：压缩数据里可能偶然出现 ``FFD9`` / ``IEND``，
    按「搜索下一个魔数」切会产生损坏切片。正确做法是
    「读首部→ 算出这个格式的结构长度 → 在结构终点切」。
    """
    pos = start
    total = len(data)
    while pos < total:
        magic = _magic_at(data, pos)
        if magic is None:
            return  # 剩余部分不是图片 → 停止，不硬切
        end = _structure_end(data, pos, magic)
        if end is None:
            return
        yield pos, end, magic
        pos = end


def _magic_at(data: bytes, pos: int) -> bytes | None:
    """读 ``pos`` 处的格式魔数；不是已知格式返回 ``None``。"""
    head = data[pos : pos + 8]
    if head.startswith(b"\x89PNG"):
        return b"\x89PNG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return b"GIF8"
    if head.startswith(b"\xff\xd8\xff"):
        return b"\xff\xd8\xff"
    if head.startswith(WXGF_MAGIC):
        return WXGF_MAGIC
    return None


def _structure_end(data: bytes, start: int, magic: bytes) -> int | None:
    """算出从 ``start`` 开始的那个图片的**结构终点**（不含）。

    ⚠️ **GIF 必须解析块结构，不能搜 trailer**（实测踩过）：
    实测某个整包里 ``0x3B`` 出现 **18982** 次而 ``GIF8`` 只有 24 个——
    GIF 的 LZW 压缩数据里``0x3B`` 是常见字节，**「找第一个 0x3B」会在
    第 1796 字节就误切**，切出的碎片连Pillow 都打不开。
    正确做法是按 GIF 规范走块链（扩展块 / 全局色表 / 图像块 + LZW 子块），
    遇到**真正的 trailer** 才算结束。
    """
    if magic == b"\x89PNG":
        idx = data.find(_PNG_IEND_TAIL, start)
        return None if idx < 0 else idx + len(_PNG_IEND_TAIL)
    if magic == b"GIF8":
        return _gif_structural_end(data, start)
    if magic == b"\xff\xd8\xff":
        idx = data.find(_JPEG_EOI, start)
        return None if idx < 0 else idx + 2
    if magic == WXGF_MAGIC:
        # wxgf 是裸 H.265 流，无自终止标记 → 容器里不可能可靠判断终点。
        # ⚠️ 故调用方**不应**把 wxgf 放进容器切分路径：
        # 实测 PersistStore 里的格式是 GIF/PNG/JPEG（RUN-012：13/1/1），
        # 无 wxgf。遇到 wxgf 返回 None（切不了就停，不猜）。
        return None
    return None


def _gif_structural_end(data: bytes, start: int) -> int | None:
    """按 GIF 块结构走到底，返回 trailer 之后的位置。

    块序列（GIF89a 规范）::

        header(6) + logical screen descriptor(7)
        [global color table]
        *block:
            0x21 扩展块（0xF9/0xFE/0x01… + sub-block 链，以 0x00 结束）
            0x2C 图像描述符 + [local color table] + LZW 子块链（以 0x00 结束）
            0x3B trailer -> 结束
    """
    total = len(data)
    pos = start
    if pos + 13 > total:
        return None
    pos += 6  # GIF89a 头
    packed = data[pos + 4]
    pos += 7  # logical screen descriptor
    if packed & 0x80:  # 有全局颜色表
        pos += 3 * (2 ** ((packed & 0x07) + 1))
    if pos >= total:
        return None

    while pos < total:
        marker = data[pos]
        if marker == 0x3B:  # trailer
            return pos + 1
        if marker == 0x21:  # 扩展块
            pos = _skip_sub_blocks(data, pos + 2)
            if pos is None:
                return None
            continue
        if marker == 0x2C:  # 图像描述符
            if pos + 10 > total:
                return None
            local_packed = data[pos + 9]
            pos += 10
            if local_packed & 0x80:  # 局部颜色表
                pos += 3 * (2 ** ((local_packed & 0x07) + 1))
            # ⚠️ **必须跳过 LZW 最小码长那1 字节**（图像描述符与子块链之间）——
            # 漏掉它会让首个「子块长度」读到错误值，解析从此全错
            # （实测踩过：漏这字节时pos 落在图像数据中间，链长读成随机值）。
            pos += 1
            pos = _skip_sub_blocks(data, pos)  # LZW 子块链
            if pos is None:
                return None
            continue
        # 未知标记：结构损坏，停止（不硬切）
        return None
    return None


def _skip_sub_blocks(data: bytes, pos: int) -> int | None:
    """跳过「子块链」：每个子块 ``[长度][数据...]``，以 ``0x00`` 长度收尾。"""
    total = len(data)
    while pos < total:
        size = data[pos]
        if size == 0:
            return pos + 1
        pos += 1 + size
    return None


def iter_container_slices(data: bytes) -> Iterator[DecryptedFile]:
    """把一个解密后的 ``PersistStore`` 整包按结构切成多个表情。"""
    index = 0
    for pos, end, magic in _iter_slices(data, 0, ()):  # noqa: B007 - 签名一致
        chunk = data[pos:end]
        fmt = detect_format(chunk)
        index += 1
        yield DecryptedFile(data=chunk, fmt=fmt, name=f"store_{index:04d}{target_suffix_for(fmt)}")


def split_container(data: bytes) -> list[DecryptedFile]:
    """:func:`iter_container_slices` 的列表版本。"""
    return list(iter_container_slices(data))

