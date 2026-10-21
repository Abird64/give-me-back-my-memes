"""平台枚举与各平台**目标格式**。

本模块只声明两类东西：**平台标识**（:class:`Platform`）与**目标格式**
（:data:`PLATFORM_SUFFIXES`），**不含任何阈值**。

为什么没有体积/像素阈值：档位数字曾以「平台体积/尺寸上限」的形式存在于本模块，
但 2026-10-13 的实测复核（见 ``docs/05-表情档位限制的必要性实测复核.md``）
推翻了其前提——本产品**不做压缩**，而阈值唯一的用途是「压到多大」；
不压就没有阈值可谈。实测要点：

- QQ 在自己的 ``personal_emoji/Ori`` 目录里原样保留了**单个达 16.6 MB 的表情**，
  即先前那套体积上限**从来不是平台的真实限制**；
- 本机 46 个多帧 GIF 在 QQ 自动生成的 ``Thumb`` 目录里**全被压成静态单帧**，
  即平台自动压缩会把动图压死——那恰恰是本产品**不该**做的事。

因此数字被删除而非保留为「推荐值」。若未来要恢复，必须先推翻那份实测报告。

（档位枚举 ``QualityTier`` 与压缩档像素表 ``config.TIER_MAX_EDGE`` 属压缩域，
按 D-12 裁决不在本模块，见 ``config.py``。）
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Platform", "PLATFORM_SUFFIXES"]


class Platform(StrEnum):
    """平台标识（**目标端** / 输入侧兜底）。

    四个成员都必须保留：``WECHAT`` / ``QQ`` / ``DOUYIN`` 是三端目标，
    ``SOURCE`` 表示原始/未知来源平台，是**输入侧**的兜底标识（不是真实平台）。

    ⚠️ ``DOUYIN`` 不可删：EM-005 的抖音采集需要用它标识来源端。
    """

    WECHAT = "wechat"
    QQ = "qq"
    DOUYIN = "douyin"
    SOURCE = "source"


#: 各平台的**目标格式**（EM-002 归一化时产出的常规封装后缀）。
#:
#: ⚠️ **这是目标格式，不是压缩阈值。** 本表的键是「转成什么格式」，
#: 与「压到多大字节/多少像素」无关——本模块已无任何阈值可配。
#: 读取时不要把它当作体积或尺寸上限的来源。
#:
#: - 微信 ``.gif``：EM-001 沿用值，需求基线 C-7 未限定微信的导出格式。
#: - QQ ``.png``、source ``.png``：同上，C-7 未规定。
#: - 抖音 ``.webp``：C-7 只规定「WebP 优先」，未给其他候选，故取此为首选。
PLATFORM_SUFFIXES: dict[Platform, str] = {
    Platform.WECHAT: ".gif",
    Platform.QQ: ".png",
    Platform.DOUYIN: ".webp",
    Platform.SOURCE: ".png",
}
