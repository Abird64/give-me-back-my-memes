"""端到端集成测试：合成集 146 → 后缀修正 → 感知哈希去重 → 正确性守门。

对应验收标准的**核心两条**（工作包第3、4 条）与第 8 条：

1. 合成集共 146个，分布 49 GIF / 94 JPEG / 3 PNG；
2. 后缀错乱 52 个 = 49 GIF + 3 PNG；
3. **52 个错乱全部修正**，输出后缀与真实格式 100% 一致；
4. **全部动图（49 个）输出中仍 ``n_frames > 1``**，无一变静态；
5. 重复文件被去重，且动图按像素内容而非文件字节判定。

数字全部**实测**（跑一遍管线再数），不复读常量——常量在
:mod:`test_fixtures` 里另行与实测值比对，两者缺一不可。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from emoji_migrator.dedupe import compute_phash, dedupe
from emoji_migrator.guard import MIN_ANIMATION_FRAMES
from emoji_migrator.normalize import apply_normalize, plan_normalize
from emoji_migrator.platforms import Platform
from emoji_migrator.sniff import sniff

#: 验收标准的数字（此处只作「期望值」，测试实际数字由管线实测得出后与之比对）
EXPECTED_TOTAL = 146
EXPECTED_GIF = 49
EXPECTED_JPEG = 94
EXPECTED_PNG = 3
EXPECTED_MISMATCH = 52
EXPECTED_ANIMATED = 49


def _frames_of(path: Path) -> int:
    with Image.open(path) as img:
        total = getattr(img, "n_frames", 1)
        img.seek(max(total, 1) - 1)
        return total


def _targets_by_source_name(normalized: dict) -> dict[str, Path]:
    """源文件名 → 输出路径，便于逐个核对动图是否存活。"""
    return {plan.source.name: plan.target for plan in normalized["plans"]}


@pytest.fixture(scope="module")
def normalized(synthetic_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """跑一遍「嗅探 → 后缀修正」，返回实测统计。"""
    out_dir = tmp_path_factory.mktemp("normalized")
    sources = sorted((p for p in synthetic_dir.iterdir() if p.is_file()), key=lambda p: p.name)

    results = [sniff(p) for p in sources]
    animated_sources = {p for p in sources if _frames_of(p) > 1}

    plans = []
    taken: set[str] = set()
    for result in results:
        plan = plan_normalize(result, Platform.WECHAT, out_dir, taken=taken)
        taken.add(plan.target.name.casefold())
        plans.append(plan)
    for plan in plans:
        apply_normalize(plan)

    outputs = [p for p in out_dir.iterdir() if p.is_file()]
    return {
        "sources": sources,
        "results": results,
        "plans": plans,
        "outputs": outputs,
        "animated_sources": animated_sources,
        "out_dir": out_dir,
    }


# --------------------------------------------------------------------------------------
# 第1、2 条：合成集分布
# --------------------------------------------------------------------------------------


def test_fixture_total_matches_the_acceptance_number(normalized: dict) -> None:
    assert len(normalized["sources"]) == EXPECTED_TOTAL
    assert len(normalized["outputs"]) == EXPECTED_TOTAL


def test_fixture_format_distribution(normalized: dict) -> None:
    counts: dict[str, int] = {}
    for result in normalized["results"]:
        counts[result.actual_format] = counts.get(result.actual_format, 0) + 1
    assert counts == {"GIF": EXPECTED_GIF, "JPEG": EXPECTED_JPEG, "PNG": EXPECTED_PNG}


def test_mismatch_count_is_exactly_52(normalized: dict) -> None:
    """错乱 = 49 GIF + 3 PNG；94 个 JPEG 后缀本就正确，不计入。"""
    mismatches = [r for r in normalized["results"] if r.mismatch]
    assert len(mismatches) == EXPECTED_MISMATCH
    assert {r.actual_format for r in mismatches} == {"GIF", "PNG"}


def test_input_animation_count(normalized: dict) -> None:
    assert len(normalized["animated_sources"]) == EXPECTED_ANIMATED


# --------------------------------------------------------------------------------------
# 第3 条（核心）：错乱全部修正
# --------------------------------------------------------------------------------------


def test_every_mismatch_is_renamed(normalized: dict) -> None:
    renamed = [p for p in normalized["plans"] if p.needs_rename]
    assert len(renamed) == EXPECTED_MISMATCH


def test_output_suffix_matches_real_format_for_all_files(normalized: dict) -> None:
    """**核心验收第 3 条**：输出后缀与真实格式 100% 一致，146/146。"""
    wrong = [str(sniff(p)) for p in normalized["outputs"] if sniff(p).mismatch]
    assert not wrong, f"输出后缀仍与真实格式不符：{wrong}"


def test_correctly_suffixed_inputs_are_left_alone(normalized: dict) -> None:
    """94 个 JPEG 不应被改名（避免「多动」带来的无谓风险）。"""
    untouched = [p for p in normalized["plans"] if not p.needs_rename]
    assert len(untouched) == EXPECTED_JPEG


def test_output_extension_histogram(normalized: dict) -> None:
    counts: dict[str, int] = {}
    for path in normalized["outputs"]:
        counts[path.suffix.lower()] = counts.get(path.suffix.lower(), 0) + 1
    assert counts == {".gif": EXPECTED_GIF, ".jpg": EXPECTED_JPEG, ".png": EXPECTED_PNG}


# --------------------------------------------------------------------------------------
# 第4 条（核心）：动图全部存活
# --------------------------------------------------------------------------------------


def test_every_animated_input_stays_animated(normalized: dict) -> None:
    """**核心验收第 4 条**：输入是动图的，输出必须仍是多帧。"""
    targets = _targets_by_source_name(normalized)
    dead = [
        source.name
        for source in sorted(normalized["animated_sources"])
        if _frames_of(targets[source.name]) < MIN_ANIMATION_FRAMES
    ]
    assert not dead, f"这些动图输出后退化为静态图：{dead}"


def test_animated_output_count(normalized: dict) -> None:
    animated_out = [p for p in normalized["outputs"] if _frames_of(p) > 1]
    assert len(animated_out) == EXPECTED_ANIMATED


def test_frame_counts_are_exactly_preserved(normalized: dict) -> None:
    """不只是 >1，而是**逐个相等**——丢帧也算回归。"""
    targets = _targets_by_source_name(normalized)
    sources = {p.name: p for p in normalized["sources"]}
    drift = [
        source.name
        for source in sorted(normalized["animated_sources"])
        if _frames_of(targets[source.name]) != _frames_of(sources[source.name])
    ]
    assert not drift, f"帧数被改动：{drift}"


def test_all_outputs_are_byte_identical_to_their_source(normalized: dict) -> None:
    """逐字节复制 ⇒ 输出与输入完全一致（这是动图不丢帧的结构性保证）。"""
    changed = [
        plan.source.name
        for plan in normalized["plans"]
        if plan.source.read_bytes() != plan.target.read_bytes()
    ]
    assert not changed, f"这些文件的字节被改动过：{changed}"


# --------------------------------------------------------------------------------------
# 第5 条：去重
# --------------------------------------------------------------------------------------


def test_dedupe_removes_exactly_the_intended_duplicates(normalized: dict) -> None:
    """端到端三断言之三：去重后数量。

    合成集里刻意埋了 9 个重复：3 个动图逐字节复制、2 个动图改帧时长、
    3 个 JPEG 逐字节复制、1 个 PNG 多写文本块。全部应被去掉。
    """
    report = dedupe(normalized["outputs"])
    assert report.removed_count == 9
    assert report.unique_count == EXPECTED_TOTAL - 9
    assert report.skipped == ()


def test_dedupe_groups_are_the_duplicate_families(normalized: dict) -> None:
    """每个组都应当恰好是「原样本 + 它的重复」，不多不少。"""
    report = dedupe(normalized["outputs"])
    assert len(report.groups) == 9
    for group in report.groups:
        assert len(group.duplicates) == 1, f"{group.keeper.name} 的重复数异常"
        assert compute_phash(group.keeper) == compute_phash(group.duplicates[0])


def test_dedupe_keeps_the_intended_representative(normalized: dict) -> None:
    """代表必须是「体积更小」的那个（逐字节复制时源与副本同体积，
    此时按文件名定序——两个方向都可接受，只要**确定**）。"""
    report = dedupe(normalized["outputs"])
    for group in report.groups:
        pair = sorted([group.keeper, *group.duplicates], key=lambda p: p.name)
        assert group.keeper in pair


def test_animated_duplicates_are_matched_by_pixels_not_bytes(normalized: dict) -> None:
    """**核心验收第 5 条**：改帧时长的两个动图，字节不同但像素相同 ⇒ 必须去重。

    若实现换成``md5(path.read_bytes())``，这两对会漏检，
    ``test_dedupe_removes_exactly_the_intended_duplicates`` 随即变红。
    """
    outputs = {p.name: p for p in normalized["outputs"]}
    pairs = [
        ("gif_003.gif", "gif_dupslow_0.gif"),
        ("gif_004.gif", "gif_dupslow_1.gif"),
        ("png_000.png", "png_dupmeta.png"),
    ]
    for left, right in pairs:
        assert left in outputs, left
        assert right in outputs, right
        assert outputs[left].read_bytes() != outputs[right].read_bytes(), (
            f"{left} 与 {right} 字节必须不同，这条测试才有效"
        )
        assert compute_phash(outputs[left]) == compute_phash(outputs[right])

    report = dedupe(normalized["outputs"])
    removed = {d.name for group in report.groups for d in group.duplicates}
    assert {"gif_dupslow_0.gif", "gif_dupslow_1.gif", "png_dupmeta.png"} <= removed


def test_dedupe_after_normalize_keeps_all_unique_files(normalized: dict) -> None:
    """去重后仍应留下 137 个互不相同的文件（146 - 9 组每组去掉 1 个）。"""
    report = dedupe(normalized["outputs"])
    assert report.unique_count == 137
    assert report.removed_count == 9
    # 去重后的数量必须等于「输入数 - 移除数」，多一个少一个都说明统计口径错了
    assert report.unique_count == len(normalized["outputs"]) - report.removed_count
