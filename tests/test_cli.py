"""CLI 与正确性守门测试。

覆盖：

- ``scan`` 子命令：只读、不写文件、列出不一致项；
- ``normalize`` 子命令：端到端编排（EM-004 会复用它）；
- **D-13**：微信超大表情**只给中文提示**，不缩放、不重编码、不阻断产出；
- 两条正确性判定（后缀一致 / 动图存活），含「判定本身真的能抓到问题」的阳性对照。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from emoji_migrator import cli
from emoji_migrator.guard import (
    ANIMATION_LOSS_WARNING,
    CORRECTNESS_RULES,
    MIN_ANIMATION_FRAMES,
    RULE_ANIMATION_SURVIVED,
    RULE_SUFFIX_MATCHES_FORMAT,
)
from emoji_migrator.sniff import sniff


def _static(seed: int, size: tuple[int, int] = (64, 64)) -> Image.Image:
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    bits = seed * 2654435761 & 0xFFFFFFFF
    cell = max(size[0] // 8, 1)
    for index in range(64):
        col, row = index % 8, index // 8
        draw.rectangle(
            (col * cell, row * cell, (col + 1) * cell - 1, (row + 1) * cell - 1),
            fill=(0, 0, 0) if (bits >> index) & 1 else (255, 255, 255),
        )
    return img


def _write_gif(path: Path, seed: int, frames: int = 4) -> Path:
    imgs = [_static(seed * 10 + i) for i in range(frames)]
    imgs[0].save(path, format="GIF", save_all=True, append_images=imgs[1:], duration=80, loop=0)
    return path


def _frames_of(path: Path) -> int:
    with Image.open(path) as img:
        return getattr(img, "n_frames", 1)


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """一个小而全的样本目录：错乱动图、正常 JPEG、正常动图、超大图。"""
    root = tmp_path / "in"
    root.mkdir()
    _write_gif(root / "moved.jpg", seed=3)          # 声明 jpg 实为 GIF
    _write_gif(root / "ok.gif", seed=9)             # 后缀正确
    _static(5).save(root / "still.jpg", format="JPEG")
    _static(6, (400, 400)).save(root / "huge.jpg", format="JPEG")  # 超大尺寸
    (root / "notes.txt").write_text("不是图片", encoding="utf-8")
    return root


# --------------------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------------------


def test_scan_reports_the_mismatch_count(sample_dir: Path, capsys) -> None:
    assert cli.main(["scan", str(sample_dir)]) == 0
    out = capsys.readouterr().out
    assert "后缀错乱：1 个" in out


def test_scan_lists_the_mismatching_file(sample_dir: Path, capsys) -> None:
    cli.main(["scan", str(sample_dir)])
    out = capsys.readouterr().out
    assert "moved.jpg: JPEG -> GIF [MISMATCH]" in out


def test_scan_is_read_only(sample_dir: Path, capsys) -> None:
    """``scan`` 不得创建、修改或删除任何文件。

    逐文件比对字节 + 比对文件名字典（少了或多了都算改动）。
    注意 ``ok.gif`` 是**本就有**的样本，不是 scan 产生的。
    """
    before = {p.name: p.read_bytes() for p in sample_dir.iterdir() if p.is_file()}
    cli.main(["scan", str(sample_dir)])
    after = {p.name: p.read_bytes() for p in sample_dir.iterdir() if p.is_file()}
    assert before == after
    assert sorted(before) == ["huge.jpg", "moved.jpg", "notes.txt", "ok.gif", "still.jpg"]


def test_scan_verbose_lists_every_file(sample_dir: Path, capsys) -> None:
    cli.main(["scan", str(sample_dir), "--verbose"])
    out = capsys.readouterr().out
    assert "ok.gif" in out and "still.jpg" in out


def test_scan_on_empty_directory_is_fine(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["scan", str(empty)]) == 0
    assert "受支持图片：0 个" in capsys.readouterr().out


def test_scan_on_missing_directory_returns_error_code(tmp_path: Path, capsys) -> None:
    assert cli.main(["scan", str(tmp_path / "nope")]) == 2
    assert "不是目录" in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------------------


def test_normalize_corrects_the_suffix_and_passes_the_guard(
    sample_dir: Path, tmp_path: Path, capsys
) -> None:
    out_dir = tmp_path / "out"
    assert cli.main(["normalize", str(sample_dir), "-o", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "后缀修正：1 个" in out
    assert "正确性守门（2 条）：通过" in out
    assert sniff(out_dir / "moved.gif").mismatch is False


def test_normalize_output_has_no_mismatches(sample_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    cli.main(["normalize", str(sample_dir), "-o", str(out_dir)])
    wrong = [p.name for p in out_dir.iterdir() if p.is_file() and sniff(p).mismatch]
    assert not wrong


def test_normalize_keeps_animations_alive(sample_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    cli.main(["normalize", str(sample_dir), "-o", str(out_dir)])
    assert _frames_of(out_dir / "moved.gif") > 1
    assert _frames_of(out_dir / "ok.gif") > 1


def test_normalize_skips_non_images_without_failing(
    sample_dir: Path, tmp_path: Path, capsys
) -> None:
    """后缀**不受支持**的文件（如 ``.txt``）在扫描阶段就被滤掉，压根不进修正流程。"""
    out_dir = tmp_path / "out"
    assert cli.main(["normalize", str(sample_dir), "-o", str(out_dir)]) == 0
    assert not (out_dir / "notes.txt").exists()
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "huge.jpg",
        "moved.gif",
        "ok.gif",
        "still.jpg",
    ]


def test_normalize_dedupe_can_be_disabled(sample_dir: Path, tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "out"
    cli.main(["normalize", str(sample_dir), "-o", str(out_dir), "--no-dedupe"])
    assert "去重" not in capsys.readouterr().out


def test_normalize_dedupe_output_shape(sample_dir: Path, tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "out"
    cli.main(["normalize", str(sample_dir), "-o", str(out_dir), "--dedupe"])
    out = capsys.readouterr().out
    # 4 个可识别样本（.txt 被collect_images 滤掉），彼此不重复
    assert "去重前：4 个" in out
    assert "去重后：4 个" in out


def test_normalize_reports_unreadable_image_without_failing(tmp_path: Path, capsys) -> None:
    """后缀受支持但内容**既无魔数又解不开**⇒ 必须跳过并说明，且**不阻断**整批产出。

    样本内容刻意用「无任何已知魔数前缀」的数据：若写成 ``b"GIF89a" + 零字节``，
    嗅探会靠魔数兜底判成 GIF 并正常改名——那条路径由
    :mod:`test_sniff` 的 ``test_truncated_gif_is_still_identified_as_gif`` 守着，
    在这里用它当「无法判定」的样本是拿错了前提。
    """
    root = tmp_path / "in"
    root.mkdir()
    (root / "broken.jpg").write_bytes(b"\x00\x01\x02\x03not an image at all\xff" * 8)
    _static(3).save(root / "good.jpg", format="JPEG")

    out_dir = tmp_path / "out"
    assert cli.main(["normalize", str(root), "-o", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "无法判定真实格式" in out
    assert "跳过：1 个" in out
    assert (out_dir / "good.jpg").exists(), "一个坏文件不该让整批产出消失"
    assert not (out_dir / "broken.gif").exists()


def test_normalize_reports_duplicates(sample_dir: Path, tmp_path: Path, capsys) -> None:
    import shutil

    shutil.copyfile(sample_dir / "moved.jpg", sample_dir / "moved_copy.jpg")
    out_dir = tmp_path / "out"
    cli.main(["normalize", str(sample_dir), "-o", str(out_dir)])
    out = capsys.readouterr().out
    assert "移除重复 1 个" in out


def test_normalize_on_empty_directory(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["normalize", str(empty), "-o", str(tmp_path / "out")]) == 0
    assert "受支持图片：0 个" in capsys.readouterr().out


def test_normalize_on_missing_directory_returns_error_code(tmp_path: Path) -> None:
    assert cli.main(["normalize", str(tmp_path / "nope"), "-o", str(tmp_path / "o")]) == 2


def test_compliance_subcommand_still_works(capsys) -> None:
    assert cli.main(["compliance"]) == 0
    out = capsys.readouterr().out
    assert "policy:" in out and "forbidden api count: 9" in out


def test_no_command_prints_help(capsys) -> None:
    assert cli.main([]) == 0
    assert "usage:" in capsys.readouterr().out


def test_collect_images_finds_files_recursively(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    _static(1).save(nested / "x.png", format="PNG")
    (tmp_path / "a" / "ignore.txt").write_text("x", encoding="utf-8")
    found = cli.collect_images(tmp_path)
    assert [p.name for p in found] == ["x.png"]


def test_collect_images_on_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        cli.collect_images(tmp_path / "nope")


# --------------------------------------------------------------------------------------
# D-13：微信超大表情只提示不处理
# --------------------------------------------------------------------------------------


def test_oversize_hint_text_is_exactly_as_specified() -> None:
    """D-13 文案逐字固定，包含「可能把动图变成静态图」这个核心提醒。"""
    rendered = cli.WECHAT_OVERSIZE_HINT.format(edge=cli.OVERSIZE_EDGE)
    assert "该表情超出 240×240，微信导入时会自行压缩" in rendered
    assert "注意微信的压缩可能把动图变成静态图" in rendered


def test_oversize_emits_the_hint(sample_dir: Path, tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "out"
    cli.main(["normalize", str(sample_dir), "-o", str(out_dir)])
    out = capsys.readouterr().out
    assert "该表情超出 240×240" in out
    assert "注意微信的压缩可能把动图变成静态图" in out


def test_oversize_does_not_block_or_resize(sample_dir: Path, tmp_path: Path, capsys) -> None:
    """D-13：只提示——**不缩放、不重编码、不阻断产出**。

    三件事分别可断言：退出码仍为 0、文件尺寸不变、字节与源完全一致。
    """
    source = sample_dir / "huge.jpg"
    out_dir = tmp_path / "out"
    before = source.read_bytes()

    assert cli.main(["normalize", str(sample_dir), "-o", str(out_dir)]) == 0

    produced = out_dir / "huge.jpg"
    assert produced.exists(), "超大表情被丢弃了（不该阻断产出）"
    assert produced.stat().st_size == source.stat().st_size, "超大表情被缩放了"
    assert produced.read_bytes() == before, "超大表情被重编码了"


def test_normal_size_gets_no_hint(sample_dir: Path, tmp_path: Path, capsys) -> None:
    root = tmp_path / "small"
    root.mkdir()
    _static(4, (32, 32)).save(root / "tiny.png", format="PNG")
    cli.main(["normalize", str(root), "-o", str(tmp_path / "out")])
    assert "该表情超出" not in capsys.readouterr().out


# --------------------------------------------------------------------------------------
# 两条正确性判定
# --------------------------------------------------------------------------------------


def test_correctness_rules_are_exactly_the_two_agreed_ones() -> None:
    """范围简化的结果：体积/尺寸/透明度判定已作废，不在规则集里。"""
    assert CORRECTNESS_RULES == (RULE_SUFFIX_MATCHES_FORMAT, RULE_ANIMATION_SURVIVED)
    assert len(CORRECTNESS_RULES) == 2


def test_min_animation_frames_is_strictly_above_one() -> None:
    """``>= 1`` 对任何可解码图片都成立，等于没有断言。

    这条直接钉住突变实验 M2 的反面：常量必须是 2。
    这样「把判定改成 ``n_frames >= 1``」才会被本条与
    :func:`test_verify_output_flags_a_dead_animation` 一起抓住。
    """
    assert MIN_ANIMATION_FRAMES == 2


def test_verify_output_accepts_a_correct_file(tmp_path: Path) -> None:
    good = tmp_path / "a.gif"
    _write_gif(good, seed=3)
    assert cli.verify_output(good, was_animated=True) == []


def test_verify_output_flags_a_wrong_suffix(tmp_path: Path) -> None:
    """阳性对照：判定逻辑本身要真能抓到后缀错乱。"""
    bad = tmp_path / "a.jpg"
    _write_gif(bad, seed=3)
    problems = cli.verify_output(bad, was_animated=True)
    assert [p.rule for p in problems] == [RULE_SUFFIX_MATCHES_FORMAT]


def test_verify_output_flags_a_dead_animation(tmp_path: Path) -> None:
    """**阳性对照（最关键）**：输入是动图、输出只剩一帧 ⇒ 必须报违规。

    没有这条，``n_frames >= 1`` 那种写法能一直混下去而测试全绿——
    因为真实管线上从不丢帧，判定压根不会被执行到。
    这里手工造一个「本该是动图却是静态」的文件，把它喂进判定。
    """
    dead = tmp_path / "dead.gif"
    _static(3).save(dead, format="GIF")  # 单帧 GIF

    assert _frames_of(dead) == 1, "前提失效：这个文件应当是单帧"
    problems = cli.verify_output(dead, was_animated=True)
    assert [p.rule for p in problems] == [RULE_ANIMATION_SURVIVED]
    assert ANIMATION_LOSS_WARNING in problems[0].detail


def test_static_input_is_not_flagged_as_dead_animation(tmp_path: Path) -> None:
    """静态图本就该单帧；若对它也要求多帧就是把「本该静态」误判成违规。"""
    still = tmp_path / "still.gif"
    _static(3).save(still, format="GIF")
    assert cli.verify_output(still, was_animated=False) == []


def test_verify_output_flags_both_problems_at_once(tmp_path: Path) -> None:
    bad = tmp_path / "a.jpg"
    _static(3).save(bad, format="GIF")  # 后缀错 + 丢帧
    assert {p.rule for p in cli.verify_output(bad, was_animated=True)} == {
        RULE_SUFFIX_MATCHES_FORMAT,
        RULE_ANIMATION_SURVIVED,
    }
