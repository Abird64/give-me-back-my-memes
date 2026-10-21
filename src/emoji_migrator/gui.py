"""PySide6 导入向导（EM-004）：把 EM-002 的规范化管线包成三步桌面流程。

职责边界：本模块**只做编排与呈现**，不实现任何业务算法——

- 目录探测在 :mod:`emoji_migrator.qqdir`（D-07 两级链，只读）；
- 嗅探 / 后缀修正 / 去重 / 正确性守门**不重写**：导出复用
  :func:`emoji_migrator.cli._normalize_dir`（EM-002 的受测编排），
  多来源（目录 + 零散文件混选）先由 :func:`stage_sources` 摊平暂存，
  再走**同一条**管线，输出目录与用户原文件完全隔离；
- 指引文案与步骤截图随包发布（``guides/<平台>/stepN.png``，文件名由
  :data:`GUIDES` 枚举；EM-004/EM-006 阶段截图取自本工具自身窗口的 offscreen 抓取，
  真实客户端截图不在本产品范围）。

三步流程：**选来源**（首次运行自检 / 自动扫描 / 拖拽 / 添加 / 现有导出 / TraceMemo）→
**选平台**（微信 / QQ / 抖音，质量档位默认 STANDARD，D-04；抖音可顺手扫码登录，
EM-005-B，有缓存时**不自动弹窗**）→ **导出 + 图文指引**。

依赖方向（``AGENTS.md`` §3）：本模块在业务模块**之上**做编排，
``gui → {cli, qqdir, config, platforms, sniff, douyin_auth}``；业务模块不得 import 本模块，
故箭头单向、无环。
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from emoji_migrator import cli
from emoji_migrator.collect_flow import NO_COOKIE_HINT, collect_douyin_stickers
from emoji_migrator.config import PACKAGE_ROOT, QualityTier
from emoji_migrator.douyin_auth import DouyinAuthWindow, has_cached_session, load_cached_cookie
from emoji_migrator.douyin_collect import SessionExpired
from emoji_migrator.platforms import Platform
from emoji_migrator.qqdir import NOT_FOUND_MESSAGE, detect_qq_emoji_dirs
from emoji_migrator.sniff import is_image_suffix

__all__ = [
    "GUIDES",
    "DEFAULT_QUALITY_TIER",
    "NOT_FOUND_MESSAGE",
    "TRACE_MEMO_RELATIVE_PATH",
    "GuideStep",
    "GuideDialog",
    "WizardState",
    "WizardWindow",
    "guide_for",
    "trace_memo_dir",
    "default_output_dir",
    "stage_sources",
    "run_export",
    "main",
]

#: D-04 裁决：质量档位选择控件默认预选 STANDARD。
DEFAULT_QUALITY_TIER = QualityTier.STANDARD

#: TraceMemo 表情目录相对用户主目录的位置（可选增强，缺失不影响主流程）。
TRACE_MEMO_RELATIVE_PATH = ("Documents", "TraceMemo", "Emojis")

#: 档位下拉框的中文标签（档位本身不做任何压缩，见 D-12/D-13）。
_TIER_LABELS: dict[QualityTier, str] = {
    QualityTier.DRAFT: "草稿 draft",
    QualityTier.STANDARD: "标准 standard",
    QualityTier.HIGH: "高清 high",
}

#: 导出目录的默认根（``~/emoji-migrator-out``）。刻意不用仓库内 ``_out/``：
#: 产物必须与仓库工作区、与用户原文件都隔离（验收：导出目录不含任何用户原文件）。
_OUTPUT_ROOT_NAME = "emoji-migrator-out"


@dataclass(frozen=True, slots=True)
class GuideStep:
    """图文指引的一个编号步骤。

    ``image`` 为随包截图的绝对路径（``None`` 表示该步骤只有文字）；
    截图文件名由 :data:`GUIDES` 枚举，验收要求「文件名可枚举」。
    """

    title: str
    detail: str
    image: Path | None = None


def _guide_image(platform_key: str, name: str) -> Path:
    """指引截图路径：``<包根>/guides/<平台>/<name>.png``。"""
    return PACKAGE_ROOT / "guides" / platform_key / f"{name}.png"


#: 微信 / QQ / 抖音各一套导入指引。每套 ≥3 个编号步骤、≥1 张截图（验收标准第 5 条）。
#: 键集合与 :class:`~emoji_migrator.platforms.Platform` 的真实目标端一一对应
#: （``SOURCE`` 是输入侧兜底、不是真实平台，故不占一套）——由
#: ``test_guides_invariants`` 守护，未来加平台不会静默缺指引。
#: 步骤文案面向「小白零配置」：只描述客户端里点哪里，不提任何技术名词。
GUIDES: dict[str, tuple[GuideStep, ...]] = {
    "wechat": (
        GuideStep(
            title="打开微信的表情管理",
            detail="打开微信，点左下角「我」→「表情」→ 右上角的齿轮图标，进入表情管理。",
            image=_guide_image("wechat", "step1"),
        ),
        GuideStep(
            title="添加表情",
            detail="点「添加单张表情」，或在批量添加里选中本工具导出的 .gif 文件。"
            "可以一次多选，微信会逐个导入。",
            image=_guide_image("wechat", "step2"),
        ),
        GuideStep(
            title="在聊天里使用",
            detail="回到聊天窗口打开表情面板，就能看到刚导入的表情。"
            "注意：微信会自行压缩超大表情，可能把动图压成静态图——这是微信自身行为，"
            "本工具不做任何压缩。",
            image=_guide_image("wechat", "step3"),
        ),
    ),
    "qq": (
        GuideStep(
            title="打开 QQ 聊天窗口",
            detail="打开任意 QQ 聊天窗口，点开表情面板，再点左上角的齿轮「设置」图标。",
            image=_guide_image("qq", "step1"),
        ),
        GuideStep(
            title="导入表情",
            detail="选择「导入导出」→「导入表情」，选中本工具导出的目录，"
            "或目录里的 .png / .gif 文件。",
            image=_guide_image("qq", "step2"),
        ),
        GuideStep(
            title="在收藏里查看",
            detail="导入完成后打开「我的收藏」即可看到。QQ 会自己生成缩略图，"
            "原图不会被改动。",
            image=_guide_image("qq", "step3"),
        ),
    ),
    "douyin": (
        GuideStep(
            title="打开抖音的表情面板",
            detail="打开任意抖音聊天窗口，点输入框旁边的笑脸图标打开表情面板，"
            "再点左上角的「+」号。",
            image=_guide_image("douyin", "step1"),
        ),
        GuideStep(
            title="添加表情",
            detail="在相册或文件列表里选中本工具导出的表情文件。"
            "可以一次多选，抖音会逐个导入。",
            image=_guide_image("douyin", "step2"),
        ),
        GuideStep(
            title="在聊天里使用",
            detail="回到聊天窗口打开表情面板，就能看到刚导入的表情。",
            image=_guide_image("douyin", "step3"),
        ),
    ),
}

#: 目标平台 → :data:`GUIDES` 键的**显式映射**。刻意不用 ``else`` 兜底：
#: 新增平台成员时若忘记补指引，这里会当场 ``KeyError``，而不是静默走错一套。
#: ``SOURCE`` 是输入侧兜底标识（不是真实平台），没有自己的指引，走微信那套。
_GUIDE_KEYS: dict[Platform, str] = {
    Platform.WECHAT: "wechat",
    Platform.QQ: "qq",
    Platform.DOUYIN: "douyin",
    Platform.SOURCE: "wechat",
}


def guide_for(platform: Platform) -> tuple[GuideStep, ...]:
    """按目标平台取指引；``SOURCE``（输入侧兜底）走微信那套。"""
    return GUIDES[_GUIDE_KEYS[platform]]


def trace_memo_dir() -> Path:
    """TraceMemo 表情目录（可选增强入口；缺失时 GUI 按钮为禁用态）。"""
    return Path.home().joinpath(*TRACE_MEMO_RELATIVE_PATH)


def default_output_dir() -> Path:
    """默认导出目录：``~/emoji-migrator-out/<时间戳>``。

    每次导出一个新时间戳目录：不覆盖上次产物，也不往用户的来源目录里写任何东西。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / _OUTPUT_ROOT_NAME / stamp


