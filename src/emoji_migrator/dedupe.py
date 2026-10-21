"""感知哈希去重。

职责：对修正后的文件计算感知哈希（pHash/dHash），在阈值内聚类，只保留最优代表。

## 为什么必须按像素内容，而不是文件字节

验收标准明文要求「动图按**像素内容**哈希，非文件字节哈希」。这不是风格偏好，
而是因为**同一份表情可以有不同的字节**：

- 同一个动图，帧时长不同 ⇒ 字节不同，像素完全相同；
- 同一张图，PNG 里多写一个文本块 ⇒ 字节不同，像素完全相同；
- 同一个动图换一套调色板 ⇒ 字节不同，肉眼无法分辨。

按字节哈希（md5/sha256）会把上面三种**判成不重复**，于是同一张表情在整理结果里
出现多份——去重失效，且失效方式是**静默**的（不报错，只是没去掉）。
按像素内容哈希才能真正识别「看起来是同一个」。

## 算法

用 dHash（差分哈希）：灰度化后缩到 ``9x8``，逐行比较相邻像素的大小，
得64 位整数。选dHash 而非 DCT-pHash 的原因有二：

- 本模块 docstring 原本就写的是「pHash/dHash」，dHash 在其列；
- DCT 需在32×32 上做纯Python 二维变换，约 10^6 次乘加/文件，
  146 个样本要跑 2 分钟以上；dHash 只需 72 个像素，**毫秒级**。

多帧文件不只看首帧：把**所有帧**拼成一张贴图再缩放求哈希，
因此「首帧相同但后续动作不同」的两个动图不会被误判为重复。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

__all__ = [
    "DedupGroup",
    "DedupReport",
    "DEFAULT_PHASH_DISTANCE",
    "compute_phash",
    "dedupe",
    "hamming_distance",
    "PHASH_BITS",
]

#: pHash 汉明距离阈值：<= 该值视为重复
DEFAULT_PHASH_DISTANCE: int = 4

#: 哈希位宽（dHash：8 行 × 8 位 = 64 位）
PHASH_BITS: int = 64

#: 每帧缩放到的尺寸（拼贴的单元格大小）
_FRAME_SIZE: tuple[int, int] = (32, 32)

#: dHash 采样尺寸（宽 9 / 高 8，相邻像素两两比较得 8×8=64 位）
_SAMPLE_SIZE: tuple[int, int] = (9, 8)


@dataclass(frozen=True, slots=True)
class DedupGroup:
    """一组重复项，第一个成员为代表。"""

    keeper: Path
    duplicates: tuple[Path, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class DedupReport:
    """去重结果汇总。

    ``total`` 是输入总数（含被跳过的），**EM-002 追加**：:attr:`groups` 只收
    「真的有重复」的组（成员数 > 1），单独存在的文件不构成组，因此去重后数量
    无法从 ``groups`` 反推——早期版本用 ``len(groups)`` 当去重后数量，
    146 个文件里 9 个有重复就被报成「去重后 9 个」，实际是 137 个，少报了 128 个。
    """

    groups: tuple[DedupGroup, ...] = field(default=())
    skipped: tuple[Path, ...] = field(default=())
    total: int = 0

    @property
    def removed_count(self) -> int:
        return sum(len(g.duplicates) for g in self.groups)

    @property
    def unique_count(self) -> int:
        """去重后剩余文件数。

        ``total - removed``：被 :attr:`skipped` 跳过的文件从未进入任何组，
        自然仍算在结果里——它们没被删，不能算作「少了」。
        """
        return self.total - self.removed_count


def hamming_distance(left: int, right: int) -> int:
    """两个哈希的汉明距离。"""
    return (left ^ right).bit_count()


def _content_gray(path: Path) -> Image.Image:
    """把文件的**全部帧**拼成一张灰度贴图。

    多帧一起参与，是因为「首帧相同、动作不同」的两个动图必须被判为不重复。
    网格按帧数自适应（近正方形），保证同样内容得到同样的几何形状。
    """
    with Image.open(path) as img:
        frames = [
            img.convert("L").copy().resize(_FRAME_SIZE, Image.Resampling.LANCZOS)
            for img in _iter_frames(img)
        ]

    if not frames:
        raise ValueError(f"图片不含任何帧：{path}")

    count = len(frames)
    cols = _ceil_sqrt(count)
    rows = -(-count // cols)
    cell_w, cell_h = _FRAME_SIZE
    montage = Image.new("L", (cols * cell_w, rows * cell_h))
    for index, frame in enumerate(frames):
        montage.paste(frame, ((index % cols) * cell_w, (index // cols) * cell_h))
    return montage


def _iter_frames(img: Image.Image) -> Iterable[Image.Image]:
    """逐帧产出，兼容静态图（单帧）与多帧图。"""
    total = getattr(img, "n_frames", 1)
    for index in range(max(total, 1)):
        img.seek(index)
        yield img


def _ceil_sqrt(value: int) -> int:
    """不引入 math 也能算的平方根上取整（拼贴网格只需它，不必上取到完美平方）。"""
    column = 1
    while column * column < value:
        column += 1
    return column


def compute_phash(path: Path) -> int:
    """计算单文件的 pHash（dHash，64 位）。

    哈希输入是**解码后的像素内容**（全部帧），与文件字节无关。
    同一张图的两种不同编码会得到相同的哈希——这正是要的行为。

    :raises UnidentifiedImageError: 不是可识别的图片。
    :raises ValueError: 图片可识别但不含任何帧（零帧、截断）。
    """
    montage = _content_gray(path)
    sample = montage.resize(_SAMPLE_SIZE, Image.Resampling.LANCZOS)
    pixels = sample.load()

    bits = 0
    for y in range(_SAMPLE_SIZE[1]):
        for x in range(_SAMPLE_SIZE[0] - 1):
            bits = (bits << 1) | int(pixels[x, y] > pixels[x + 1, y])
    return bits


def _try_phash(path: Path) -> int | None:
    """算不出哈希时返回 ``None``（非图片、零帧、损坏），不抛异常。

    兜住 :class:`~PIL.Image.DecompressionBombError`：坏文件头部可能宣称
    「宽 3 万 × 高 2 万」，Pillow 会当作解压炸弹抛错。那是正当防护，
    但对「只算哈希」来说答案应是「这个文件算不了」，而非中断整批去重。
    """
    try:
        return compute_phash(path)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        IndexError,
        Image.DecompressionBombError,
    ):
        return None


def _pixels_of(path: Path) -> int:
    """首帧像素面积，用于同体积时的质量比较；读不出时返回 0。"""
    try:
        with Image.open(path) as img:
            return img.size[0] * img.size[1]
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        return 0


def _keeper_rank(path: Path) -> tuple[int, int, str]:
    """代表选择排序键：**体积优先、再质量、最后按名**。

    体积小优先是「整理」语义下最实用的偏好；同体积再取分辨率大的；
    最后的文件名项保证排序**全序且确定**，否则同键文件的结果会随目录遍历顺序漂移。
    """
    return (path.stat().st_size, -_pixels_of(path), path.name)


def dedupe(paths: Iterable[Path], max_distance: int = DEFAULT_PHASH_DISTANCE) -> DedupReport:
    """对文件集合聚类去重。

    聚类用**并查集**做传递闭包：A≈B、B≈C 即三者同组。贪心单遍比较会因输入顺序
    不同给出不同分组（「跑绿也不等于行为确定」），而顺序会随文件系统而变。

    算不出感知哈希的文件（非图片、零帧）**不参与聚类**，单列在
    :attr:`DedupReport.skipped`：它们无法用像素内容判定重复，
    混进聚类只会造成误删。
    """
    paths_seq: Sequence[Path] = list(paths)

    hashes: list[int] = []
    hashed: list[Path] = []
    skipped: list[Path] = []
    for path in paths_seq:
        value = _try_phash(path)
        if value is None:
            skipped.append(path)
            continue
        hashes.append(value)
        hashed.append(path)

    parent = list(range(len(hashed)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            if hamming_distance(hashes[i], hashes[j]) <= max_distance:
                parent[find(i)] = find(j)

    buckets: dict[int, list[Path]] = {}
    for index, path in enumerate(hashed):
        buckets.setdefault(find(index), []).append(path)

    groups = tuple(
        DedupGroup(keeper=members[0], duplicates=tuple(members[1:]))
        for members in (
            sorted(group_paths, key=_keeper_rank)
            for group_paths in buckets.values()
            if len(group_paths) > 1
        )
    )
    groups = tuple(sorted(groups, key=lambda g: str(g.keeper)))

    return DedupReport(groups=groups, skipped=tuple(skipped), total=len(paths_seq))
