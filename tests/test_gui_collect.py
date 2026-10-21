"""GUI 抖音采集入口测试（EM-005-D）。

本文件守护三件事：

1. **按钮可用性跟着登录缓存走**——无缓存禁用、有缓存启用、登录成功后解禁；
2. **网络请求不在 UI 线程**（BL-13「UI 同步执行卡顿」的守护）——采集走 QThread；
3. **摘要/失败文案是中文可读的**，且**不含 cookie 值**（隐私铁律）。

与 :mod:`tests.test_collect_flow`（编排逻辑）、:mod:`tests.test_cli_collect`（命令行）
分工明确：本文件**只测 UI 层**，不打桩网络。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from emoji_migrator import gui

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def state(tmp_path: Path) -> gui.WizardState:
    return gui.WizardState(out_dir=tmp_path / "out")


@pytest.fixture
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """无登录缓存（不碰真实 APPDATA）。"""
    monkeypatch.setattr(gui, "has_cached_session", lambda: False)


@pytest.fixture
def with_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """有登录缓存。"""
    monkeypatch.setattr(gui, "has_cached_session", lambda: True)


def _page(qapp: QApplication, state: gui.WizardState) -> gui._PlatformPage:
    return gui._PlatformPage(state)


# -- 按钮可用性 ----------------------------------------------------------------


def test_collect_button_disabled_without_cache(qapp, state, no_cache) -> None:
    """没登录 → 采集按钮禁用（不自动登录，和EM-005-B 同一条原则）。"""
    page = _page(qapp, state)
    assert page._collect_button.isEnabled() is False


def test_collect_button_enabled_with_cache(qapp, state, with_cache) -> None:
    """有登录缓存 → 采集按钮可用。"""
    page = _page(qapp, state)
    assert page._collect_button.isEnabled() is True


def test_login_refresh_also_unlocks_collect(qapp, state, monkeypatch) -> None:
    """登录完成后采集按钮要跟着解禁——否则用户登录完仍点不动。"""
    state_flag = {"cached": False}
    monkeypatch.setattr(gui, "has_cached_session", lambda: state_flag["cached"])
    page = _page(qapp, state)
    assert page._collect_button.isEnabled() is False

    state_flag["cached"] = True  #模拟登录完成
    page._refresh_douyin_login_button()
    assert page._collect_button.isEnabled() is True


def test_collect_button_exists_on_page(qapp, state, no_cache) -> None:
    """按钮确实挂在第 2 页上（落点守护）。"""
    page = _page(qapp, state)
    assert page._collect_button.text() == "采集抖音收藏表情"


def test_click_without_cache_does_not_start_thread(qapp, state, no_cache) -> None:
    """无缓存时点采集不得启动线程。"""
    page = _page(qapp, state)
    page._start_collect()
    assert page._collect_thread is None
    assert "登录抖音" in page._collect_status.text()


def test_collect_thread_not_started_twice(qapp, state, with_cache) -> None:
    """采集进行中不接受第二次启动。"""
    page = _page(qapp, state)

    class _FakeThread:
        def __init__(self, out_dir):
            self.out_dir = out_dir

    page._collect_thread = _FakeThread(Path("x"))
    page._start_collect()
    assert isinstance(page._collect_thread, _FakeThread), "不得替换正在跑的线程"


# -- 不卡 UI 线程 --------------------------------------------------------------


def test_collect_runs_in_a_qthread_not_ui_thread(qapp, state, with_cache) -> None:
    """核心守护：**网络请求必须在 QThread 里跑**。

    BL-13 实测记录过「UI 同步执行卡顿」——采集含几十次网络往返 + Pillow 转换，
    放 UI 线程会让窗口无响应。本测试断言线程类型 + ``run()`` 里真调了编排，
    改动成同步调用（页面里直接调 ``collect_douyin_stickers``）必须变红。
    """
    from PySide6.QtCore import QThread

    assert issubclass(gui._CollectThread, QThread)
    source = inspect.getsource(gui._CollectThread.run)
    assert "collect_douyin_stickers" in source
    assert "super().run()" not in source, "run 必须重写，不能调基类实现"

    # 反向断言：页面本身**不**直接调编排（那才是真卡 UI）
    page_source = inspect.getsource(gui._PlatformPage._start_collect)
    assert "collect_douyin_stickers" not in page_source, "页面不得直接调采集"
    assert "_CollectThread" in page_source, "页面必须走线程"


def test_page_start_collect_spawns_thread(qapp, state, with_cache, monkeypatch) -> None:
    """点采集 → 创建 _CollectThread（而不是直接调编排）。

    打桩方式：替换 ``gui._CollectThread`` 为一个假的类，
    记录构造参数与 ``start()`` 调用。
    """
    page = _page(qapp, state)
    started: list = []

    class _FakeSignal:
        def connect(self, slot):
            started.append(("connect", getattr(slot, "__name__", "slot")))

        def emit(self, *_args):
            pass

    class _FakeThread:
        def __init__(self, out_dir, parent=None):
            started.append(("init", str(out_dir)))
            self.finished_ok = _FakeSignal()
            self.failed = _FakeSignal()

        def start(self) -> None:
            started.append(("start", None))

    monkeypatch.setattr(gui, "_CollectThread", _FakeThread)
    page._start_collect()
    kinds = [k for k, _ in started]
    assert "init" in kinds and "start" in kinds, f"必须走 start()，实际 {kinds}"
    connected = [v for k, v in started if k == "connect"]
    assert any("collect" in name for name in connected), "必须接上完成/失败回调"


def test_collect_button_disabled_while_running(qapp, state, with_cache) -> None:
    """采集中按钮禁用，防止重复启动。"""
    page = _page(qapp, state)
    page._collect_thread = object()  # type: ignore[assignment]
    page._refresh_collect_button()
    assert page._collect_button.isEnabled() is False


# -- 结果与失败文案 ------------------------------------------------------------


def test_collect_done_updates_status_and_sources(qapp, state) -> None:
    """采集成功：更新状态栏，并把产物目录并入向导来源（走正常导出流程）。"""
    page = _page(qapp, state)
    out_dir = state.out_dir / "douyin"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "a.gif").write_bytes(b"GIF89a")

    page._on_collect_done("采集完成：1 页 / 列表 1 条 / 转成 GIF 1 个")

    assert "采集完成" in page._collect_status.text()
    assert out_dir in state.sources
    assert page._collect_thread is None


# -- 真机复验抓到的 bug 专属守护（2026-10-20）---------------------------------
#
#⚠️ **这个 bug 让用户「导出结果只有 QQ」**。它之所以能溜过去，是因为
#    初版测试**总是先建好 out_dir**，正好绕开了「out_dir 为 None」的真实路径：
#       _start_collect 写  (out_dir or default_output_dir()) / "douyin"
#       _on_collect_done 读  out_dir / "douyin"          ← out_dir=None 时炸在这
#    采集显示成功、38 个文件真躺在磁盘上，但 sources 是空的 → 导出时没有它。
#    教训：**测试必须复现真实用户路径**，不是复现「我写测试时方便构造的路径」。


def test_collect_without_out_dir_still_registers_sources(qapp, tmp_path, monkeypatch) -> None:
    """核心守护：**用户正常流程**（第 2 页点采集，``out_dir`` 还是 ``None``）
    下，采集产物**必须**进 ``sources``——否则导出时只有 QQ。

    这是本文件最重要的一条。它对应用户实际遇到的现象：
    采集显示成功、文件在磁盘上、但导出目录里没有抖音表情。
    """
    monkeypatch.setattr(gui, "has_cached_session", lambda: True)
    # 隔离 default_output_dir，避免真写用户主目录
    fake_out = tmp_path / "default-out"
    monkeypatch.setattr(gui, "default_output_dir", lambda: fake_out)

    # 真实起点：out_dir 为 None
    state = gui.WizardState()
    assert state.out_dir is None
    page = _page(qapp, state)

    # 模拟线程写出了产物（目录结构与 _start_collect 一致）
    (fake_out / "douyin").mkdir(parents=True, exist_ok=True)
    (fake_out / "douyin" / "x.gif").write_bytes(b"GIF89a")

    page._on_collect_done("采集完成：1 页 / 列表 1 条")

    assert state.out_dir is not None, "out_dir 必须被就地赋值"
    assert state.sources, "❌ 采集产物没进 sources（用户导出时会只有 QQ）"
    assert state.sources[0] == fake_out / "douyin"
    assert "未找到采集产物目录" not in page._collect_status.text()


def test_collect_dir_is_single_source_of_truth(qapp, tmp_path, monkeypatch) -> None:
    """「算目录」与「登记目录」必须用**同一个值**——两处各算各的正是 bug 根因。

    守护方式：先让 ``_start_collect`` 算出目录，再让 ``_on_collect_done`` 登记，
    断言登记的目录**就是**采集时用的那个。
    """
    monkeypatch.setattr(gui, "has_cached_session", lambda: True)
    fake_out = tmp_path / "out-root"
    monkeypatch.setattr(gui, "default_output_dir", lambda: fake_out)

    state = gui.WizardState()
    page = _page(qapp, state)

    created: list[Path] = []

    class _FakeThread:
        finished_ok = _Sig()
        failed = _Sig()

        def __init__(self, out_dir, parent=None):
            created.append(Path(out_dir))

        def start(self):
            pass

    monkeypatch.setattr(gui, "_CollectThread", _FakeThread)
    page._start_collect()
    assert created, "线程必须被创建"
    collect_dir = created[0]

    collect_dir.mkdir(parents=True, exist_ok=True)
    (collect_dir / "y.gif").write_bytes(b"GIF89a")
    page._on_collect_done("采集完成")

    assert state.sources == [collect_dir], (
        f"登记的目录与采集用的目录不一致：{state.sources} vs {collect_dir}"
    )


class _Sig:
    """最小 Signal 替身（供打桩线程用）。"""

    def connect(self, slot):
        pass

    def emit(self, *_a):
        pass


def test_collect_with_missing_output_dir_reports_instead_of_silent(qapp, tmp_path) -> None:
    """线程说成功但目录不存在 → 明确报「未找到」，不静默失败。

    静默失败是这类 bug 最难受的地方：用户看到「采集完成」，却什么都没导出。
    """
    state = gui.WizardState(out_dir=tmp_path / "out")
    page = _page(qapp, state)
    page._on_collect_done("采集完成")
    assert "未找到采集产物目录" in page._collect_status.text()


def test_export_page_does_not_overwrite_out_dir_set_by_collect(qapp, tmp_path) -> None:
    """采集定下的 ``out_dir`` 不能被第 3 页覆盖——否则采集产物与导出目录脱节。"""
    state = gui.WizardState(out_dir=tmp_path / "fixed-out")
    export_page = gui._ExportPage(state)
    export_page.initializePage()
    assert state.out_dir == tmp_path / "fixed-out", "第 3 页不得覆盖已定的导出目录"


def test_collect_done_without_output_dir_does_not_crash(qapp, state) -> None:
    """产物目录不存在时不崩（用户可能在采集途中改了目录）。"""
    state.out_dir = None
    page = _page(qapp, state)
    page._on_collect_done("采集完成")
    assert "采集完成" in page._collect_status.text()


def test_collect_failed_shows_chinese_reason(qapp, state) -> None:
    """失败：显示中文原因并恢复按钮可用。"""
    page = _page(qapp, state)
    page._collect_thread = object()  # type: ignore[assignment]
    page._on_collect_failed("登录态已过期，请重新扫码登录。")

    assert page._collect_thread is None
    assert "登录态已过期" in page._collect_status.text()
    assert page._collect_progress.isVisible() is False


def test_expired_summary_prompt_mentions_rescan(monkeypatch, tmp_path) -> None:
    """401 的文案必须引导重新扫码（D-15 口径）——**真跑一遍 run()**，不查源码字符串。"""
    from emoji_migrator.douyin_collect import SessionExpired

    emitted: list[str] = []

    def raising_collect(*args, **kwargs):
        raise SessionExpired("过期")

    monkeypatch.setattr(gui, "load_cached_cookie", lambda: {"sessionid": "x"})
    monkeypatch.setattr(gui, "collect_douyin_stickers", raising_collect)
    thread = gui._CollectThread(tmp_path)
    thread.failed.connect(emitted.append)
    thread.run()

    assert emitted, "401 必须走 failed 信号"
    assert "重新" in emitted[0] and "扫码" in emitted[0]


def test_summarize_includes_counts_and_dir(tmp_path: Path) -> None:
    """摘要含翻页/列表/下载/转换四项计数 + 产物目录。"""
    from emoji_migrator.collect_flow import CollectOutcome

    outcome = CollectOutcome(
        pages=2, listed=41, downloaded=41, converted=40,
        duplicates=1, failed=0, convert_failed=1, out_dir=tmp_path,
    )
    text = gui._CollectThread._summarize(outcome)
    assert "2 页" in text
    assert "41" in text
    assert "40" in text
    assert str(tmp_path) in text
    assert "跨页重复 1 个" in text


def test_summarize_flags_page_limit_hit() -> None:
    """命中上限必须在摘要里说清楚（不静默截断）。"""
    from emoji_migrator.collect_flow import CollectOutcome

    outcome = CollectOutcome(pages=200, listed=10000, downloaded=1, converted=1,
                             hit_page_limit=True)
    assert "翻页上限" in gui._CollectThread._summarize(outcome)


def test_summarize_omits_zero_counters() -> None:
    """计数为 0 的项不出现（摘要不塞废话）。"""
    from emoji_migrator.collect_flow import CollectOutcome

    clean = CollectOutcome(pages=1, listed=1, downloaded=1, converted=1)
    text = gui._CollectThread._summarize(clean)
    assert "重复" not in text
    assert "失败" not in text


def test_summarize_never_contains_cookie_like_values() -> None:
    """隐私守护：摘要里不出现 cookie 样式的内容。"""
    from emoji_migrator.collect_flow import CollectOutcome

    outcome = CollectOutcome(
        pages=1, listed=1, downloaded=1, converted=1,
        failures=[("idX", "下载", "HTTP 500")],
    )
    text = gui._CollectThread._summarize(outcome)
    assert "sessionid" not in text
    assert "passport" not in text


# -- 线程安全：信号回主线程 ----------------------------------------------------


def test_thread_defines_both_signals() -> None:
    """两个信号必须在（成功 / 失败各一条路径）。"""
    assert hasattr(gui._CollectThread, "finished_ok")
    assert hasattr(gui._CollectThread, "failed")


def test_thread_run_emits_failed_when_no_cookie(monkeypatch, tmp_path) -> None:
    """无 cookie → emit failed（而不是崩溃或静默）。

    打桩方式：用 ``signal.connect`` 挂回调，**不替换 Signal 对象**
    （Signal 是 Qt 的类属性，赋一个 lambda 上去会让 ``.emit`` 变成普通函数属性——
    实测踩过，报 ``'function' object has no attribute 'emit'``）。
    """
    emitted: list[str] = []
    monkeypatch.setattr(gui, "load_cached_cookie", lambda: None)
    thread = gui._CollectThread(tmp_path)
    thread.failed.connect(emitted.append)
    thread.run()
    assert emitted and "登录" in emitted[0]


def test_thread_run_emits_failed_on_session_expired(monkeypatch, tmp_path) -> None:
    """401 → emit failed 且文案引导重扫。"""
    from emoji_migrator.douyin_collect import SessionExpired

    emitted: list[str] = []

    def raising_collect(*args, **kwargs):
        raise SessionExpired("过期")

    monkeypatch.setattr(gui, "load_cached_cookie", lambda: {"sessionid": "x"})
    monkeypatch.setattr(gui, "collect_douyin_stickers", raising_collect)
    thread = gui._CollectThread(tmp_path)
    thread.failed.connect(emitted.append)
    thread.run()
    assert emitted and "重新" in emitted[0]


def test_thread_run_swallows_unknown_exceptions(monkeypatch, tmp_path) -> None:
    """未知异常不得把原始消息抛给用户（可能带路径等细节）。"""

    def raising_collect(*args, **kwargs):
        raise ValueError("内部细节 /secret/path")

    emitted: list[str] = []
    monkeypatch.setattr(gui, "load_cached_cookie", lambda: {"sessionid": "x"})
    monkeypatch.setattr(gui, "collect_douyin_stickers", raising_collect)
    thread = gui._CollectThread(tmp_path)
    thread.failed.connect(emitted.append)
    thread.run()
    assert emitted
    assert "/secret/path" not in emitted[0], "原始异常消息不得外泄"
    assert "ValueError" in emitted[0]


def test_thread_run_emits_ok_on_success(monkeypatch, tmp_path) -> None:
    """成功 → emit finished_ok 带中文摘要。"""
    from emoji_migrator.collect_flow import CollectOutcome

    emitted: list[str] = []
    outcome = CollectOutcome(pages=1, listed=1, downloaded=1, converted=1, out_dir=tmp_path)
    monkeypatch.setattr(gui, "load_cached_cookie", lambda: {"sessionid": "x"})
    monkeypatch.setattr(gui, "collect_douyin_stickers", lambda *a, **k: outcome)
    thread = gui._CollectThread(tmp_path)
    thread.finished_ok.connect(emitted.append)
    thread.run()
    assert emitted and "采集完成" in emitted[0]


# -- 依赖方向 ------------------------------------------------------------------


def test_gui_imports_collect_flow_not_the_other_way() -> None:
    """编排层不得反向 import gui（否则 CLI 用不了它）。"""
    flow_source = (REPO_ROOT / "src" / "emoji_migrator" / "collect_flow.py").read_text(
        encoding="utf-8"
    )
    assert "import gui" not in flow_source
    assert "from emoji_migrator.gui" not in flow_source


def test_collect_flow_has_no_qt_dependency() -> None:
    """编排层不能依赖 Qt——它要能被纯命令行复用。"""
    source = (REPO_ROOT / "src" / "emoji_migrator" / "collect_flow.py").read_text(
        encoding="utf-8"
    )
    assert "PySide6" not in source
    assert "QThread" not in source
