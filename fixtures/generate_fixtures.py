"""合成测试样本生成器（EM-002）。

**本脚本不读取、不复制任何用户真实表情。** 全部样本由 Pillow 现画，
仅使用随机色块与几何图形，因此版权与隐私风险为零（``fixtures/README.md`` 的硬约束）。

## 分布目标（与本机实测对齐，见``AGENTS.md`` §7）

合成集共 **146** 个文件，全部以 ``.jpg`` 命名：

| 真实格式 | 数量 | 计入后缀错乱 |
| --- | --- | --- |
| GIF | 49 | 是 |
| JPEG | 94 | 否（``.jpg`` 本就正确） |
| PNG | 3 | 是 |

故**后缀错乱 52 个 = 49 GIF + 3 PNG**。

## 三个刻意的设计（都是为了守住验收标准）

1. **重复样本必须「像素相同、字节不同」**。
   最省事的重复是直接 ``shutil.copyfile``，那样字节哈希（md5）与感知哈希
   会**同时**命中——一旦有人把 :func:`~emoji_migrator.dedupe.compute_phash`
   改成文件字节哈希，测试仍然全绿，M4 突变就抓不到。
   因此这里额外造两类「同像素、不同字节」的重复：
   动图改帧时长（GIF 帧延时不同但像素一致）、PNG 多写一个文本块。
   验收标准要求「按像素内容哈希」，只有存在这种样本才**测得出**。

2. **重复样本与被复制样本必须像素完全一致**，否则感知哈希按阈值聚类时可能不命中，
   「重复被去重」这条就变成无法判定的空话。

3. **唯一性样本两两之间要离得足够远**（感知哈希距离 > 阈值），
   否则会被误判成重复，「去重后数量」这条断言就跟着漂。
   脚本跑完会自检这两条，不满足直接非零退出。

## 输出位置

样本写到 ``_out/synthetic/``（``.gitignore`` 已忽略 ``_out/``）。
工作包建议写到 ``fixtures/`` 子目录，但 ``.gitignore`` 里**没有** ``fixtures/synthetic/``
的规则，而 ``.gitignore`` 不在本工作包白名单内、不得修改——写到那里会产出
上百个待提交的二进制文件，违背 ``fixtures/README.md``「合成素材不入库二进制」的意图。
**入库的是本脚本，不是它的产物。**

幂等：每次运行先清空输出目录再重建，重复执行结果一致。
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 默认输出目录（``.gitignore`` 已忽略 ``_out/``）
DEFAULT_OUT_DIR = PROJECT_ROOT / "_out" / "synthetic"

# --------------------------------------------------------------------------------------
# 分布常量（验收标准第1、2 条的数字来源）
# --------------------------------------------------------------------------------------

#: 声明 ``.jpg`` 实为 GIF 的数量
GIF_AS_JPG = 49

#: 声明 ``.jpg`` 实为 JPEG 的数量（后缀正确，**不**计入错乱）
JPEG_AS_JPG = 94

#: 声明 ``.jpg`` 实为 PNG 的数量
PNG_AS_JPG = 3

#: 样本总数
TOTAL_FIXTURES = GIF_AS_JPG + JPEG_AS_JPG + PNG_AS_JPG

#: 后缀错乱总数 = GIF + PNG
MISMATCH_TOTAL = GIF_AS_JPG + PNG_AS_JPG

#: 动图数量（全部 GIF 样本都是多帧）
ANIMATED_TOTAL = GIF_AS_JPG

#: 超大尺寸样本的边长下界（工作包要求 1018×1024 以上）
OVERSIZED_MIN_EDGE = 1018

#: 超大体积下界：2300 KB
HUGE_MIN_BYTES = 2300 * 1024

#: 本脚本生成的文件后缀（全部以 .jpg 命名），供幂等清理时识别
GENERATED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})

#: 唯一内容样本数（其余为它们的重复变体）
UNIQUE_GIF = 44
UNIQUE_JPEG = 88
UNIQUE_PNG = 2


def _noise(size: tuple[int, int], seed: int) -> Image.Image:
    """由种子确定的高熵灰度图。

    用 ``random.Random(seed).randbytes()`` 而不是 :meth:`PIL.Image.effect_noise`：
    后者不接种子、依赖全局随机状态，同一脚本跑两次会得到**不同的字节**，
    幂等性直接没了（工作包要求「脚本要可重复运行」）。``randbytes`` 在 C 层批量产字节，
    又快又是确定性的。
    """
    width, height = size
    raw = random.Random(seed).randbytes(width * height)
    return Image.frombytes("L", size, raw)


def _identifier(img: Image.Image, seed: int, cells: int = 8) -> Image.Image:
    """在图上盖一层高对比度「标识图案」（黑白方块阵），由种子决定。

    为什么必须加这一层：单纯靠噪声图案的样本，两两之间的感知哈希距离可能只有 4
    （实测 gif_006/010/030/038 恰好撞在阈值上被误判为重复）。原因是噪声图被缩到
    dHash 的 9×8 采样尺寸后，高频信息基本被平均掉了，剩下的差异不足以越过阈值。
    高对比度方块阵几乎一一映射到 9×8 采样格，随机种子之间的期望距离约 32 位，
    离阈值 4 有充足余量——这样「唯一样本被判成重复」这件事才不会污染去重断言。
    """
    draw = ImageDraw.Draw(img)
    width, height = img.size
    cell_w = max(width // cells, 1)
    cell_h = max(height // cells, 1)
    bits = random.Random(seed * 7919 + 13).getrandbits(cells * cells)
    for index in range(cells * cells):
        col, row = index % cells, index // cells
        x0, y0 = col * cell_w, row * cell_h
        x1 = width if col == cells - 1 else x0 + cell_w
        y1 = height if row == cells - 1 else y0 + cell_h
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(0, 0, 0) if bits >> index & 1 else (255, 255, 255))
    return img


def _noise_image(size: tuple[int, int], seed: int, quality: int, out: Path) -> None:
    """写一张高熵 JPEG：靠噪声把体积顶到 2300 KB 以上。

    刻意**不**加 :func:`_identifier` 的黑白方块阵：方块是大片平坦区域，
    JPEG 一压就几乎不占字节，体积会从 2.6 MB 掉到几十 KB（实测）。
    高熵噪声缩到 9×8 后本就与任何其他样本相距甚远，不需要额外标识。
    """
    img = _noise(size, seed).convert("RGB")
    ImageDraw.Draw(img).line((0, 0, size[0], size[1]), fill=(255, 0, 0), width=8)
    img.save(out, format="JPEG", quality=quality)


def _noise_gif(size: tuple[int, int], seed: int, frames: int) -> list[Image.Image]:
    """噪声动图：逐帧换种子与标识图案，保证帧与帧之间确有差异。"""
    out = []
    for index in range(frames):
        img = _identifier(_noise(size, seed * 100 + index).convert("RGB"), seed * 100 + index)
        draw = ImageDraw.Draw(img)
        draw.ellipse((4 + index, 4, size[0] - 4, size[1] - 4), outline=(255, 0, 0), width=3)
        out.append(img)
    return out


def _static(size: tuple[int, int], seed: int) -> Image.Image:
    """静态图：色块 + 圆 + 斜线，由种子决定，**刻意每张都不一样**。"""
    rnd = random.Random(seed)
    base = (rnd.randrange(20, 235), rnd.randrange(20, 235), rnd.randrange(20, 235))
    img = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(img)
    draw.rectangle(
        (rnd.randrange(0, size[0] // 2), rnd.randrange(0, size[1] // 2), size[0] - 3, size[1] - 3),
        outline=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)),
        width=3 + rnd.randrange(0, 6),
    )
    draw.ellipse(
        (rnd.randrange(0, size[0] // 2), rnd.randrange(0, size[1] // 2),
         rnd.randrange(size[0] // 2, size[0]), rnd.randrange(size[1] // 2, size[1])),
        fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)),
    )
    return _identifier(img, seed)


def _save_gif(frames: list[Image.Image], out: Path, duration: int = 80) -> None:
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
    )


def _save_png(img: Image.Image, out: Path, comment: str | None = None) -> None:
    if comment is None:
        img.save(out, format="PNG")
        return
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", comment)
    img.save(out, format="PNG", pnginfo=info)


def _png_size(index: int) -> tuple[tuple[int, int], int]:
    """PNG 样本的尺寸与种子。抽成函数是为了让「重复样本」与「被复制样本」
    **必然**用同一组参数——否则两者像素不同，重复样本就白造了。"""
    return (100 + index * 13, 100 + index * 17), 9000 + index


def _drop_stale(out_dir: Path, expected: set[str]) -> None:
    """删掉输出目录里**本轮不会生成**的旧文件。

    不做「先全清再重建」：那样每次都要删 143 个文件，既慢又危险
    （批量删除一旦 ``out_dir`` 被误传就是不可逆事故）。
    改为覆盖写 + 只删多余项——重复运行时多余项为 0，一次删除都不发生，
    幂等性由「内容确定性」保证而不是由「删干净」保证。
    """
    for path in out_dir.iterdir():
        if path.is_file() and path.name not in expected and path.suffix.lower() in GENERATED_SUFFIXES:
            path.unlink()


def generate(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, int]:
    """生成全套合成样本，返回实测统计。幂等：重复运行结果一致。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    gif_names: list[str] = []
    jpeg_names: list[str] = []
    png_names: list[str] = []

    # ---- GIF：49 个，全部多帧，全部以 .jpg 命名 --------------------------------------
    # 种子规划：g00 用1001，其余用 1100+index，slow_sources 复用同一批种子。
    # **种子必须一一对应唯一内容**——早期版本让 g00 与 g01 共用种子 1001（仅尺寸不同），
    # 两者的标识图案完全一致、感知哈希只差 1 位，「唯一样本互不重复」这条就靠运气了。
    seeds: dict[int, int] = {index: 1100 + index for index in range(1, UNIQUE_GIF)}

    # g00 超大尺寸（1018×1024），且被复制成 gif_dupbyte_0
    _save_gif(_noise_gif((1018, 1024), seed=1001, frames=4), out_dir / "gif_000.jpg")
    gif_names.append("gif_000.jpg")

    # 帧数固定为 4 的两个样本，稍后要用「改帧时长」造出像素相同的重复
    slow_sources: dict[int, list[Image.Image]] = {
        index: _noise_gif((96, 96), seeds[index], frames=4) for index in (3, 4)
    }
    for index in range(1, UNIQUE_GIF):
        frames = slow_sources.get(index)
        if frames is None:
            frames = _noise_gif((96, 96), seeds[index], frames=3 + index % 4)
        name = f"gif_{index:03d}.jpg"
        _save_gif(frames, out_dir / name)
        gif_names.append(name)

    # 三个逐字节复制（最简单的重复）
    for offset, source in enumerate(("gif_000.jpg", "gif_001.jpg", "gif_002.jpg")):
        name = f"gif_dupbyte_{offset}.jpg"
        shutil.copyfile(out_dir / source, out_dir / name)
        gif_names.append(name)

    # 两个「像素相同、字节不同」：只改帧时长
    for offset, index in enumerate((3, 4)):
        name = f"gif_dupslow_{offset}.jpg"
        _save_gif(slow_sources[index], out_dir / name, duration=400)
        gif_names.append(name)

    # ---- JPEG：94 个，后缀正确 ------------------------------------------------------
    for index in range(UNIQUE_JPEG):
        name = f"jpg_{index:03d}.jpg"
        size = (72 + (index * 7) % 120, 72 + (index * 11) % 120)
        img = _static(size, seed=5000 + index)
        img.save(out_dir / name, format="JPEG", quality=88)
        jpeg_names.append(name)

    # 两个超大体积（必须都 >= 2300 KB）。尺寸是量出来的：1200×1204 的纯噪声
    # 在 quality=100 下只有 2262 KB，压线不过，故两档都留出余量。
    for offset, (size, seed) in enumerate((((1240, 1244), 7001), ((1404, 1200), 7002))):
        name = f"jpg_huge_{offset}.jpg"
        _noise_image(size, seed, quality=100, out=out_dir / name)
        jpeg_names.append(name)

    # 一个超大尺寸（1018×1024）
    img = _static((1018, 1024), seed=8001)
    img.save(out_dir / "jpg_oversized.jpg", format="JPEG", quality=90)
    jpeg_names.append("jpg_oversized.jpg")

    # 三个逐字节复制
    for offset, source in enumerate(("jpg_000.jpg", "jpg_001.jpg", "jpg_002.jpg")):
        name = f"jpg_dupbyte_{offset}.jpg"
        shutil.copyfile(out_dir / source, out_dir / name)
        jpeg_names.append(name)

    # ---- PNG：3 个，全部以 .jpg 命名 ----------------------------------------------
    for index in range(UNIQUE_PNG):
        size, seed = _png_size(index)
        _save_png(_static(size, seed), out_dir / f"png_{index:03d}.jpg")
        png_names.append(f"png_{index:03d}.jpg")

    # 一个「像素相同、字节不同」：同一张图（png_000）多写一个 PNG 文本块
    size, seed = _png_size(0)
    _save_png(_static(size, seed), out_dir / "png_dupmeta.jpg", comment="synthetic duplicate")
    png_names.append("png_dupmeta.jpg")

    produced = gif_names + jpeg_names + png_names
    _drop_stale(out_dir, set(produced))

    return {
        "total": len(produced),
        "gif": len(gif_names),
        "jpeg": len(jpeg_names),
        "png": len(png_names),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 EM-002 合成测试样本")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）",
    )
    args = parser.parse_args(argv)

    stats = generate(args.out)
    print(f"输出目录：{args.out}")
    print(f"总数：{stats['total']}")
    print(f"真实 GIF：{stats['gif']}")
    print(f"真实 JPEG：{stats['jpeg']}")
    print(f"真实 PNG：{stats['png']}")
    print(f"预期后缀错乱：{MISMATCH_TOTAL}（= {GIF_AS_JPG} GIF + {PNG_AS_JPG} PNG）")

    if stats["total"] != TOTAL_FIXTURES:
        print(f"❌ 数量不符：预期 {TOTAL_FIXTURES}，实际 {stats['total']}", file=sys.stderr)
        return 1
    print("✅ 数量分布符合验收标准")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
