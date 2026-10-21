"""``dedupe`` 模块单元测试：感知哈希去重（正常路径 + 边界）。

包含 EM-010 独立审核转来的 **X4**：补强去重的边界覆盖
（空目录、重叠文件、非图片）。去重的**分组**层面的全量断言在 :mod:`test_end_to_end`。

本文件最要紧的一组是 :func:`test_duplicates_are_found_even_when_bytes_differ`：
它守的是验收标准明文「动图按**像素内容**哈希，非文件字节哈希」。
若样本只有逐字节相同的重复，这条断言与「改成md5(file bytes)」的实现无法区分。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, PngImagePlugin

from emoji_migrator.dedupe import (
    DEFAULT_PHASH_DISTANCE,
    PHASH_BITS,
    compute_phash,
    dedupe,
    hamming_distance,
)


def _static(seed: int, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """高对比度方块阵：保证不同 seed 的感知哈希距离足够远。"""
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    bits = seed * 2654435761 & 0xFFFFFFFF
    cell = max(size[0] // 8, 1)
    for index in range(64):
        col, row = index % 8, index // 8
        colour = (0, 0, 0) if (bits >> index) & 1 else (255, 255, 255)
        draw.rectangle(
            (col * cell, row * cell, (col + 1) * cell - 1, (row + 1) * cell - 1), fill=colour
        )
    return img


def _anim(seed: int, frames: int = 4, size: tuple[int, int] = (64, 64)) -> list[Image.Image]:
    return [_static(seed * 10 + index, size) for index in range(frames)]


def _write_gif(path: Path, seed: int, frames: int = 4, duration: int = 80) -> Path:
    imgs = _anim(seed, frames)
    imgs[0].save(
        path, format="GIF", save_all=True, append_images=imgs[1:], duration=duration, loop=0
    )
    return path


def _write_png(path: Path, seed: int, comment: str | None = None) -> Path:
    img = _static(seed)
    if comment is None:
        img.save(path, format="PNG")
        return path
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", comment)
    img.save(path, format="PNG", pnginfo=info)
    return path


def _write_jpeg(path: Path, seed: int) -> Path:
    _static(seed).save(path, format="JPEG", quality=92)
    return path


# --------------------------------------------------------------------------------------
# 感知哈希本身
# --------------------------------------------------------------------------------------


def test_phash_is_64_bits() -> None:
    assert PHASH_BITS == 64


def test_same_image_has_identical_phash(tmp_path: Path) -> None:
    first = _write_jpeg(tmp_path / "a.jpg", 3)
    second = _write_jpeg(tmp_path / "b.jpg", 3)
    assert compute_phash(first) == compute_phash(second)


def test_different_images_have_far_apart_phash(tmp_path: Path) -> None:
    first = _write_jpeg(tmp_path / "a.jpg", 3)
    second = _write_jpeg(tmp_path / "b.jpg", 91)
    assert hamming_distance(compute_phash(first), compute_phash(second)) > DEFAULT_PHASH_DISTANCE


def test_phash_is_reproducible(tmp_path: Path) -> None:
    path = _write_jpeg(tmp_path / "a.jpg", 5)
    assert compute_phash(path) == compute_phash(path)


def test_phash_does_not_fit_in_fewer_than_64_bits(tmp_path: Path) -> None:
    """哈希必须真的用满 64 位——否则阈值 4 相对位数过小，会把一堆东西判成重复。"""
    values = [compute_phash(_write_jpeg(tmp_path / f"{i}.jpg", i)) for i in range(8)]
    assert max(values).bit_length() > 32


def test_hamming_distance_basics() -> None:
    assert hamming_distance(0, 0) == 0
    assert hamming_distance(0b1011, 0) == 3
    assert hamming_distance(0b1011, 0b1011) == 0
    assert hamming_distance(0xFFFFFFFFFFFFFFFF, 0) == 64


# --------------------------------------------------------------------------------------
# 核心：按像素内容而非字节
# --------------------------------------------------------------------------------------


def test_duplicates_are_found_even_when_bytes_differ(tmp_path: Path) -> None:
    """同像素、不同字节的动图必须被判为重复。

    这是「按像素内容哈希、非文件字节哈希」的可判定形式。若实现退化成
    ``md5(path.read_bytes())``，这两个文件会各算各的，本测试立即变红。
    """
    original = _write_gif(tmp_path / "fast.gif", seed=7, duration=80)
    slower = _write_gif(tmp_path / "slow.gif", seed=7, duration=800)

    assert original.read_bytes() != slower.read_bytes(), "前提失效：两个样本字节必须不同"
    assert compute_phash(original) == compute_phash(slower)

    report = dedupe([original, slower])
    assert report.removed_count == 1
    assert report.unique_count == 1


def test_duplicates_are_found_when_png_carries_extra_metadata(tmp_path: Path) -> None:
    """PNG 多写一个文本块 ⇒ 字节不同、像素相同 ⇒ 仍是重复。"""
    plain = _write_png(tmp_path / "plain.png", seed=11)
    tagged = _write_png(tmp_path / "tagged.png", seed=11, comment="hello")

    assert plain.read_bytes() != tagged.read_bytes()
    report = dedupe([plain, tagged])
    assert report.removed_count == 1


def test_byte_identical_copies_are_duplicates(tmp_path: Path) -> None:
    """最朴素的那种重复：逐字节相同。"""
    import shutil

    original = _write_jpeg(tmp_path / "a.jpg", seed=13)
    clone = tmp_path / "copy.jpg"
    shutil.copyfile(original, clone)

    assert compute_phash(original) == compute_phash(clone)
    assert dedupe([original, clone]).removed_count == 1


def test_same_first_frame_but_different_motion_is_not_duplicate(tmp_path: Path) -> None:
    """首帧相同、后续帧不同 ⇒ 必须**不**判为重复。

    这条守护「多帧一起参与哈希」的实现。只取首帧的实现会在这里误判，
    把两个动作不同的表情合并成一个。
    """
    shared_first = _anim(100, frames=4)[0]
    tail_a = _anim(200, frames=3)
    tail_b = _anim(201, frames=3)

    path_a = tmp_path / "a.gif"
    path_b = tmp_path / "b.gif"
    for path, tail in ((path_a, tail_a), (path_b, tail_b)):
        shared_first.save(
            path, format="GIF", save_all=True, append_images=tail, duration=80, loop=0
        )

    assert hamming_distance(compute_phash(path_a), compute_phash(path_b)) > DEFAULT_PHASH_DISTANCE
    assert dedupe([path_a, path_b]).removed_count == 0


# --------------------------------------------------------------------------------------
# 去重行为
# --------------------------------------------------------------------------------------


def test_all_distinct_files_are_all_kept(tmp_path: Path) -> None:
    paths = [_write_jpeg(tmp_path / f"{i}.jpg", seed=100 + i * 7) for i in range(6)]
    report = dedupe(paths)
    assert report.removed_count == 0
    assert report.unique_count == 6
    assert report.groups == ()


def test_keeper_is_the_smallest_file(tmp_path: Path) -> None:
    """代表选择：体积优先。"""
    paths = [_write_jpeg(tmp_path / f"{i}.jpg", seed=55) for i in range(3)]
    # 再加一个同内容但更小的（用更低质量重存）
    paths.append(tmp_path / "small.jpg")
    _static(55).save(paths[-1], format="JPEG", quality=20)

    report = dedupe(paths)
    assert len(report.groups) == 1
    assert report.groups[0].keeper.name == "small.jpg"


def test_all_duplicates_land_in_exactly_one_group(tmp_path: Path) -> None:
    paths = [
        _write_gif(tmp_path / "a.gif", seed=77, duration=80),
        _write_gif(tmp_path / "b.gif", seed=77, duration=400),
        _write_gif(tmp_path / "c.gif", seed=77, duration=900),
    ]
    report = dedupe(paths)
    assert len(report.groups) == 1
    assert len(report.groups[0].duplicates) == 2
    assert report.unique_count == 1


def test_clustering_is_transitive(tmp_path: Path) -> None:
    """A≈B、B≈C ⇒ 三者同组（并查集的传递闭包）。"""
    paths = [
        _write_gif(tmp_path / "a.gif", seed=88, duration=80),
        _write_gif(tmp_path / "b.gif", seed=88, duration=500),
        _write_gif(tmp_path / "c.gif", seed=88, duration=1500),
        _write_jpeg(tmp_path / "other.jpg", seed=999),
    ]
    report = dedupe(paths)
    assert len(report.groups) == 1
    assert report.groups[0].keeper.name == "a.gif"
    assert report.unique_count == 2


def test_max_distance_controls_aggressiveness(tmp_path: Path) -> None:
    """阈值调大 ⇒ 判为重复的更多（本测试用「距离刚好在阈值上」的样本）。"""
    a = _write_jpeg(tmp_path / "a.jpg", seed=31)
    b = _write_jpeg(tmp_path / "b.jpg", seed=32)
    distance = hamming_distance(compute_phash(a), compute_phash(b))
    assert distance > 0

    assert dedupe([a, b], max_distance=0).removed_count == 0
    assert dedupe([a, b], max_distance=distance).removed_count == 1


def test_default_threshold_value_is_pinned() -> None:
    """阈值本身必须被钉死为 4。

    这条是M3 突变（把阈值改成 0 或 999）的**主要防线**。实测：
    早期版本只靠「构造一对完全相同的图」来测去重，把阈值改成 0 后
    那些用例**依然全绿**——因为完全相同的图哈希距离就是 0，
    无论阈值取 0 还是 4 都会命中。换句话说，**默认值当时零覆盖**。
    """
    assert DEFAULT_PHASH_DISTANCE == 4


def test_threshold_boundary_decides_grouping(tmp_path: Path) -> None:
    """阈值上下各差一位 ⇒ 分组结果必须**相反**。

    这条直接杀掉「阈值被改成永不命中」或「改成永远命中」的退化：
    样本距离已知为 ``d``，那么阈值取 ``d - 1`` 必须**不**判重复，
    取 ``d`` 必须判重复。任何把阈值整体平移的实现都过不去。
    """
    a = _write_jpeg(tmp_path / "a.jpg", seed=301)
    b = _write_jpeg(tmp_path / "b.jpg", seed=302)
    distance = hamming_distance(compute_phash(a), compute_phash(b))
    assert distance >= 1, "前提失效：需要一对非零距离的样本"

    assert dedupe([a, b], max_distance=distance - 1).removed_count == 0
    assert dedupe([a, b], max_distance=distance).removed_count == 1


def test_threshold_change_alters_the_real_run(tmp_path: Path) -> None:
    """把阈值调大 ⇒ 判为重复的**变多**（不是恒等变化）。

    用一串互不相同的样本，在多个阈值下统计命中数，
    断言它随阈值**单调不减**且确实在动。
    """
    paths = [_write_jpeg(tmp_path / f"{i}.jpg", seed=400 + i * 13) for i in range(8)]

    def removed_at(threshold: int) -> int:
        return dedupe(paths, max_distance=threshold).removed_count

    series = [removed_at(t) for t in range(0, 33, 4)]
    assert series == sorted(series), f"命中数随阈值下降而减少，说明阈值未生效：{series}"
    assert series[-1] > series[0], f"阈值完全没起作用：{series}"


def test_default_threshold_used_when_argument_omitted(tmp_path: Path) -> None:
    """省略 ``max_distance`` 时必须真的用默认阈值 4，而不是某个硬编码值。

    样本构造方式：**同一张图**分别以 quality=95 与 70 存成 JPEG。
    像素差异极小，实测感知哈希距离为 1——正好能区分「阈值 1 判不重复」
    与「默认阈值 4 判重复」。

    之所以用质量差异而不是另画两张图：另画的两张图距离动辄几十位，
    任何阈值都判重复，**区分不出阈值**（这正是 M3 早期零覆盖的成因）。

    距离按实测写死为 1，并同时在断言里复核——若将来换了 Pillow 版本
    导致距离变成 0或 2，这里会明确失败，而不是悄悄改用一个仍能通过的阈值。
    """
    base = _static(701)
    high = tmp_path / "high.jpg"
    low = tmp_path / "low.jpg"
    base.save(high, format="JPEG", quality=95)
    base.save(low, format="JPEG", quality=70)

    distance = hamming_distance(compute_phash(high), compute_phash(low))
    assert distance == 1, f"实测距离变为 {distance}，样本构造需重新校准"

    assert dedupe([high, low]).removed_count == 1, "默认阈值 4 未生效"
    assert dedupe([high, low], max_distance=distance - 1).removed_count == 0, (
        "阈值降到距离以下仍判重复，说明距离计算与分组判定不同源"
    )


def test_max_distance_zero_still_finds_exact_duplicates(tmp_path: Path) -> None:
    """阈值 0 只认完全相同的哈希——但同像素样本仍应命中。"""
    a = _write_gif(tmp_path / "a.gif", seed=42, duration=80)
    b = _write_gif(tmp_path / "b.gif", seed=42, duration=700)
    assert dedupe([a, b], max_distance=0).removed_count == 1


def test_empty_input_yields_empty_report() -> None:
    """X4：空目录不是崩溃，是空结果。"""
    report = dedupe([])
    assert report.groups == ()
    assert report.skipped == ()
    assert report.removed_count == 0
    assert report.unique_count == 0


def test_single_file_is_not_a_group(tmp_path: Path) -> None:
    path = _write_jpeg(tmp_path / "only.jpg", seed=9)
    report = dedupe([path])
    assert report.groups == ()
    assert report.unique_count == 1


# --------------------------------------------------------------------------------------
# X4：非图片与损坏文件的边界
# --------------------------------------------------------------------------------------


def test_non_image_file_is_skipped_not_hashed(tmp_path: Path) -> None:
    """X4：非图片进不了聚类，只登记到 skipped。"""
    path = tmp_path / "notes.txt"
    path.write_text("不是图片", encoding="utf-8")
    report = dedupe([path])
    assert report.groups == ()
    assert report.skipped == (path,)
    assert report.unique_count == 1


def test_corrupt_image_is_skipped_not_fatal(tmp_path: Path) -> None:
    """X4：损坏 / 零帧文件同样走 skipped，不能让整批去重崩掉。"""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"GIF89a" + b"\x00" * 8)
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")

    good = _write_jpeg(tmp_path / "good.jpg", seed=3)
    report = dedupe([broken, empty, good])

    assert set(report.skipped) == {broken, empty}
    assert report.removed_count == 0
    # 被跳过的文件**从未被删除**，所以仍算在结果里：3 个输入 - 0 个移除 = 3。
    # 早期版本在这里期望 1，是把「跳过了」错当成「删掉了」——那会让调用方
    # 以为两个坏文件被清理掉了。
    assert report.unique_count == 3


def test_non_image_does_not_collide_with_an_image(tmp_path: Path) -> None:
    """非图片绝不能与图片被判为同一组（那会「删掉」一个正常表情）。"""
    text = tmp_path / "a.txt"
    text.write_text("hello", encoding="utf-8")
    img = _write_jpeg(tmp_path / "b.jpg", seed=21)
    report = dedupe([text, img])
    assert report.removed_count == 0


def test_mixed_batch_keeps_every_unique_file(tmp_path: Path) -> None:
    """好坏混合时，唯一文件一个都不能少。"""
    text = tmp_path / "readme.txt"
    text.write_text("x", encoding="utf-8")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff garbage")

    uniques = [_write_jpeg(tmp_path / f"u{i}.jpg", seed=300 + i * 11) for i in range(4)]
    dup_a = _write_gif(tmp_path / "da.gif", seed=61, duration=80)
    dup_b = _write_gif(tmp_path / "db.gif", seed=61, duration=650)

    report = dedupe([text, broken, *uniques, dup_a, dup_b])
    assert report.removed_count == 1
    assert report.unique_count == 7  # 2跳过 + 4 唯一 + 1 组代表
    assert len(report.skipped) == 2
