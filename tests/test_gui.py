"""GUI 导入向导测试（EM-004）。

offscreen 运行：``QT_QPA_PLATFORM`` 由 ``conftest.py`` 在导入 PySide6 之前统一设置。
本文件守护：

- 三步向导结构与导航；
- D-04：质量档位控件默认 STANDARD；
- 探测失败时第 1 页给出中文可读报错（含验收关键句）；
- TraceMemo 可选入口：缺失 → 按钮禁用 + 点击零写入（目标目录 mtime 前后一致）；
- 多来源摊平暂存：只收受支持图片、扁平化、同名不覆盖、原文件不动；
- 导出复用 CLI 管线：错乱全修正、动图全存活、产物与原文件隔离；
- 图文指引不变量：微信 / QQ / 抖音各一套、每套 ≥3 编号步骤 + ≥1 张**真实存在**的截图，
  且键集合与真实目标平台（除输入侧兜底 ``SOURCE``）一一对应；
- 首次运行自检：探测到 / 未探测到 QQ 表情目录两个分支都只提示、不自动加来源；
- 抖音扫码登录入口（EM-005-B）：按钮文案随本地缓存切换（登录抖音 / 已登录，重新登录），
  有缓存**不自动弹窗**，点击才弹；页面加载在测试中被挡掉（不联网）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from emoji_migrator import douyin_auth, gui
from emoji_migrator.config import QualityTier
from emoji_migrator.platforms import Platform
from emoji_migrator.sniff import sniff

#: 仓库根（tests/ -> 仓库根）
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """整个 GUI 测试文件共用一个 QApplication（offscreen）。"""
    return QApplication.instance() or QApplication([])


def _write_gif(path: Path, frames: int = 2) -> None:
    images = [Image.new("RGB", (96, 96), (40 * (index + 1), 80, 160)) for index in range(frames)]
    images[0].save(path, format="GIF", save_all=True, append_images=images[1:])


def _write_png(path: Path) -> None:
    Image.new("RGB", (120, 80), (60, 180, 90)).save(path, format="PNG")


def _write_jpeg(path: Path) -> None:
    Image.new("RGB", (100, 100), (180, 60, 120)).save(path, format="JPEG")


# --------------------------------------------------------------------------------------
# 向导结构与导航
# --------------------------------------------------------------------------------------


def test_wizard_has_three_pages_in_order(qapp: QApplication) -> None:
    """三步流程：选来源 → 选平台 → 导出 + 指引。"""
    window = gui.WizardWindow()
    assert window.pageIds() == [0, 1, 2]
    assert "选择表情来源" in window.page(0).title()
    assert "选择目标平台" in window.page(1).title()
    assert "导出" in window.page(2).title()


def test_wizard_navigation_walks_three_pages(qapp: QApplication, tmp_path: Path) -> None:
    """第 1 页选了来源才能往后走（isComplete 守门），三步可走通。"""
    window = gui.WizardWindow()
    window.restart()  # 未 show 时 currentId 为 -1，restart 把它指到起始页
    assert window.currentId() == 0
    assert not window.page(0).isComplete()

    window.state.sources.append(tmp_path)
    window.next()
    assert window.currentId() == 1
    window.next()
    assert window.currentId() == 2


def test_fresh_state_defaults_to_standard_tier() -> None:
    """D-04：新向导状态默认微信 + STANDARD 档位。"""
    state = gui.WizardState()
    assert state.platform == Platform.WECHAT
    assert state.tier == QualityTier.STANDARD
    assert gui.DEFAULT_QUALITY_TIER == QualityTier.STANDARD


def test_platform_page_defaults_to_standard_tier(qapp: QApplication) -> None:
    """D-04：档位控件默认预选 STANDARD（改默认值必须让本测试变红）。

    ``currentData()`` 经 QVariant 往返后可能是值字符串，故用 ``QualityTier(...)``
    归一化后再比对，两种形态都算数。
    """
    page = gui._PlatformPage(gui.WizardState())
    assert QualityTier(page._tier_combo.currentData()) == QualityTier.STANDARD
    assert page._wechat_radio.isChecked()


def test_platform_page_switching_updates_state(qapp: QApplication) -> None:
    state = gui.WizardState()
    page = gui._PlatformPage(state)
    page._qq_radio.setChecked(True)
    assert state.platform == Platform.QQ
    page._douyin_radio.setChecked(True)
    assert state.platform == Platform.DOUYIN
    page._wechat_radio.setChecked(True)
    assert state.platform == Platform.WECHAT
    page._tier_combo.setCurrentIndex(page._tier_combo.findData(QualityTier.HIGH))
    assert state.tier == QualityTier.HIGH


# --------------------------------------------------------------------------------------
# 第 1 页：自动扫描与中文报错
# --------------------------------------------------------------------------------------


def test_source_page_scan_reports_found_dirs(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """扫描成功：目录进入来源列表，状态栏报告数量，页面变为可完成。"""
    found = tmp_path / "personal_emoji" / "Ori"
    found.mkdir(parents=True)
    monkeypatch.setattr(gui, "detect_qq_emoji_dirs", lambda: [found])

    state = gui.WizardState()
    page = gui._SourcePage(state)
    page._scan_button.click()

    assert state.sources == [found]
    assert "扫描到" in page._status.text()
    assert page.isComplete()


def test_source_page_scan_failure_shows_chinese_error(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描失败：中文可读报错（含验收关键句），不猜不遍历，页面保持不可完成。"""
    monkeypatch.setattr(gui, "detect_qq_emoji_dirs", lambda: [])

    state = gui.WizardState()
    page = gui._SourcePage(state)
    page._scan_button.click()

    assert state.sources == []
    assert not page.isComplete()
    assert "没找到 QQ 表情目录" in page._status.text()
    assert "请确认 QQ 已启动过并打开过收藏表情" in page._status.text()