@dataclass
class WizardState:
    """向导三个页面共享的状态。页面只读写这里，互不直接调用。"""

    sources: list[Path] = field(default_factory=list)
    platform: Platform = Platform.WECHAT
    tier: QualityTier = DEFAULT_QUALITY_TIER
    out_dir: Path | None = None
    export_code: int | None = None
    export_log: str = ""


def stage_sources(sources: Sequence[Path]) -> Path:
    """把多来源（目录与零散文件混选）摊平暂存到一个临时目录并返回它。

    为什么需要暂存：导出管线 :func:`emoji_migrator.cli._normalize_dir` 的输入是
    **单个目录**（EM-002 的受测编排，不重写）。拖拽 / 多选可能混有多个目录与
    零散文件，摊平后即可复用同一条管线。暂存目录在系统临时区，与仓库、
    与用户原文件目录都无关；用户原文件只读，不被修改。

    同名文件来自不同目录时追加序号，避免静默覆盖（与
    :func:`emoji_migrator.normalize._target_name` 同一保守策略）。
    """
    staged = Path(tempfile.mkdtemp(prefix="emoji-migrator-stage-"))
    taken: set[str] = set()
    for source in sources:
        if source.is_dir():
            candidates = cli.collect_images(source)
        elif is_image_suffix(source):
            candidates = [source]
        else:
            candidates = []
        for path in candidates:
            name = path.name
            if name.casefold() in taken:
                stem, suffix = path.stem, path.suffix
                index = 1
                while f"{stem}-{index}{suffix}".casefold() in taken:
                    index += 1
                name = f"{stem}-{index}{suffix}"
            taken.add(name.casefold())
            shutil.copyfile(path, staged / name)
    return staged


