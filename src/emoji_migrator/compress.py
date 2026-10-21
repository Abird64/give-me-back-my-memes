"""体积压缩。

职责：在满足平台体积上限的前提下，按 :class:`~emoji_migrator.config.QualityTier`
压缩体积。EM-001 只定义对外签名与结果类型，不实现压缩算法。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from emoji_migrator.config import QualityTier

__all__ = ["CompressResult", "compress"]


@dataclass(frozen=True, slots=True)
class CompressResult:
    """单文件压缩结果。"""

    source: Path
    target: Path
    tier: QualityTier
    before_bytes: int
    after_bytes: int

    @property
    def ratio(self) -> float:
        """压缩比 after/before，1.0 表示未变小。"""
        if self.before_bytes <= 0:
            return 1.0
        return self.after_bytes / self.before_bytes


def compress(source: Path, target: Path, tier: QualityTier) -> CompressResult:
    """压缩单个文件。

    EM-001 为占位实现。EM-002 需实现 GIF 帧差分/帧数上限/调色板量化等策略。
    """
    raise NotImplementedError("compress() 由 EM-002 实现")
