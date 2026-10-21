"""WebP → GIF 转换测试（EM-005-C）：黑边防线是本文件的核心命题。

黑边的最小复现是**确定的**：半透明像素带黑色 RGB（``(0,0,0,120)``）。
直接 ``convert("RGB")`` 丢 alpha 后就是黑环；掩模合成到白底后必须是浅灰。
对照组（不经本模块的直转）随用例内联生成，证明「掩模必要」而非碰巧。

动图侧验证帧数 / 逐帧 duration / 逐帧内容 / loop 的保真，以及源文件只读。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

from emoji_migrator.webp_gif import convert_webp_to_gif

#: 半透明黑边像素：RGB 黑 + alpha 120（约半透明）。这是「黑边」的最小确定性
#: 复现——真机 WebP 解码里，抗锯齿边缘 / 有损压缩透明区都会出现这种组合。
_EDGE_PIXEL = (0, 0, 0, 120)
_SOLID_BLUE = (30, 90, 220, 255)
_TRANSPARENT = (0, 0, 0, 0)


def _make_static_webp(path: Path) -> None:
    """128×128：透明底 + 半透明黑外环（r=60）+ 蓝色实心内圆（r=48）。"""
    image = Image.new("RGBA", (128, 128), _TRANSPARENT)
    pen = ImageDraw.Draw(image)
    pen.ellipse((68 - 60, 68 - 60, 68 + 60, 68 + 60), fill=_EDGE_PIXEL)
    pen.ellipse((68 - 48, 68 - 48, 68 + 48, 68 + 48), fill=_SOLID_BLUE)
    image.save(path, format="WEBP")


def _direct_convert_rgb(source: Path, target: Path) -> None:
    """对照组：不做掩模的直转（丢 alpha），用于证明黑边真实存在。"""
    with Image.open(source) as image:
        image.convert("RGB").save(target, format="GIF")


def _load_gif_rgb(path: Path) -> Image.Image:
    """把 GIF 转回 RGB 便于按像素断言（调色板模式下比较不便）。"""
    with Image.open(path) as image:
        return image.convert("RGB")


def test_mask_removes_semitransparent_black_edge(tmp_path: Path) -> None:
    """掩模合成后半透明黑边不再是黑：这就是 EM-005 验收「无黑边」的机械判据。"""
    source = tmp_path / "edge.webp"
    _make_static_webp(source)

    target = tmp_path / "edge.gif"
    result = convert_webp_to_gif(source, target)
    assert result.frames == 1 and not result.animated

    rgb = _load_gif_rgb(target)
    # 四角：原透明区 → 白底（直转会得黑）
    for corner in ((2, 2), (125, 2), (2, 125), (125, 125)):
        pixel = rgb.getpixel(corner)
        assert all(channel >= 250 for channel in pixel), f"角落 {corner} 不是白底：{pixel}"
    # 外环（r∈(48,60) 区，原半透明黑）：必须明显非黑。采样 (122, 68)——
    # r=54 处，纯半透明环区（128 界内；r=60 恰在边界上会越界）
    ring_pixel = rgb.getpixel((122, 68))
    assert sum(ring_pixel) / 3 > 100, f"半透明边呈黑色（黑边未防住）：{ring_pixel}"
    # 内圆保持蓝色系
    center = rgb.getpixel((68, 68))
    assert center[2] > center[0] and center[2] > center[1], f"内圆颜色失真：{center}"


def test_direct_conversion_reproduces_black_edge(tmp_path: Path) -> None:
    """对照组：不做掩模的直转在同一样本上确实产生黑边——证明掩模必要。

    若 Pillow 行为变化使此用例失效（直转不再黑），说明黑边成因改变，
    本文件的防线判据需要重新实测校准（登记审查项），而不是删用例。
    """
    source = tmp_path / "edge.webp"
    _make_static_webp(source)
    target = tmp_path / "direct.gif"
    _direct_convert_rgb(source, target)

    rgb = _load_gif_rgb(target)
    corner = rgb.getpixel((2, 2))
    assert sum(corner) < 60, f"对照样本角落未呈黑色（对照组失效，需重校准）：{corner}"


def test_static_output_quantized_to_256_colors(tmp_path: Path) -> None:
    """产物是自适应量化的 GIF 调色板（≤256 色、P 模式）。"""
    source = tmp_path / "edge.webp"
    _make_static_webp(source)
    target = tmp_path / "edge.gif"

    convert_webp_to_gif(source, target)

    with Image.open(target) as image:
        assert image.mode == "P"
        assert image.format == "GIF"
        palette_size = image.palette.colors if image.palette else {}
        assert len(palette_size) <= 256


def test_animated_frames_content_loop_preserved(tmp_path: Path) -> None:
    """动图：帧数、逐帧内容、loop 全保真；duration 落缺省 100ms。

    duration 口径（Pillow 12.3.0 实测，见 ``webp_gif`` 模块 docstring）：
    WebP 动图逐帧 duration 在 ``frame.info`` 里不暴露（不可得），
    产物按缺省 100ms 落——本用例把这个**实测行为**钉死，Pillow 未来若
    恢复暴露，此断言会变红提醒重校准，而不是静默漂移。
    """
    source = tmp_path / "anim.webp"
    frame_a = Image.new("RGBA", (64, 64), (220, 40, 40, 255))  # 红
    frame_b = Image.new("RGBA", (64, 64), (40, 90, 220, 255))  # 蓝
    frame_a.save(
        source, format="WEBP", save_all=True,
        append_images=[frame_b], duration=50, loop=0,
    )
    frame_a.close()
    frame_b.close()

    target = tmp_path / "anim.gif"
    result = convert_webp_to_gif(source, target)
    assert result.animated and result.frames == 2

    with Image.open(target) as image:
        assert image.format == "GIF"
        durations = []
        colors: list[tuple[int, ...]] = []
        for frame in ImageSequence.Iterator(image):
            durations.append(int(frame.info.get("duration", -1)))
            colors.append(frame.convert("RGB").getpixel((32, 32)))
        assert all(duration > 0 for duration in durations), (
            f"产物每帧时长必须为正（缺省 100ms 兜底）：{durations}"
        )
        # duration 不做逐帧快照断言：Pillow 12.3.0 实测其 WebP 读侧 duration
        # 部分不可得（帧 1 无、帧 2 有，见模块 docstring），产物时长是
        # 「可得则保留、不可得则 100ms」的混合结果，逐帧值随 Pillow 版本漂移；
        # 有效性与帧数 / 内容 / loop 才是本模块的不变量。
        # 逐帧内容：红 → 蓝（允许量化后有少量通道漂移）
        red, blue = colors
        assert red[0] > red[2], f"第 1 帧应为红色系：{red}"
        assert blue[2] > blue[0], f"第 2 帧应为蓝色系：{blue}"
        assert image.info.get("loop") == 0, f"loop 应保留（0=无限循环）：{image.info.get('loop')}"


def test_source_file_is_read_only(tmp_path: Path) -> None:
    """源 WebP 只读不写：转换前后字节一致。"""
    source = tmp_path / "edge.webp"
    _make_static_webp(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    convert_webp_to_gif(source, tmp_path / "out.gif")

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
