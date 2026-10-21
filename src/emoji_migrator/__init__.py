"""emoji-migrator：跨平台表情格式嗅探、修正、去重与压缩。

模块清单与命名在 EM-001 定死，后续工作包不得改名：

- :mod:`emoji_migrator.config`：全局常量（路径、后缀与格式映射）
- :mod:`emoji_migrator.platforms`：目标平台枚举与各平台**目标格式**
- :mod:`emoji_migrator.sniff`：文件真实格式嗅探（不信后缀）
- :mod:`emoji_migrator.normalize`：后缀修正
- :mod:`emoji_migrator.dedupe`：感知哈希去重（动图按像素内容，非文件字节）
- :mod:`emoji_migrator.compress`：体积压缩（2026-10-13裁决后**本轮不实现**，留骨架）
- :mod:`emoji_migrator.guard`：合规阈值与质量守门规则常量
- :mod:`emoji_migrator.cli`：命令行入口

依赖方向为 ``cli -> {sniff, normalize, dedupe, compress} -> config/platforms/guard``，
本模块不引入任何子模块，以避免导入期环。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
