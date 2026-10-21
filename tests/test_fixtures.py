"""合成测试集的生成契约。

工作包要求「脚本要可重复运行（幂等），测试可调用它或直接断言数字」。
本文件把**分布常量与实测数字**对上——两者缺一不可：

- 只断言常量 ⇒ 生成逻辑改坏了也全绿（常量没变）；
- 只断言实测 ⇒ 有人把目标分布改成 100 个 JPEG 也全绿。

「无真实用户表情」这条硬约束也在此守护。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from emoji_migrator.sniff import sniff

#: 验收标准的期望数字（独立于生成脚本的常量重新写一遍，避免同源错误）
EXPECTED_TOTAL = 146
EXPECTED_GIF = 49
EXPECTED_JPEG = 94
EXPECTED_PNG = 3
EXPECTED_MISMATCH = 52
EXPECTED_ANIMATED = 49
EXPECTED_MIN_HUGE_FILES = 2
EXPECTED_MIN_OVERSIZE_FILES = 1
EXPECTED_MIN_EXACT_DUPLICATES = 3


def _files(directory: Path) -> list[Path]:
    return sorted((p for p in directory.iterdir() if p.is_file()), key=lambda p: p.name)


def _frames_of(path: Path) -> int:
    with Image.open(path) as img:
        return getattr(img, "n_frames", 1)


def test_generated_count_is_exactly_146(synthetic_dir: Path) -> None:
    assert len(_files(synthetic_dir)) == EXPECTED_TOTAL


def test_generated_format_distribution(synthetic_dir: Path) -> None:
    counts: dict[str, int] = {}
    for path in _files(synthetic_dir):
        fmt = sniff(path).actual_format
        counts[fmt] = counts.get(fmt, 0) + 1
    assert counts == {"GIF": EXPECTED_GIF, "JPEG": EXPECTED_JPEG, "PNG": EXPECTED_PNG}


def test_generated_mismatch_count(synthetic_dir: Path) -> None:
    mismatches = [p for p in _files(synthetic_dir) if sniff(p).mismatch]
    assert len(mismatches) == EXPECTED_MISMATCH


def test_every_generated_file_is_named_jpg(synthetic_dir: Path) -> None:
    """全部以 ``.jpg`` 命名，这样「错乱」才全部来自内容而非后缀本身。"""
    assert {p.suffix for p in _files(synthetic_dir)} == {".jpg"}


def test_generated_animation_count(synthetic_dir: Path) -> None:
    animated = [p for p in _files(synthetic_dir) if _frames_of(p) > 1]
    assert len(animated) == EXPECTED_ANIMATED


def test_oversize_samples_exist(synthetic_dir: Path) -> None:
    """超大尺寸样本 ≥1 个（1018×1024 以上）。"""
    big = []
    for path in _files(synthetic_dir):
        with Image.open(path) as img:
            if min(img.size) >= 1018 and max(img.size) >= 1024:
                big.append(path)
    assert len(big) >= EXPECTED_MIN_OVERSIZE_FILES


def test_huge_volume_samples_exist(synthetic_dir: Path) -> None:
    """超大体积样本 ≥2 个（2300 KB 以上）。"""
    huge = [p for p in _files(synthetic_dir) if p.stat().st_size >= 2300 * 1024]
    assert len(huge) >= EXPECTED_MIN_HUGE_FILES


def test_static_normal_samples_exist(synthetic_dir: Path) -> None:
    """静态图正常样本 ≥5 个。"""
    static = [p for p in _files(synthetic_dir) if _frames_of(p) == 1]
    assert len(static) >= 5


def test_exact_duplicate_pairs_exist(synthetic_dir: Path) -> None:
    """完全重复（同内容不同文件名）≥3 组。"""
    digests: dict[str, list[str]] = {}
    for path in _files(synthetic_dir):
        digests.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), []).append(path.name)
    repeated = [names for names in digests.values() if len(names) > 1]
    assert len(repeated) >= EXPECTED_MIN_EXACT_DUPLICATES


def test_duplicate_families_are_pixel_identical(synthetic_dir: Path) -> None:
    """重复样本必须与被复制样本**像素完全一致**，否则「重复」无从谈起。"""
    from emoji_migrator.dedupe import compute_phash

    files = {p.name: p for p in _files(synthetic_dir)}
    pairs = [
        ("gif_000.jpg", "gif_dupbyte_0.jpg"),
        ("gif_003.jpg", "gif_dupslow_0.jpg"),
        ("gif_004.jpg", "gif_dupslow_1.jpg"),
        ("jpg_000.jpg", "jpg_dupbyte_0.jpg"),
        ("png_000.jpg", "png_dupmeta.jpg"),
    ]
    for original, duplicate in pairs:
        assert original in files, f"缺少样本 {original}"
        assert duplicate in files, f"缺少样本 {duplicate}"
        assert compute_phash(files[original]) == compute_phash(files[duplicate]), (
            f"{original} 与 {duplicate} 像素不一致，重复样本失去意义"
        )


def test_some_duplicates_differ_in_bytes(synthetic_dir: Path) -> None:
    """必须存在「像素相同、字节不同」的重复样本。

    这是 M4 突变（把感知哈希换成文件字节哈希）能被抓住的前提：
    若所有重复都逐字节相同，字节哈希与感知哈希行为一致，测试抓不到退化。
    """
    files = {p.name: p for p in _files(synthetic_dir)}
    differ = [
        (a, b)
        for a, b in (
            ("gif_003.jpg", "gif_dupslow_0.jpg"),
            ("gif_004.jpg", "gif_dupslow_1.jpg"),
            ("png_000.jpg", "png_dupmeta.jpg"),
        )
        if files[a].read_bytes() != files[b].read_bytes()
    ]
    assert len(differ) == 3, "「同像素不同字节」的重复样本数量与预期不符"


def test_unique_samples_are_not_accidentally_duplicates(synthetic_dir: Path) -> None:
    """唯一样本之间要离得足够远，否则会被误判成重复、污染去重断言。"""
    from emoji_migrator.dedupe import DEFAULT_PHASH_DISTANCE, compute_phash, hamming_distance

    unique = [p for p in _files(synthetic_dir) if "dup" not in p.name]
    hashes = [(p.name, compute_phash(p)) for p in unique]

    closest = min(
        (
            hamming_distance(hashes[i][1], hashes[j][1])
            for i in range(len(hashes))
            for j in range(i + 1, len(hashes))
        ),
        default=DEFAULT_PHASH_DISTANCE + 1,
    )
    assert closest > DEFAULT_PHASH_DISTANCE, f"有唯一样本彼此相距仅 {closest} 位，会被误判为重复"


def test_generation_is_idempotent(generator, tmp_path: Path) -> None:
    """跑两次得到同样的文件名集合与同样的字节（幂等）。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    generator.generate(first)
    generator.generate(second)

    names_a = sorted(p.name for p in first.iterdir() if p.is_file())
    names_b = sorted(p.name for p in second.iterdir() if p.is_file())
    assert names_a == names_b

    mismatched = [
        name
        for name in names_a
        if hashlib.sha256((first / name).read_bytes()).digest()
        != hashlib.sha256((second / name).read_bytes()).digest()
    ]
    assert not mismatched, f"重复运行时这些文件的字节变了：{mismatched}"