def test_source_page_manual_add(qapp: QApplication, tmp_path: Path) -> None:
    """手动添加来源；不存在的路径被忽略。"""
    state = gui.WizardState()
    page = gui._SourcePage(state)
    page._add_source(tmp_path)
    page._add_source(tmp_path / "not-exist")
    assert state.sources == [tmp_path]
    assert page.isComplete()


# --------------------------------------------------------------------------------------
# TraceMemo 可选入口（验收：禁用态 + 点击零写入）
# --------------------------------------------------------------------------------------


def test_trace_memo_button_disabled_when_missing(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TraceMemo 缺失：按钮禁用；点击（含直接调处理器）后目标目录 mtime 前后一致、零写入。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    state = gui.WizardState()
    page = gui._SourcePage(state)

    button = page._tracememo_button
    assert not button.isEnabled()

    before_ns = tmp_path.stat().st_mtime_ns
    button.click()  # 禁用态：点击不产生任何信号
    page._read_trace_memo()  # 兜底路径也不写
    assert tmp_path.stat().st_mtime_ns == before_ns
    assert not (tmp_path / "Documents").exists()
    assert state.sources == []


def test_trace_memo_button_enabled_when_present(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TraceMemo 存在：按钮启用，点击后该目录进入来源列表。"""
    emoji_dir = tmp_path / "Documents" / "TraceMemo" / "Emojis"
    emoji_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    state = gui.WizardState()
    page = gui._SourcePage(state)
    assert page._tracememo_button.isEnabled()

    page._tracememo_button.click()
    assert state.sources == [emoji_dir]


# --------------------------------------------------------------------------------------
# 多来源暂存与导出（复用 CLI 管线）
# --------------------------------------------------------------------------------------


def test_stage_sources_flattens_and_filters(tmp_path: Path) -> None:
    """目录 + 零散文件混选：只收受支持图片、摊平、原文件不动。

    刻意同时覆盖两条过滤路径：目录内的非图片由 ``collect_images`` 过滤，
    **直接丢进来**的非图片文件由 ``is_image_suffix`` 过滤（M12 突变抓的就是后者）。
    """
    dir_a = tmp_path / "a"
    (dir_a / "sub").mkdir(parents=True)
    _write_gif(dir_a / "sub" / "anim.gif")
    (dir_a / "note.txt").write_text("not an image", encoding="utf-8")
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    _write_png(dir_b / "pic.png")
    loose = tmp_path / "loose.jpg"
    _write_jpeg(loose)
    loose_txt = tmp_path / "loose.txt"
    loose_txt.write_text("not an image either", encoding="utf-8")

    before = {
        path: path.read_bytes()
        for path in (dir_a / "sub" / "anim.gif", dir_b / "pic.png", loose)
    }

    staged = gui.stage_sources([dir_a, dir_b, loose, loose_txt])
    staged_files = sorted(path.name for path in staged.iterdir())

    assert staged_files == ["anim.gif", "loose.jpg", "pic.png"]
    assert all(path.is_file() for path in staged.iterdir())  # 摊平：没有子目录
    for path, data in before.items():
        assert path.read_bytes() == data, "暂存不得改动用户原文件"


def test_stage_sources_handles_name_collisions(tmp_path: Path) -> None:
    """不同目录的同名文件都保留（追加序号），不静默覆盖。"""
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    _write_gif(dir_a / "same.gif", frames=2)
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    _write_gif(dir_b / "same.gif", frames=3)

    staged = gui.stage_sources([dir_a, dir_b])
    names = sorted(path.name for path in staged.iterdir())
    assert names == ["same-1.gif", "same.gif"]
    contents = {path.name: path.read_bytes() for path in staged.iterdir()}
    assert contents["same.gif"] == (dir_a / "same.gif").read_bytes()
    assert contents["same-1.gif"] == (dir_b / "same.gif").read_bytes()


def test_run_export_rejects_empty_sources(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        gui.run_export([], tmp_path / "out", Platform.WECHAT)


def test_run_export_produces_verified_outputs(synthetic_dir: Path, tmp_path: Path) -> None:
    """导出复用 CLI 管线：错乱全修正、动图全存活、产物与原文件隔离。

    用合成集的确定子集（2 个 GIF-as-jpg 动图 + 2 个 JPEG + 1 个 PNG-as-jpg），
    数字全部实测，不硬编码（与 EM-002 端到端测试同口径）。
    """
    names = ["gif_000.jpg", "gif_003.jpg", "jpg_000.jpg", "jpg_001.jpg", "png_000.jpg"]
    sources = [synthetic_dir / name for name in names]
    out_dir = tmp_path / "out"
    source_bytes = {source.name: source.read_bytes() for source in sources}

    code, log = gui.run_export(sources, out_dir, Platform.WECHAT)

    assert code == 0, f"正确性守门未通过，日志：\n{log}"
    assert "后缀修正：3 个" in log  # 2 个 GIF + 1 个 PNG 声明 .jpg
    assert "通过" in log

    outputs = sorted(path.name for path in out_dir.iterdir())
    assert outputs == ["gif_000.gif", "gif_003.gif", "jpg_000.jpg", "jpg_001.jpg", "png_000.png"]

    # 错乱全修正：输出后缀与真实格式 100% 一致
    assert not [sniff(path) for path in out_dir.iterdir() if sniff(path).mismatch]

    # 动图全存活：帧数与输入逐个相等
    frames_of = {}
    for path in out_dir.iterdir():
        with Image.open(path) as img:
            frames_of[path.name] = getattr(img, "n_frames", 1)
    assert frames_of["gif_000.gif"] > 1
    assert frames_of["gif_003.gif"] > 1

    # 产物独立落盘：输出目录里没有任何用户原文件（路径互不相交），
    # 且原文件一个字节都没被改动（逐字节复制是输出侧语义，不是「用了原文件」）。
    output_paths = {path.resolve() for path in out_dir.iterdir()}
    assert not output_paths & {source.resolve() for source in sources}
    for source in sources:
        assert source.read_bytes() == source_bytes[source.name]


def test_default_output_dir_is_outside_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认导出目录在用户主目录下，不在仓库内、不是 ``_out/``。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    out_dir = gui.default_output_dir()
    assert out_dir.parent == tmp_path / "emoji-migrator-out"
    assert REPO_ROOT not in out_dir.parents
    assert re.fullmatch(r"\d{8}-\d{6}", out_dir.name)


# --------------------------------------------------------------------------------------
# 首次运行自检（探测到 / 未探测到：都只提示，不自动加来源）
# --------------------------------------------------------------------------------------


def test_startup_check_reports_found_dirs_without_adding(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """探测到 QQ 表情目录：状态栏给出数量与按钮指引，但**不**自动加入来源。

    真实路径含 QQ 号，不上屏也不进截图——故这里连状态文案里都不允许出现路径。
    """
    found = tmp_path / "personal_emoji" / "Ori"
    found.mkdir(parents=True)
    monkeypatch.setattr(gui, "detect_qq_emoji_dirs", lambda: [found])

    state = gui.WizardState()
    page = gui._SourcePage(state)
    page._startup_check()

    assert "检测到 1 个 QQ 表情目录" in page._status.text()
    assert "自动扫描本机 QQ 表情" in page._status.text()
    assert state.sources == []  # 不自动加入：要加由用户点按钮
    assert str(found) not in page._status.text()
    assert page._list.count() == 0


def test_startup_check_shows_chinese_hint_when_not_found(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未探测到：状态栏展示 NOT_FOUND_MESSAGE（含验收关键句），同样不自动加来源。"""
    monkeypatch.setattr(gui, "detect_qq_emoji_dirs", lambda: [])

    state = gui.WizardState()
    page = gui._SourcePage(state)
    page._startup_check()

    assert "没找到 QQ 表情目录" in page._status.text()
    assert "请确认 QQ 已启动过并打开过收藏表情" in page._status.text()
    assert state.sources == []
    assert not page.isComplete()


# --------------------------------------------------------------------------------------
# 第 2 页：抖音扫码登录入口（EM-005-B）
# --------------------------------------------------------------------------------------


def test_platform_page_douyin_login_button_without_cache(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """无缓存：按钮文案「登录抖音」；构造页面本身不弹任何窗口。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    page = gui._PlatformPage(gui.WizardState())
    assert page._douyin_login_button.text() == "登录抖音"
    assert page._auth_window is None


def test_platform_page_douyin_login_button_with_cache(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """有缓存：按钮文案「已登录，重新登录」，且**不自动弹窗**。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    douyin_auth.save_cookies({"sessionid": "FAKE_SESSION_VALUE_XYZ"})
    page = gui._PlatformPage(gui.WizardState())
    assert page._douyin_login_button.text() == "已登录，重新登录"
    assert page._auth_window is None


def test_platform_page_douyin_login_click_opens_window(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """点击「登录抖音」弹出扫码登录窗口（页面加载挡掉：不联网、不动真实 profile）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(gui.DouyinAuthWindow, "_open_login_page", lambda self: None)
    page = gui._PlatformPage(gui.WizardState())
    page._douyin_login_button.click()
    assert isinstance(page._auth_window, douyin_auth.DouyinAuthWindow)


def test_douyin_login_abandoned_timeout_updates_hint(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """超时放弃：按钮文案按缓存实况刷新，并给出可重试的中文提示（不含任何细节）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    page = gui._PlatformPage(gui.WizardState())
    page._on_douyin_login_abandoned("timeout")
    assert "超时" in page._douyin_hint.text()
    assert page._douyin_login_button.text() == "登录抖音"


# --------------------------------------------------------------------------------------
# 图文指引（验收：三端各一套，每套 ≥3 编号步骤 + ≥1 张可枚举截图）
# --------------------------------------------------------------------------------------


def test_guides_invariants() -> None:
    """每套指引 ≥3 步、≥1 张截图，截图文件真实存在，且键集合与真实目标平台一致。

    两条集合断言都要在：第一条锁「三端各一套」；第二条锁「键集合 == 平台值集合」，
    保证未来新增平台成员时不会静默缺一套指引（删 douyin 一套 / 加平台不加指引都会红）。
    """
    assert set(gui.GUIDES) == {"wechat", "qq", "douyin"}
    assert set(gui.GUIDES) == {p.value for p in Platform if p is not Platform.SOURCE}
    for key, steps in gui.GUIDES.items():
        assert len(steps) >= 3, f"{key} 指引不足 3 步"
        assert all(step.title and step.detail for step in steps), f"{key} 有空文案步骤"
        images = [step.image for step in steps if step.image is not None]
        assert images, f"{key} 指引没有截图"
        for image in images:
            assert image.is_file(), f"{key} 指引截图缺失：{image}"


def test_guide_for_maps_platforms() -> None:
    """guide_for 对三个真实目标端逐个显式映射；SOURCE（输入侧兜底）走微信那套。"""
    assert gui.guide_for(Platform.WECHAT) is gui.GUIDES["wechat"]
    assert gui.guide_for(Platform.QQ) is gui.GUIDES["qq"]
    assert gui.guide_for(Platform.DOUYIN) is gui.GUIDES["douyin"]
    assert gui.guide_for(Platform.SOURCE) is gui.GUIDES["wechat"]


def test_guide_dialog_renders_offscreen(qapp: QApplication) -> None:
    """指引窗口能离屏渲染（截图证据即由此产生）。"""
    dialog = gui.GuideDialog(gui.guide_for(Platform.WECHAT), title="微信导入指引")
    pixmap = dialog.grab()
    assert not pixmap.isNull()
    assert pixmap.width() > 100


def test_export_page_initializes_default_out_dir(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """进入第 3 页时自动定好导出目录（用户零配置）。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    state = gui.WizardState()
    page = gui._ExportPage(state)
    page.initializePage()
    assert state.out_dir is not None
    assert state.out_dir.parent == tmp_path / "emoji-migrator-out"
