"""文件真实格式嗅探。

职责：读文件头与 Pillow 元信息，判定「扩展名声称的格式」与「真实格式」是否一致。

判定顺序刻意定为**先看魔数、后问Pillow**，因为两者会打架且各有失效场景：

- Pillow 对「扩展名是 ``.jpg``、内容是 GIF」这类文件**照样能打开**，
  报出 ``GIF``——这是它的强项，能穿过伪装；
- 但 Pillow 遇到**截断 / 零帧 / 非图片**文件会抛
  :class:`~PIL.UnidentifiedImageError`，此时若没有魔数兜底，
  这些文件就会从「后缀错乱」退化成「无法判定」，错乱计数随之失真。
- 魔数只认固定前缀，遇到容器变体（如 APNG 仍以 PNG 魔数开头）时**恰好**
  给出正确的 PNG 结论。

因此魔数是**下限**（保证非图片也能被如实归类），Pillow 是**上限**
（保证伪装能穿透）。魔数不认识时才交给 Pillow 兜底。

本模块**只读不写**：不创建、不修改、不删除任何文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from emoji_migrator.config import SUFFIX_TO_FORMAT, SUPPORTED_SUFFIXES, UNKNOWN_FORMAT

__all__ = [
    "SniffResult",
    "sniff",
    "HEADER_PROBE_BYTES",
    "format_of_suffix",
    "is_image_suffix",
]

#: 嗅探时需要读取的头部字节数（覆盖 APNG/GIF/WEBP 头）
HEADER_PROBE_BYTES: int = 64

#: 魔数表：(偏移, 魔数, 真实格式)。偏移为 0 的条目按前缀匹配。
#:
#: BMP 只认文件头两字节 ``BM``——它没有更长的定长前缀，而 BMP 文件头的
#: 第 3-6 字节是**文件总长**，随文件大小变化，不能进魔数。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"BM", "BMP"),
)

#: WEBP 的魔数分成两段（RIFF 与 WEBP 标记），单独处理。
_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"


@dataclass(frozen=True, slots=True)
class SniffResult:
    """单个文件的嗅探结果。"""

    path: Path
    claimed_format: str
    actual_format: str
    size_bytes: int
    mismatch: bool

    def __str__(self) -> str:
        state = "MISMATCH" if self.mismatch else "OK"
        return f"{self.path.name}: {self.claimed_format} -> {self.actual_format} [{state}]"


def format_of_suffix(path: Path) -> str:
    """由文件名后缀得出**声称格式**。

    - 后缀小写后若在 :data:`~emoji_migrator.config.SUFFIX_TO_FORMAT` 中，映射为对应格式；
    - 不认识的后缀（含无后缀）返回 :data:`~emoji_migrator.config.UNKNOWN_FORMAT`。

    不认识的后缀**不抛错**：一个 ``.txt`` 与一个 ``.dat`` 都应被如实记为
    「后缀声称不了格式」，而不是让整批扫描因一个怪文件中断。
    """
    return SUFFIX_TO_FORMAT.get(path.suffix.lower(), UNKNOWN_FORMAT)


def _format_from_magic(head: bytes) -> str:
    """由头部字节判定真实格式；不认识返回空串（交由 Pillow 兜底）。"""
    for magic, fmt in _MAGIC:
        if head.startswith(magic):
            return fmt
    if len(head) >= 12 and head[:4] == _RIFF and head[8:12] == _WEBP_TAG:
        return "WEBP"
    return ""


def _format_from_pillow(path: Path) -> str:
    """由 Pillow 解码结果判定真实格式；无法解码返回 :data:`UNKNOWN_FORMAT`。

    兜住 :class:`~PIL.Image.DecompressionBombError`：损坏文件的头部可能写着
    「宽 3 万 × 高 2 万」这类荒谬尺寸，Pillow 会据此判定「疑似解压炸弹」而抛错。
    那是**正当的安全防护**，但对一个「只读、逐个文件判定格式」的工具来说，
    正确答案应是「这是个解不开的坏文件」，而不是让整批扫描中断。
    """
    try:
        with Image.open(path) as img:
            return (img.format or UNKNOWN_FORMAT).upper()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        # 前四种是「这个文件不是我能解码的图片」的各种表现；
        # Pillow 对损坏数据抛的异常类型并不统一，故一并兜住。
        return UNKNOWN_FORMAT


def sniff(path: Path) -> SniffResult:
    """嗅探单个文件，返回声明格式与真实格式的比对结果。

    ``size_bytes`` 取磁盘上的实际字节数（而非解码后的像素数），
    因为体积相关的告警要的是「文件有多大」。

    两端都判不出来时（后缀不认识、内容也不像图片，如 ``notes.txt``），
    两边同为 :data:`~emoji_migrator.config.UNKNOWN_FORMAT`，判定为**一致**——
    把「都不是图片」报成「后缀错乱」会污染错乱计数。
    """
    claimed = format_of_suffix(path)
    if not path.is_file():
        return SniffResult(
            path=path,
            claimed_format=claimed,
            actual_format=UNKNOWN_FORMAT,
            size_bytes=0,
            mismatch=claimed != UNKNOWN_FORMAT,
        )

    try:
        size_bytes = path.stat().st_size
        head = path.read_bytes()[:HEADER_PROBE_BYTES]
    except OSError:
        # QQ/微信客户端可能在扫描期间替换文件；交给批处理层跳过。
        return SniffResult(
            path=path,
            claimed_format=claimed,
            actual_format=UNKNOWN_FORMAT,
            size_bytes=0,
            mismatch=claimed != UNKNOWN_FORMAT,
        )
    actual = _format_from_magic(head) or _format_from_pillow(path)

    return SniffResult(
        path=path,
        claimed_format=claimed,
        actual_format=actual,
        size_bytes=size_bytes,
        mismatch=claimed != actual,
    )


def is_image_suffix(path: Path) -> bool:
    """后缀是否属于本工具关注的图片类型（大小写不敏感）。"""
    return path.suffix.lower() in SUPPORTED_SUFFIXES
