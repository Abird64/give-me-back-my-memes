"""合规守门阈值。

职责：集中放置「可公开发布」的硬性闸门，供 CLI 与测试共同引用。

说明：本项目为**纯离线、只读**工具。需求基线 §3 非目标 1、2 已明确禁止
进程内存写入、远程线程注入、Hook 注入等手法，因此被禁 API 的**权威清单放在
``tests/test_compliance.py``**，由静态扫描断言其在本包源码中零出现。

本模块只携带阈值与策略元数据，**刻意不复述被禁 API 的具体标识符**——
否则守门常量本身就会触发它自己守的那道闸门。

只读扫描 API 在未来包内允许出现（EM-003 只读扫描需要），但必须带注释说明用途。

本模块同时承载**质量守门的两条正确性判定**（2026-10-13 范围简化后由 5 条收缩为 2 条）：
体积/尺寸判定与透明度黑边检查**已作废**——它们依赖压缩，而压缩已砍（工作包「不包含」节）。

**为什么这两条是硬门槛、其余是提示**：判据是「失效后果」。
这两条一旦失效就是**静默失败**——文件看着正常、程序不报错，但导进微信后动图成了静态图；
而体积/尺寸超标的唯一后果只是文件偏大，用户看得见。
"""

from __future__ import annotations

__all__ = [
    "COMPLIANCE_POLICY_ID",
    "FORBIDDEN_API_COUNT",
    "MAX_SNIPPED_HEADER_BYTES",
    "MAX_INLINE_HTML_BYTES",
    "ALLOWED_READONLY_APIS",
    "COMPLIANCE_SOURCE_OF_TRUTH",
    "RULE_SUFFIX_MATCHES_FORMAT",
    "RULE_ANIMATION_SURVIVED",
    "CORRECTNESS_RULES",
    "MIN_ANIMATION_FRAMES",
    "ANIMATION_LOSS_WARNING",
]

#: 合规策略标识，随报错信息一起输出，便于追溯到需求基线条目
COMPLIANCE_POLICY_ID: str = "BASELINE-S3-NONGOAL-1,2"

#: 权威清单所在位置（被禁 API 具体标识符只在该文件中出现）
COMPLIANCE_SOURCE_OF_TRUTH: str = "tests/test_compliance.py"

#: 被禁 API 的条目数；与权威清单长度做一致性断言，防止清单被悄悄删项
FORBIDDEN_API_COUNT: int = 9

#: 允许出现的只读 API（EM-003 只读扫描需要），使用时必须注释说明用途
ALLOWED_READONLY_APIS: tuple[str, ...] = (
    "ReadProcessMemory",
    "OpenProcess",
)

#: 嗅探时允许读入内存的头部字节上限
MAX_SNIPPED_HEADER_BYTES: int = 64

#: 内联预览所需的 HTML 上限，超出则拒绝内联
MAX_INLINE_HTML_BYTES: int = 5 * 1024 * 1024

# --------------------------------------------------------------------------------------
# 质量守门（正确性判定）
# --------------------------------------------------------------------------------------

#: 规则一：文件后缀声称的格式必须与嗅探出的真实格式一致。
#:
#: 「声称格式」就是后缀映射出的格式，因此本规则等价于「后缀已被修正为真实格式」；
#: 不一致意味着导入端可能按错误格式解析。
RULE_SUFFIX_MATCHES_FORMAT: str = "suffix-matches-format"

#: 规则二：动图的帧数必须 > 1，即**它仍然是动图**。
#:
#: 这是整套工具里最重要的一条断言。平台（微信）的自动压缩会把动图压成静态图
#: （本机实测 46 个 GIF → Thumb 目录 0 个动图，见 ``docs/05-…实测复核.md``），
#: 而「表情不会动了」这件事如果不主动检查就是**静默失败**。
RULE_ANIMATION_SURVIVED: str = "animation-survived"

#: 现行正确性规则的全集。体积/尺寸/透明度判定已随压缩下线而作废，不在此列。
CORRECTNESS_RULES: tuple[str, ...] = (
    RULE_SUFFIX_MATCHES_FORMAT,
    RULE_ANIMATION_SURVIVED,
)

#: 动图存活所需的最小帧数。**必须严格大于 1**：``n_frames >= 1`` 对任何可解码图片都成立，
#: 等于没有断言（该写法是本包突变验证 M2 的对象）。
MIN_ANIMATION_FRAMES: int = 2

#: 动图退化为静态图时的中文告警文案。
ANIMATION_LOSS_WARNING: str = "动图已被压成静态图（本应多帧）"