def run_export(
    sources: Sequence[Path],
    out_dir: Path,
    platform: Platform,
    tier: QualityTier = DEFAULT_QUALITY_TIER,
    dedupe: bool = True,
) -> tuple[int, str]:
    """执行导出并返回 ``(退出码, 日志文本)``。

    退出码口径与 CLI 一致：``0`` = 正确性守门通过，``1`` = 有违规。
    复用 :func:`emoji_migrator.cli._normalize_dir`（stdout 重定向到字符串），
    嗅探 / 修后缀 / 去重 / 守门逻辑因此与命令行完全一致。
    """
    if not sources:
        raise ValueError("还没有选择表情来源")
    staged = stage_sources(sources)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli._normalize_dir(staged, out_dir, platform, dedupe)
    header = "来源：" + "、".join(str(path) for path in sources) + "\n"
    header += f"目标平台：{platform.value}｜质量档位：{tier.value}\n"
    header += "（当前版本不做压缩，档位仅作记录）\n"
    return code, header + buffer.getvalue()


class _DropZone(QListWidget):
    """接受拖拽的列表：文件夹与图片文件都可以丢进来。"""

    dropped = Signal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                self.dropped.emit(Path(local))
        event.acceptProposedAction()


class _SourcePage(QWizardPage):
    """第 1 步：选来源。自动扫描 / 拖拽 / 添加 / 现有导出 / TraceMemo。"""

    def __init__(self, state: WizardState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setTitle("第 1 步：选择表情来源")
        self.setSubTitle("可以自动扫描本机 QQ 收藏表情，也可以把文件夹或图片文件拖进来")

        self._scan_button = QPushButton("自动扫描本机 QQ 表情")
        self._scan_button.clicked.connect(self._scan)
        self._status = QLabel("尚未选择来源")
        self._status.setWordWrap(True)
        self._list = _DropZone()
        self._list.dropped.connect(self._add_source)

        self._add_dir_button = QPushButton("添加文件夹…")
        self._add_dir_button.clicked.connect(self._choose_dir)
        self._add_files_button = QPushButton("添加文件…")
        self._add_files_button.clicked.connect(self._choose_files)
        self._existing_button = QPushButton("使用现有导出目录…")
        self._existing_button.clicked.connect(self._choose_existing)
        self._tracememo_button = QPushButton("一键读取 TraceMemo 表情")
        self._tracememo_button.clicked.connect(self._read_trace_memo)
        self._refresh_trace_memo_button()

        buttons = QHBoxLayout()
        for button in (
            self._add_dir_button,
            self._add_files_button,
            self._existing_button,
            self._tracememo_button,
        ):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scan_button)
        layout.addWidget(self._status)
        layout.addWidget(self._list, stretch=1)
        layout.addLayout(buttons)

        # 首次运行自检：进事件循环后立刻探测一次本机 QQ 表情目录（范围「包含」第 5 条）。
        # 只在状态栏提示，**不把目录加进来源列表**——真实路径含 QQ 号，不上屏也不进
        # 截图；要加入由用户点「自动扫描本机 QQ 表情」。测试不跑事件循环，定时器
        # 不会误触发，两个分支由直接调 ``_startup_check`` 的测试覆盖。
        if QApplication.instance() is not None:
            QTimer.singleShot(0, self._startup_check)

    # -- 交互 ------------------------------------------------------------------

    def _startup_check(self) -> None:
        """首次运行自检：探测本机 QQ 表情目录，仅在状态栏给出提示。

        检测到 → 告诉用户有多少个目录、点哪个按钮加入（目录本身不上屏）；
        未检测到 → 展示 :data:`~emoji_migrator.qqdir.NOT_FOUND_MESSAGE`
        （含「没找到 QQ 表情目录」「请确认 QQ 已启动过并打开过收藏表情」关键句）。
        两个分支都**不自动加入任何来源**：``state.sources`` 保持不变。
        """
        found = detect_qq_emoji_dirs()
        if found:
            hint = f"检测到 {len(found)} 个 QQ 表情目录，点「自动扫描本机 QQ 表情」加入"
            self._status.setText(hint)
            return
        self._status.setText(NOT_FOUND_MESSAGE)

    def _scan(self) -> None:
        """自动扫描：探测失败时给中文可读报错，不猜不遍历（D-07）。"""
        found = detect_qq_emoji_dirs()
        if not found:
            self._status.setText(NOT_FOUND_MESSAGE)
            self.completeChanged.emit()
            return
        for directory in found:
            if directory not in self._state.sources:
                self._state.sources.append(directory)
                self._list.addItem(QListWidgetItem(str(directory)))
        self._status.setText(f"自动扫描到 {len(found)} 个 QQ 表情目录")
        self.completeChanged.emit()

    def _add_source(self, path: Path) -> None:
        if not path.exists() or path in self._state.sources:
            return
        self._state.sources.append(path)
        self._list.addItem(QListWidgetItem(str(path)))
        self._refresh_status()
        self.completeChanged.emit()

    def _choose_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择表情文件夹")
        if directory:
            self._add_source(Path(directory))

    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片文件")
        for file in files:
            self._add_source(Path(file))

    def _choose_existing(self) -> None:
        """使用现有导出目录：QQ 客户端手动导出过的目录、或命令行导出过的目录。"""
        directory = QFileDialog.getExistingDirectory(self, "选择已导出的表情目录")
        if directory:
            self._add_source(Path(directory))

    def _read_trace_memo(self) -> None:
        """TraceMemo 可选入口：目录缺失时**不写入任何东西**（禁用态兜底）。"""
        directory = trace_memo_dir()
        if not directory.is_dir():
            return
        self._add_source(directory)

    # -- 状态 ------------------------------------------------------------------

    def _refresh_trace_memo_button(self) -> None:
        """有目录才启用；没有就禁用并说明原因（验收：禁用态 + 点击零写入）。"""
        available = trace_memo_dir().is_dir()
        self._tracememo_button.setEnabled(available)
        if not available:
            self._tracememo_button.setToolTip(
                "未检测到 TraceMemo 表情目录（%USERPROFILE%\\Documents\\TraceMemo\\Emojis）"
            )

    def _refresh_status(self) -> None:
        count = len(self._state.sources)
        self._status.setText(f"已选 {count} 个来源" if count else "尚未选择来源")

    def isComplete(self) -> bool:
        return bool(self._state.sources)