def test_rerun_into_same_dir_does_not_pile_up(generator, tmp_path: Path) -> None:
    """往同一目录跑两次，文件数不得增加（否则重复执行会越堆越多）。"""
    generator.generate(tmp_path / "out")
    first_count = len([p for p in (tmp_path / "out").iterdir() if p.is_file()])
    generator.generate(tmp_path / "out")
    second_count = len([p for p in (tmp_path / "out").iterdir() if p.is_file()])
    assert first_count == second_count == EXPECTED_TOTAL


def test_generated_constants_match_the_acceptance_numbers(generator) -> None:
    """生成脚本自己声明的分布常量必须与验收标准一致。"""
    assert generator.TOTAL_FIXTURES == EXPECTED_TOTAL
    assert generator.GIF_AS_JPG == EXPECTED_GIF
    assert generator.JPEG_AS_JPG == EXPECTED_JPEG
    assert generator.PNG_AS_JPG == EXPECTED_PNG
    assert generator.MISMATCH_TOTAL == EXPECTED_MISMATCH
    assert generator.ANIMATED_TOTAL == EXPECTED_ANIMATED
    assert generator.HUGE_MIN_BYTES == 2300 * 1024
    assert generator.OVERSIZED_MIN_EDGE == 1018


def test_no_real_emoji_directory_is_referenced_by_the_generator() -> None:
    """生成脚本不得指向本机真实表情目录（版权 + 隐私红线）。

    ``fixtures/README.md`` 的硬约束是「不得提交任何用户真实表情」。
    除了不复制文件，脚本里**连路径都不该出现**——出现了就说明将来
    有人可能顺手「改成从那里读」。
    """
    source = (Path(__file__).resolve().parents[1] / "fixtures" / "generate_fixtures.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("personal_emoji", "Tencent Files", "nt_qq", "Persit", "Persist", "chat"):
        assert forbidden not in source, f"生成脚本引用了真实表情目录关键词：{forbidden}"


def test_generated_set_contains_no_user_paths(generator, tmp_path: Path) -> None:
    """生成过程不读取本机任何既有目录：产物全部落在传入的 ``out_dir`` 内。"""
    out = tmp_path / "only_here"
    generator.generate(out)
    produced = [p for p in out.rglob("*") if p.is_file()]
    assert produced
    assert all(out in p.parents for p in produced)


def test_generator_exposes_the_documented_entry_points(generator) -> None:
    """生成脚本必须暴露 ``generate`` / ``main``，否则外部（EM-004、CI）无法复用。"""
    assert callable(generator.generate)
    assert callable(generator.main)
    assert generator.generate.__doc__, "generate() 必须有docstring 说明它会写文件"
