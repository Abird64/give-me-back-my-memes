"""全局档位与阈值常量。

本模块只放**跨模块共享的纯常量**，不含任何 IO 与算法逻辑。
具体算法在 EM-002 起实现。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

__all__ = [
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "FIXTURES_DIR",
    "QualityTier",
    "TIER_MAX_EDGE",
    "SUPPORTED_SUFFIXES",
    "SUFFIX_TO_FORMAT",
    "FORMAT_TO_SUFFIX",
    "BYTES_PER_KIB",
    "UNKNOWN_FORMAT",
]


class QualityTier(StrEnum):
    """压缩档位。值越大越清晰、文件越大。"""

    DRAFT = "draft"
    STANDARD = "standard"
    HIGH = "high"


#: 仓库根目录（src/emoji_migrator/config.py -> 仓库根）
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
PACKAGE_ROOT: Path = Path(__file__).resolve().parent
FIXTURES_DIR: Path = PROJECT_ROOT / "fixtures"

#: 各档位的最长边像素上限，由 EM-002 的 normalize 消费
TIER_MAX_EDGE: dict[QualityTier, int] = {
    QualityTier.DRAFT: 128,
    QualityTier.STANDARD: 200,
    QualityTier.HIGH: 256,
}

#: 嗅探阶段关注的文件后缀（小写，含点）
SUPPORTED_SUFFIXES: frozenset[str] = frozenset(
    {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".apng"}
)

#: 无法判定真实格式时的占位值。**不是**一种格式，而是「判不出来」这一状态本身——
#: 把它与任何真实格式名放在同一个命名空间里，才能让「后缀声称的格式」与
#: 「真实格式」用同一套字符串比较（见 :mod:`emoji_migrator.sniff`）。
UNKNOWN_FORMAT: str = "UNKNOWN"

#: 后缀 → 该后缀**声称**的格式。嗅探比对的是「声称」与「实测」是否一致。
#:
#: ``.apng`` 声称的是 ``PNG``：APNG 是 PNG 的动画扩展，其文件头的 PNG 魔数与
#: PNG 完全一致（实测 Pillow 也把 APNG 报成 ``PNG``）。若把 ``.apng`` 声称成独立
#: 格式 ``APNG``，则任何正常的 APNG 都会被误判成后缀错乱并被改名，那是缺陷。
SUFFIX_TO_FORMAT: dict[str, str] = {
    ".gif": "GIF",
    ".png": "PNG",
    ".apng": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
}

#: 真实格式 → 归一化时**应当使用**的后缀（真实格式的规范后缀）。
#:
#: ⚠️ **这不是平台偏好格式。** 平台偏好格式在
#: :data:`emoji_migrator.platforms.PLATFORM_SUFFIXES`，语义是「转成什么」；
#: 本表的语义是「这个真实格式正确叫什么」。后缀修正的目标只能是本表
#: （即 sniff 的结论），两者不得混用。
FORMAT_TO_SUFFIX: dict[str, str] = {
    "GIF": ".gif",
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
}

BYTES_PER_KIB: int = 1024