class _WeixinExportThread(QThread):
    """后台执行微信表情导出（EM-003）。

    ⚠️ **必须走线程**：实测整轮 **289 秒**（扫内存 41 秒 + 1109 个 wxgf 转码），
    放 UI 线程会让窗口**整整 5 分钟无响应**——比抖音采集更严重。
    """

    #: 导出成功，参数为中文摘要
    finished_ok = Signal(str)
    #: 导出失败，参数为中文原因
    failed = Signal(str)
    #: 阶段提示，参数为一句中文（用于更新状态栏）
    stage = Signal(str)

    def __init__(self, out_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._out_dir = Path(out_dir)

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        # 延迟 import：gui import 期就绪时不必带上 wxscan 的 ctypes 依赖
        from emoji_migrator import wxcollect

        try:
            outcome = wxcollect.collect_weixin_emoticons(
                self._out_dir, progress=self.stage.emit
            )
        except wxcollect.WxError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            self.failed.emit(f"导出失败：{type(exc).__name__}")
            return
        self.finished_ok.emit(self._summarize(outcome))

    @staticmethod
    def _summarize(outcome) -> str:
        """把 :class:`~emoji_migrator.wxcollect.WxCollectOutcome` 压成一句中文。"""
        parts = [
            f"微信表情导出完成：{outcome.written} 个文件",
            f"单文件 {outcome.single_files}",
            f"整包切出 {outcome.container_slices}",
        ]
        if outcome.wxgf_converted:
            parts.append(f"动图转 GIF {outcome.wxgf_converted}")
        if outcome.wxgf_failed:
            parts.append(f"（动图转码失败 {outcome.wxgf_failed}，可重试）")
        if outcome.failures:
            parts.append(f"失败 {len(outcome.failures)}")
        parts.append(f"\n耗时 {outcome.scan_seconds:.0f} 秒扫密钥")
        if outcome.out_dir:
            parts.append(f"\n输出目录：{outcome.out_dir}")
        return "｜".join(parts)


class _CollectThread(QThread):
    """后台采集线程（EM-005-D）：网络请求与转换**绝不在 UI 线程做**。

    BL-13 实测记录过「UI 同步执行卡顿」——采集含几十次网络往返与
    Pillow 转换，放 UI 线程会让窗口无响应。故本类把活全搬到
    :meth:`run`，只通过两个信号回主线程：

    - :attr:`finished_ok`：带一段中文摘要；
    - :attr:`failed`：带中文失败原因。

    **信号参数一律是可读文案**，不含 cookie 值（隐私铁律）。

    为什么在 gui 里而不在 collect_flow：线程是 Qt 概念，
    编排层不该依赖 Qt（它要能被 CLI 复用）。
    """

    #: 采集成功，参数为中文摘要（含计数与产物目录）
    finished_ok = Signal(str)
    #: 采集失败，参数为中文原因
    failed = Signal(str)

    def __init__(self, out_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._out_dir = Path(out_dir)

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        # cookie 在**子线程内**读：load_cached_cookie 做 DPAPI 解密，
        # 不该占着 UI 线程。
        cookies = load_cached_cookie()
        if not cookies:
            self.failed.emit(NO_COOKIE_HINT)
            return
        try:
            outcome = collect_douyin_stickers(cookies, self._out_dir)
        except SessionExpired:
            self.failed.emit("登录态已过期（服务端 401），请重新点「登录抖音」扫码。")
            return
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            # 未知异常一律给中文兜底文案，不把异常原文抛给用户
            # （可能含路径等细节），也不打印到控制台（可能带 cookie）。
            self.failed.emit(f"采集失败：{type(exc).__name__}，请重试或换个时间再试。")
            return
        self.finished_ok.emit(self._summarize(outcome))

    @staticmethod
    def _summarize(outcome) -> str:
        """把 :class:`~emoji_migrator.collect_flow.CollectOutcome` 压成一句中文。"""
        parts = [
            f"采集完成：{outcome.pages} 页 / 列表 {outcome.listed} 条",
            f"下载 {outcome.downloaded} 个",
            f"转成 GIF {outcome.converted} 个",
        ]
        if outcome.duplicates:
            parts.append(f"跨页重复 {outcome.duplicates} 个")
        if outcome.failed:
            parts.append(f"下载失败 {outcome.failed} 个")
        if outcome.convert_failed:
            parts.append(f"转换失败 {outcome.convert_failed} 个")
        if outcome.hit_page_limit:
            parts.append("（已达翻页上限，可能未取全）")
        if outcome.out_dir:
            parts.append(f"\n产物目录：{outcome.out_dir}")
        return "｜".join(parts)


class _PlatformPage(QWizardPage):
    """第 2 步：选平台 + 质量档位（默认 STANDARD，D-04）。"""

    def __init__(self, state: WizardState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setTitle("第 2 步：选择目标平台与质量档位")
        self.setSubTitle("档位决定导出质量的记录值；当前版本不做压缩，原图质量不变")

        self._wechat_radio = QRadioButton("微信")
        self._qq_radio = QRadioButton("QQ")
        self._douyin_radio = QRadioButton("抖音")
        self._wechat_radio.setChecked(True)
        self._wechat_radio.toggled.connect(self._sync_platform)
        self._qq_radio.toggled.connect(self._sync_platform)
        self._douyin_radio.toggled.connect(self._sync_platform)

        self._tier_combo = QComboBox()
        for tier in QualityTier:
            self._tier_combo.addItem(_TIER_LABELS[tier], tier)
        self._tier_combo.setCurrentIndex(self._tier_combo.findData(DEFAULT_QUALITY_TIER))
        self._tier_combo.currentIndexChanged.connect(self._sync_tier)

        platforms = QHBoxLayout()
        for radio in (self._wechat_radio, self._qq_radio, self._douyin_radio):
            platforms.addWidget(radio)
        platforms.addStretch(1)

        # 抖音扫码登录入口（EM-005-B）：按钮文案按本地缓存刷新——有缓存显示
        # 「已登录，重新登录」且**不自动弹窗**，登录与否完全由用户点击决定。
        self._auth_window: DouyinAuthWindow | None = None
        self._douyin_login_button = QPushButton("登录抖音")
        self._douyin_login_button.clicked.connect(self._open_douyin_login)
        self._douyin_hint = QLabel()
        self._douyin_hint.setWordWrap(True)

        # 抖音收藏采集入口（EM-005-D）：无登录缓存时禁用——不做自动登录，
        # 登录与否由用户决定（与上面的登录按钮同一条原则）。
        # ⚠️ 顺序要紧：两个刷新方法都在 __init__ 末尾统一调，
        # _refresh_douyin_login_button 会连带刷新采集按钮，
        # 若它先跑就会碰到还没创建的 _collect_button（AttributeError）。
        self._collect_button = QPushButton("采集抖音收藏表情")
        self._collect_button.clicked.connect(self._start_collect)
        self._collect_progress = QProgressBar()
        self._collect_progress.setVisible(False)
        self._collect_status = QLabel()
        self._collect_status.setWordWrap(True)
        self._collect_thread: _CollectThread | None = None
        self._wx_thread: _WeixinExportThread | None = None
        self._refresh_douyin_login_button()

        # 微信表情导出入口（EM-003）：**只读**解密本机微信表情并转成 GIF。
        # 放在第 2 页顶部而不是「来源」页——它读的是微信自己的目录，
        # 不经过第 1 页的来源选择（与抖音采集同一位置的语义）。
        self._wx_button = QPushButton("导出微信表情")
        self._wx_button.clicked.connect(self._start_weixin_export)
        self._wx_hint = QLabel()
        self._wx_hint.setWordWrap(True)
        self._wx_hint.setText(
            "导出微信里收集过的表情（含好友发来的，需微信在运行，约需 1~5 分钟；"
            "只读取，不修改微信数据）"
        )

        douyin_login_row = QHBoxLayout()
        douyin_login_row.addWidget(self._douyin_login_button)
        douyin_login_row.addWidget(self._collect_button)
        douyin_login_row.addStretch(1)

        weixin_row = QHBoxLayout()
        weixin_row.addWidget(self._wx_button)
        weixin_row.addWidget(self._wx_hint, stretch=1)

        form = QHBoxLayout()
        form.addWidget(QLabel("质量档位："))
        form.addWidget(self._tier_combo)
        form.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(platforms)
        layout.addLayout(douyin_login_row)
        layout.addWidget(self._douyin_hint)
        layout.addLayout(weixin_row)
        layout.addWidget(self._collect_progress)
        layout.addWidget(self._collect_status)
        layout.addLayout(form)
        layout.addStretch(1)

    def _sync_platform(self) -> None:
        """三个单选与目标平台一一对应；显式列举，不加隐式兜底。"""
        for radio, platform in (
            (self._wechat_radio, Platform.WECHAT),
            (self._qq_radio, Platform.QQ),
            (self._douyin_radio, Platform.DOUYIN),
        ):
            if radio.isChecked():
                self._state.platform = platform
                return

    def _sync_tier(self) -> None:
        # QVariant 往返后 userData 是 StrEnum 的**值字符串**（实测 itemData 返回
        # 'standard' 而非 QualityTier.STANDARD），必须转回枚举再入状态。
        data = self._tier_combo.currentData()
        if isinstance(data, str):
            try:
                self._state.tier = QualityTier(data)
            except ValueError:
                return

    # -- 抖音扫码登录（EM-005-B）-----------------------------------------------------

    def _refresh_douyin_login_button(self) -> None:
        """按本地缓存刷新按钮文案：有缓存 → 「已登录，重新登录」，否则「登录抖音」。

        只做**本地**判断（:func:`~emoji_migrator.douyin_auth.has_cached_session`
        不碰网络；D-15 的 401 探测留待 EM-005-C 连采集时落地），因此这里
        **不会自动弹登录窗**——登不登录完全由用户点按钮决定。
        """
        if has_cached_session():
            self._douyin_login_button.setText("已登录，重新登录")
            self._douyin_hint.setText("已在本机保存抖音登录状态；也可以点按钮重新扫码登录。")
        else:
            self._douyin_login_button.setText("登录抖音")
            self._douyin_hint.setText("抖音表情采集需要扫码登录（不登录也能先选平台、走到后面）。")
        # 登录状态变了，采集按钮的可用性跟着变（否则新登录完仍显示不可用）。
        self._refresh_collect_button()

    # -- 抖音收藏采集（EM-005-D）---------------------------------------------------

    def _refresh_collect_button(self) -> None:
        """采集按钮的可用性**只取决于本地是否有登录缓存**（不做网络探测）。

        与 :meth:`_refresh_douyin_login_button` 同一条原则：**不自动登录**。
        cookie 是否仍有效由采集时的服务端 401 判定（D-15），不在这里猜。
        采集进行中一律禁用，防止重复启动线程。
        """
        self._collect_button.setEnabled(has_cached_session() and self._collect_thread is None)

    def _start_collect(self) -> None:
        """启动后台采集。

        **绝不在 UI 线程做网络请求**（BL-13 已记录 UI 同步执行卡顿）：
        采集 = 拉列表 + 逐条下载 + WebP 转换，实测单条秒级，
        41 条能在 UI 线程里卡几十秒。这里用 :class:`QThread` + 信号回主线程。
        """
        if not has_cached_session():
            self._collect_status.setText("请先点「登录抖音」完成扫码。")
            return
        if self._collect_thread is not None:
            return

        self._ensure_out_dir()
        collect_dir = self._state.out_dir / "douyin"
        self._collect_progress.setVisible(True)
        self._collect_progress.setRange(0, 0)  # 不确定进度
        self._collect_button.setEnabled(False)
        self._collect_status.setText("正在采集抖音收藏表情，请稍候…")

        thread = _CollectThread(collect_dir)
        thread.finished_ok.connect(self._on_collect_done)
        thread.failed.connect(self._on_collect_failed)
        self._collect_thread = thread
        thread.start()

    def _ensure_out_dir(self) -> None:
        """确保 ``state.out_dir`` 有值（第 2 页就要用，故不能等到第 3 页）。

        ⚠️ **这里曾有一个真 bug（2026-10-20 真机复验发现，用户症状「导出只有 QQ」）**：
        ``_start_collect`` 写 ``(self._state.out_dir or default_output_dir()) / "douyin"``，
        而 :meth:`_on_collect_done` 读 ``self._state.out_dir / "douyin"``——
        **两处各算各的**。用户正常流程在第 2 页点采集时 ``out_dir`` 还是 ``None``
        （它要到第 3 页 ``initializePage`` 才被赋值），于是：
        产物真被下载到了 ``default_output_dir()/douyin``（实测 38 个文件都在），
        但 ``sources`` 登记走 ``None`` 分支**登记失败** → 导出时只有 QQ。
        症状极具欺骗性：采集显示成功、文件就在磁盘上、但导出里没有它。

        现在统一走这一个入口，「算目录」与「登记目录」用同一个值。
        守护见 ``tests/test_gui_collect.py`` 的
        ``test_collect_without_out_dir_still_registers_sources``（回退修复即变红）。
        """
        if self._state.out_dir is None:
            self._state.out_dir = default_output_dir()

    def _on_collect_done(self, summary: str) -> None:
        """采集成功：显示计数，并把产物目录登记进向导的来源列表。

        采集结果**并入来源**而不是直接导出——用户仍走第 3 步的正常导出流程
        （选择目标平台 / 守门 / 指引），这样三步流程的语义保持一致。
        """
        self._collect_thread = None
        self._collect_progress.setVisible(False)
        self._collect_status.setText(summary)
        # 与 _start_collect 用**同一个**入口算目录——两处各算各的正是上面
        # 那个 bug 的根因（一个用 `or default_output_dir()` 兜底、一个直接用）。
        self._ensure_out_dir()
        collect_dir = self._state.out_dir / "douyin"
        if collect_dir.is_dir():
            if collect_dir not in self._state.sources:
                self._state.sources.append(collect_dir)
            self.completeChanged.emit()
        else:
            # 静默失败是这类 bug 最难受的地方：用户看到「采集完成」，
            # 却在导出目录里找不到它。必须明确说出来。
            self._collect_status.setText(summary + "\n（未找到采集产物目录，请重试）")

    def _on_collect_failed(self, reason: str) -> None:
        """采集失败：给出**中文可读**的原因，并恢复按钮可用。

        ``reason`` 是不透明短码或业务消息——隐私铁律：不得靠近 cookie 值。
        """
        self._collect_thread = None
        self._collect_progress.setVisible(False)
        self._collect_status.setText(reason)
        self._refresh_collect_button()

    # -- 微信表情导出（EM-003）---------------------------------------------------

    def _start_weixin_export(self) -> None:
        """启动微信表情导出（后台线程，UI 不卡）。"""
        if self._wx_thread is not None:
            return
        self._ensure_out_dir()
        target = self._state.out_dir / "weixin"

        self._wx_button.setEnabled(False)
        self._wx_hint.setText("正在导出，请勿关闭微信（通常 1~5 分钟）…")

        thread = _WeixinExportThread(target)
        thread.stage.connect(self._wx_hint.setText)
        thread.finished_ok.connect(self._on_weixin_done)
        thread.failed.connect(self._on_weixin_failed)
        self._wx_thread = thread
        thread.start()

    def _on_weixin_done(self, summary: str) -> None:
        """导出成功：显示摘要，并把产物目录并入来源（走正常导出流程）。"""
        self._wx_thread = None
        self._wx_button.setEnabled(True)
        self._wx_hint.setText(summary)
        target = self._state.out_dir / "weixin" if self._state.out_dir else None
        if target is not None and target.is_dir() and target not in self._state.sources:
            self._state.sources.append(target)
            self.completeChanged.emit()

    def _on_weixin_failed(self, reason: str) -> None:
        """导出失败：中文原因 + 恢复按钮（不静默）。"""
        self._wx_thread = None
        self._wx_button.setEnabled(True)
        self._wx_hint.setText(reason)

    def _open_douyin_login(self) -> None:
        """弹出扫码登录窗口；持有引用防止被垃圾回收，完成后刷新按钮文案。"""
        self._auth_window = DouyinAuthWindow(self)
        self._auth_window.login_succeeded.connect(self._refresh_douyin_login_button)
        self._auth_window.login_abandoned.connect(self._on_douyin_login_abandoned)
        self._auth_window.show()

    def _on_douyin_login_abandoned(self, reason: str) -> None:
        """放弃 / 失败：本地缓存状态未变，按实况刷新文案即可。

        ``reason`` 是不透明短码（timeout / user-closed / save-failed）——
        隐私铁律：任何细节都不得靠近 cookie 值，UI 层只认短码。
        """
        self._refresh_douyin_login_button()
        if reason == "timeout":
            self._douyin_hint.setText("登录超时（5 分钟未完成扫码），可重新点击按钮再试。")


class _ExportPage(QWizardPage):
    """第 3 步：导出 + 打开目录 + 图文指引。"""

    def __init__(self, state: WizardState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setTitle("第 3 步：导出并查看导入指引")
        self.setSubTitle("导出的是修正后的副本，你的原文件一个字节都不会被改动")

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._out_label = QLabel()
        self._change_out_button = QPushButton("更改导出目录…")
        self._change_out_button.clicked.connect(self._choose_out_dir)
        self._export_button = QPushButton("开始导出")
        self._export_button.clicked.connect(self._export)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._open_button = QPushButton("打开导出目录")
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._open_out_dir)
        self._guide_button = QPushButton("查看导入指引")
        self._guide_button.setEnabled(False)
        self._guide_button.clicked.connect(self._show_guide)

        out_row = QHBoxLayout()
        out_row.addWidget(self._out_label, stretch=1)
        out_row.addWidget(self._change_out_button)

        done_row = QHBoxLayout()
        done_row.addWidget(self._open_button)
        done_row.addWidget(self._guide_button)
        done_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._summary)
        layout.addLayout(out_row)
        layout.addWidget(self._export_button)
        layout.addWidget(self._log, stretch=1)
        layout.addLayout(done_row)

    def initializePage(self) -> None:
        if self._state.out_dir is None:
            self._state.out_dir = default_output_dir()
        self._out_label.setText(f"导出目录：{self._state.out_dir}")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        count = len(self._state.sources)
        self._summary.setText(
            f"将导出 {count} 个来源 → 平台：{self._state.platform.value}"
            f"｜质量档位：{self._state.tier.value}"
        )

    def _choose_out_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if directory:
            self._state.out_dir = Path(directory)
            self._out_label.setText(f"导出目录：{self._state.out_dir}")

    def _export(self) -> None:
        out_dir = self._state.out_dir or default_output_dir()
        self._state.out_dir = out_dir
        self._export_button.setEnabled(False)
        try:
            code, log = run_export(
                self._state.sources, out_dir, self._state.platform, self._state.tier
            )
        except (OSError, ValueError) as exc:
            self._log.setPlainText(f"导出失败：{exc}")
            self._export_button.setEnabled(True)
            return
        self._state.export_code = code
        self._state.export_log = log
        self._log.setPlainText(log)
        ok = code == 0
        self._open_button.setEnabled(ok)
        self._guide_button.setEnabled(ok)
        self._export_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(
                self,
                "导出完成但守门未通过",
                "正确性守门未通过，请查看日志；已产出的文件不建议导入。",
            )

    def _open_out_dir(self) -> None:
        if self._state.out_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._state.out_dir)))

    def _show_guide(self) -> None:
        dialog = GuideDialog(guide_for(self._state.platform), parent=self)
        dialog.open()


