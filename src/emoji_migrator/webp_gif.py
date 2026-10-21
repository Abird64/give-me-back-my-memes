"""WebP → GIF 转换（EM-005-C）：alpha 掩模防黑边 + 自适应量化。

抖音收藏表情以动态 WebP 为主，QQ / 本地表情库的通用格式是 GIF（EM-002 规范化
的产出口径）。直接 ``convert("RGB")`` 会踩一个经典坑：**WebP 的半透明像素 RGB
通道往往是黑色**（解码器不做预乘），直接转 GIF（1-bit 透明，半透明像素被二值
切进透明或不透明之一）时，这些像素在深色 / 透明底上呈现为**黑色描边**——即
「黑边」。本模块的对策（EM-005 范围原文「alpha 通道掩模」）：

1. 每帧先转 ``RGBA``，以 alpha 为掩模**合成到不透明底色**（默认白）——
   半透明像素按透明度与底色混合，RGB 黑色伪影被底色冲掉；
2. 合成后的 ``RGB`` 再走自适应量化（256 色，等价旧常量 ``Image.ADAPTIVE``）——
   调色板从**实际画面内容**统计而来，比固定调色板保真得多；
3. 动图逐帧处理，保留 ``loop`` 与**输入可读时**的逐帧 ``duration``。

**实测限制（2026-10-20，Pillow 12.3.0）**：WebP 动图逐帧 ``duration`` 读取
**部分不可得**（编码为标量 ``duration=50`` 的样本：帧 1 的 ``frame.info``
无该键、帧 2 有——且 ``seek`` 循环与 ``ImageSequence`` 迭代表现不一致），
逐帧值随 Pillow 版本漂移。本模块语义定为「**可得则保留，不可得落缺省
100ms**」，并剥掉 quantize 链路继承的 ``info.duration``（实测它会与
save 参数混写，造成产物时长不可预期）。

产物 GIF 不含透明索引（全帧不透明）——防黑边是本模块的硬目标，宁可透明区
落底色也不引入 GIF 透明的二值锯齿。深色内容本身不是黑边，不受影响。

依赖方向（``AGENTS.md`` §3）：叶子模块，只 import 标准库与 Pillow，
不 import 包内其他模块；由 ``cli`` 向下调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence

__all__ = ["ConvertResult", "convert_webp_to_gif"]

#: 合成底色。白底是表情类内容的通用安全色：深色描边类表情在白底上保形，
#: 浅色内容也不损失。含 alpha 通道（RGBA 画布用）。
_DEFAULT_BACKGROUND = (255, 255, 255, 255)

#: 动图单帧 duration 的缺省值（毫秒）。个别 WebP 帧缺 duration 元数据时兜底；
#: 100ms ≈ 10fps，是表情动画的常见节奏。
_DEFAULT_FRAME_MS = 100


@dataclass(frozen=True)
class ConvertResult:
    """转换结果摘要：帧数与是否动图（供调用方记录 / 守门）。"""

    frames: int
    animated: bool


def _flatten(frame: Image.Image, background: tuple[int, int, int, int]) -> Image.Image:
    """单帧 alpha 掩模合成：RGBA → 与底色混合 → RGB（不透明）。

    ``alpha_composite`` 语义即「以 alpha 为掩模」：结果像素 =
    前景 × alpha + 底色 × (1 - alpha)。半透明像素（如 WebP 抗锯齿边缘）
    因此落在前景色与底色的中间色上，而不是前景 RGB 的原值（常为黑）。
    """
    rgba = frame.convert("RGBA")
    base = Image.new("RGBA", rgba.size, background)
    base.alpha_composite(rgba)
    return base.convert("RGB")


def _quantize(image: Image.Image) -> Image.Image:
    """RGB → 256 色自适应调色板（等价旧常量 ``Image.ADAPTIVE``）。

    Pillow 12.3 实测（2026-10-20）：``Image.Quantize`` 枚举只有
    MEDIANCUT / MAXCOVERAGE / FASTOCTREE / LIBIMAGEQUANT 四个成员，**没有
    ADAPTIVE**；而遗留常量 ``Image.ADAPTIVE`` 仍存在、值为 1，即
    ``Quantize.MAXCOVERAGE``。EM-005 任务文件里写的 ``Image.ADAPTIVE``
    自适应量化对应的就是它——这里显式用枚举（遗留常量随时可能被移除）。
    """
    return image.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE)


def convert_webp_to_gif(
    source: Path,
    target: Path,
    background: tuple[int, int, int, int] = _DEFAULT_BACKGROUND,
) -> ConvertResult:
    """把 WebP（静态或动图）转换为 GIF，写入 ``target``。

    源文件只读不写；``target`` 由调用方决定路径与文件名（冲突处理属
    ``normalize`` / ``dedupe`` 的职责，本模块不做）。动图保留帧数、逐帧
    duration 与循环次数；``disposal=2``（还原到背景）对全帧不透明的产物
    无实际影响，作为防御性参数保留。

    返回 :class:`ConvertResult`；解码失败由 Pillow 抛常规异常，本模块不吞。
    """
    with Image.open(source) as image:
        animated = bool(getattr(image, "is_animated", False))
        if not animated:
            flat = _flatten(image, background)
            _quantize(flat).save(target, format="GIF")
            return ConvertResult(frames=1, animated=False)

        flats: list[Image.Image] = []
        durations: list[int] = []
        for frame in ImageSequence.Iterator(image):
            durations.append(int(frame.info.get("duration", _DEFAULT_FRAME_MS)))
            flats.append(_flatten(frame, background))

        quantized = [_quantize(flat) for flat in flats]
        # 剥掉 quantize 从源帧继承的 info.duration：实测（Pillow 12.3.0）它会
        # 与 save 的 duration 参数混写（帧 2 被写成源值 50 而非参数 100），
        # 造成产物时长不可预期。统一由上面收集的 durations 控制写入。
        for q in quantized:
            q.info.pop("duration", None)
        loop = int(image.info.get("loop", 0))  # 0 = 无限循环（GIF 口径）
        quantized[0].save(
            target,
            format="GIF",
            save_all=True,
            append_images=quantized[1:],
            duration=durations,
            loop=loop,
            disposal=2,
            optimize=False,  # 优化会合并相同帧，破坏逐帧 duration 的可预期性
        )
        return ConvertResult(frames=len(quantized), animated=True)