class GuideDialog(QDialog):
    """图文导入指引窗口：编号步骤 + 随包截图（截图文件名见 :data:`GUIDES`）。"""

    def __init__(
        self,
        steps: Sequence[GuideStep],
        title: str = "导入指引",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 680)

        content = QWidget()
        inner = QVBoxLayout(content)
        for index, step in enumerate(steps, start=1):
            header = QLabel(f"第 {index} 步：{step.title}")
            header.setStyleSheet("font-weight: bold;")
            inner.addWidget(header)
            detail = QLabel(step.detail)
            detail.setWordWrap(True)
            inner.addWidget(detail)
            if step.image is not None and step.image.is_file():
                pixmap = QPixmap(str(step.image))
                if not pixmap.isNull():
                    shot = QLabel()
                    shot.setPixmap(
                        pixmap.scaledToWidth(700, Qt.TransformationMode.SmoothTransformation)
                    )
                    inner.addWidget(shot)
        inner.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        close_button = QPushButton("知道了")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(close_button)


class WizardWindow(QWizard):
    """三步导入向导窗口（选来源 → 选平台 → 导出 + 指引）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("表情包互通 · 导入向导")
        self._state = WizardState()
        self.addPage(_SourcePage(self._state, self))
        self.addPage(_PlatformPage(self._state, self))
        self.addPage(_ExportPage(self._state, self))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

    @property
    def state(self) -> WizardState:
        """向导共享状态（测试与截图脚本用）。"""
        return self._state


def main() -> int:
    """GUI 入口（供 EM-006 打包后使用；源码形态也可 ``python -m emoji_migrator.gui``）。"""
    app = QApplication.instance() or QApplication(sys.argv)
    window = WizardWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


