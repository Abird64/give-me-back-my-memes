"""WebEngine 前端壳（EM-011 新增）：把《把我的表情还回来》的 HTML 界面装进桌面窗口。

**为什么是 WebEngine 而不是纯 QSS 重做**（落地裁决，2026-10-21 人工确认）：

本产品的视觉语言是**孟菲斯 / 波普拼贴**——纯黑 3px 描边、**硬边偏移阴影（无模糊）**、
文字描边（``-webkit-text-stroke``）、半调网点背景、斜置拼贴、自定义展示字体。
这几样在 Qt Style Sheets 里**几乎无法实现**（QSS 不支持伪元素样式、渐变网点、
``text-stroke``、外部字体打包）。HTML/CSS 一行就够，故采用
**PySide6 壳 + QWebEngineView 承载**。

依赖已就位：``douyin_auth``（EM-005-B）已经在用 ``QWebEngineView`` 打开抖音登录页，
PySide6 6.12.0 的 ``QtWebEngineWidgets`` / ``QtWebEngineCore`` 实测均可导入。

**架构**::

    webui.WebWindow  (QMainWindow)
      └─ QWebEngineView
           ├─ 页面 = 包内 webui/index.html（qrc: 走 ``qrc:/`` 或磁盘路径）
           └─ QWebChannel 桥 = WebBridge(QObject)
                ├─ collect_weixin() / collect_qq() / collect_douyin()
                ├─ export_selected(platform, items) -> 摘要
                └─ open_dir() / show_guide(platform)

**边界（本模块只做壳与桥，不实现业务算法）**：

- 采集：复用 :mod:`emoji_migrator.wxcollect`（微信）/ :mod:`emoji_migrator.qqdir`（QQ）
  / :mod:`emoji_migrator.collect_flow`（抖音），**不重写**；
- 导出：复用 :func:`emoji_migrator.cli._normalize_dir`（EM-002 的受测编排），
  与旧 GUI 走**同一条**管线，输出目录与用户原文件完全隔离；
- 线程：所有耗时操作走 ``QThread``，**绝不在 UI 线程做**（EM-005-D BL-13 实测记录：
  UI 同步执行采集会卡几十秒；微信导出整轮 289 秒）。

依赖方向（``AGENTS.md`` §3）：本模块在业务模块**之上**做编排，
``webui → {cli, collect_flow, wxcollect, qqdir, douyin_auth, config, platforms}``；
业务模块不得 import 本模块，故箭头单向、无环。

隐私铁律：任何回传给前端的文案**不得包含 cookie 值或密钥片段**。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from emoji_migrator import cli
from emoji_migrator.collect_flow import NO_COOKIE_HINT, collect_douyin_stickers
from emoji_migrator.config import PACKAGE_ROOT
from emoji_migrator.douyin_auth import (
    DouyinAuthWindow,
    has_cached_session,
    load_cached_cookie,
)
from emoji_migrator.douyin_collect import SessionExpired
from emoji_migrator.platforms import Platform
from emoji_migrator.qqdir import NOT_FOUND_MESSAGE, detect_qq_emoji_dirs

__all__ = [
    "APP_TITLE",
    "WEBUI_DIR",
    "WebBridge",
    "WebWindow",
    "index_url",
    "main",
]

#: 窗口与产品标题（正式产品名，中文用书名号形式由 HTML 呈现）。
APP_TITLE = "把我的表情还回来"

#: 前端资源目录（随包发布）。
WEBUI_DIR = Path(PACKAGE_ROOT) / "webui"


def index_url() -> QUrl:
    """前端页面 URL。

    优先用 ``qrc:``（打进 exe 后仍可用）；未打包时回落到包内磁盘路径。
    两种情形下都**必须**是本地资源——不联网。
    """
    qrc = Path(":/emoji_migrator/webui/index.html")
    if qrc.exists():
        return QUrl.fromLocalFile(str(qrc).replace("\\", "/"))
    disk = WEBUI_DIR / "index.html"
    return QUrl.fromLocalFile(str(disk))


class _CollectThread(QThread):
    """抖音采集后台线程（网络请求与转换绝不在 UI 线程做）。

    沿用 EM-005-D 的结论：BL-13 实测过 UI 同步执行采集会卡几十秒。
    信号参数**一律是可读文案**，不含 cookie 值（隐私铁律）。
    """

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, out_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._out_dir = Path(out_dir)

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        # cookie 在**子线程内**读：load_cached_cookie 做 DPAPI 解密，不该占 UI 线程。
        cookies = load_cached_cookie()
        if not cookies:
            self.failed.emit(NO_COOKIE_HINT)
            return
        try:
            outcome = collect_douyin_stickers(cookies, self._out_dir)
        except SessionExpired:
            self.failed.emit("登录态已过期（服务端 401），请重新扫码登录。")
            return
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            self.failed.emit(f"采集失败：{type(exc).__name__}，请重试或换个时间再试。")
            return
        self.finished_ok.emit(self._summarize(outcome))

    @staticmethod
    def _summarize(outcome) -> str:
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
            parts.append(f"产物目录：{outcome.out_dir}")
        return "｜".join(parts)


class _WeixinThread(QThread):
    """微信表情导出后台线程。

    ⚠️ **必须走线程**：实测整轮 **289 秒**（扫内存 41 秒 + 1109 个 wxgf 转码），
    放 UI 线程会让窗口**整整 5 分钟无响应**。
    """

    finished_ok = Signal(str)
    failed = Signal(str)
    stage = Signal(str)

    def __init__(self, out_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._out_dir = Path(out_dir)

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        from emoji_migrator import wxcollect

        try:
            outcome = wxcollect.collect_weixin_emoticons(self._out_dir, progress=self.stage.emit)
        except wxcollect.WxError as exc:
            self.failed.emit(str(exc))
            return
        except OSError as exc:
            detail = exc.strerror or "文件暂时不可读或输出目录不可写"
            self.failed.emit(
                f"微信导出遇到系统文件错误：{detail}；请保持微信运行后重试。"
            )
            return
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            self.failed.emit(f"导出失败：{type(exc).__name__}，请重试。")
            return
        self.finished_ok.emit(self._summarize(outcome))

    @staticmethod
    def _summarize(outcome) -> str:
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


class _QQThread(QThread):
    """QQ 目录探测与归一化线程，避免同步桥接槽冻结页面。"""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, out_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._out_dir = Path(out_dir)

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        try:
            found = detect_qq_emoji_dirs()
            if not found:
                self.failed.emit(NOT_FOUND_MESSAGE)
                return
            self._out_dir.mkdir(parents=True, exist_ok=True)
            before = {p.name.casefold() for p in self._out_dir.iterdir() if p.is_file()}
            for source in found:
                if cli._normalize_dir(source, self._out_dir, Platform.QQ, True) != 0:
                    self.failed.emit("QQ 表情格式检查未通过，请查看导出结果。")
                    return
            total = sum(
                1 for p in self._out_dir.iterdir()
                if p.is_file() and p.name.casefold() not in before
            )
            self.finished_ok.emit(f"扫描到 {len(found)} 个 QQ 表情目录，已加入 {total} 个表情")
        except OSError as exc:
            detail = exc.strerror or "文件暂时不可读或输出目录不可写"
            self.failed.emit(
                f"QQ 扫描遇到系统文件错误：{detail}；"
                "请关闭正在修改表情的 QQ 窗口后重试。"
            )
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            self.failed.emit(f"QQ 扫描失败：{type(exc).__name__}，请重试。")


class _GalleryThread(QThread):
    """后台建立相册索引并生成轻量静态缩略图。"""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, root_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root_dir = Path(root_dir)

    @staticmethod
    def _thumbnail(path: Path, thumb_dir: Path, relative: Path) -> Path:
        """为动图只解码首帧，避免相册同时播放数百个 GIF。"""
        import hashlib

        from PIL import Image

        try:
            stat = path.stat()
            key = hashlib.sha256(
                f"{relative.as_posix()}|{stat.st_size}|{stat.st_mtime_ns}".encode()
            ).hexdigest()
            target = thumb_dir / f"{key}.png"
            if target.is_file():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(path) as image:
                if getattr(image, "n_frames", 1) > 1:
                    image.seek(0)
                has_alpha = "transparency" in image.info or "A" in image.getbands()
                image = image.convert("RGBA" if has_alpha else "RGB")
                image.thumbnail((160, 160), Image.Resampling.LANCZOS)
                image.save(target, "PNG", optimize=True)
            return target
        except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError):
            # 缩略图失败不应让一张坏图阻塞整个相册；前端回退到原文件。
            return path

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        if not self._root_dir.is_dir():
            self.finished_ok.emit("[]")
            return
        try:
            labels = {"weixin": "微信", "qq": "QQ", "douyin": "抖音"}
            thumb_dir = self._root_dir.parent / "thumbs"
            items = []
            paths = cli.collect_images(self._root_dir)
            total = len(paths)
            for index, path in enumerate(paths, 1):
                relative = path.relative_to(self._root_dir)
                thumb = self._thumbnail(path, thumb_dir, relative)
                source = relative.parts[0] if relative.parts else ""
                items.append(
                    {
                        "src": QUrl.fromLocalFile(str(thumb)).toString(),
                        "original": QUrl.fromLocalFile(str(path)).toString(),
                        "rel": relative.as_posix(),
                        "name": path.name,
                        "plat": labels.get(source, "其他"),
                        "cat": "未分类",
                    }
                )
                if total and index % 24 == 0:
                    self.msleep(1)
            self.finished_ok.emit(json.dumps(items, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            self.failed.emit(f"相册读取失败：{type(exc).__name__}，请重试。")


class _ExportThread(QThread):
    """只导出用户选中的相册项目，并把归一化放到后台。"""

    progress = Signal(int, str)
    finished_ok = Signal(str, int)
    failed = Signal(str)

    def __init__(
        self,
        source_base: Path,
        target_dir: Path,
        platform: Platform,
        selected: list[str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_base = Path(source_base)
        self._target_dir = Path(target_dir)
        self._platform = platform
        self._selected = selected

    def run(self) -> None:  # noqa: D102 - QThread 覆写点
        source_root = self._source_base / "collected"
        target = self._target_dir
        if not source_root.is_dir():
            self.failed.emit("还没有可导出的表情，先去第 1 步捞一些。")
            return
        if not self._selected:
            self.failed.emit("还没有选择表情。")
            return
        try:
            with tempfile.TemporaryDirectory(prefix="emoji-migrator-selected-") as temp:
                staged = Path(temp) / "source"
                valid = []
                for index, relative_name in enumerate(self._selected, 1):
                    relative = Path(relative_name)
                    candidate = (source_root / relative).resolve()
                    try:
                        candidate.relative_to(source_root.resolve())
                    except ValueError:
                        continue
                    if not candidate.is_file() or not cli.is_image_suffix(candidate):
                        continue
                    destination = staged / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(candidate, destination)
                    valid.append(destination)
                    self.progress.emit(
                        min(30, round(index * 30 / len(self._selected))),
                        f"准备 {index}/{len(self._selected)} 个表情",
                    )
                if not valid:
                    self.failed.emit("选中的表情已经不存在，请刷新相册后重试。")
                    return
                target.mkdir(parents=True, exist_ok=True)
                self.progress.emit(35, f"正在整理 {len(valid)} 个表情…")
                with redirect_stdout(StringIO()):
                    code = cli._normalize_dir(staged, target, self._platform, True)
                count = sum(1 for path in target.iterdir() if path.is_file())
                self.progress.emit(100, "整理完成")
                if code:
                    self.finished_ok.emit(
                        "导出完成，但有文件没有通过正确性检查，请查看结果。",
                        code,
                    )
                else:
                    self.finished_ok.emit(f"导出完成：{count} 个文件", code)
        except Exception as exc:  # noqa: BLE001 - 线程边界必须兜住一切
            self.failed.emit(f"导出失败：{type(exc).__name__}，请重试。")


#: 平台图标对应的来源目录名（与后端产物目录约定一致）。
_PLATFORM_BY_LABEL: dict[str, Platform] = {
    "微信": Platform.WECHAT,
    "QQ": Platform.QQ,
    "抖音": Platform.DOUYIN,
}


class WebBridge(QObject):
    """暴露给前端页面的槽（QWebChannel）。

    命名一律 ``snake_case`` 动词短语，前端用 ``window.bridge.xxx()`` 调用。
    **所有返回值都是给用户看的中文文案**，不含路径以外的机器细节，
    更**绝不含** cookie 值或密钥片段（隐私铁律）。
    """

    def __init__(self, window: WebWindow) -> None:
        super().__init__(window)
        self._window = window
        self._collect_thread: _CollectThread | None = None
        self._wx_thread: _WeixinThread | None = None
        self._qq_thread: _QQThread | None = None
        self._gallery_thread: _GalleryThread | None = None
        self._export_thread: _ExportThread | None = None
        self._auth_window: DouyinAuthWindow | None = None
        self._session_out_dir: Path | None = None
        self._session_temp: tempfile.TemporaryDirectory | None = None

    # -- 采集 ------------------------------------------------------------------

    @Slot(result=str)
    def scan_qq(self) -> str:
        """扫描本机 QQ 表情目录（只读探测）。

        返回中文摘要；**不自动加入任何来源**——由用户在前端点选。
        """
        found = detect_qq_emoji_dirs()
        if not found:
            return NOT_FOUND_MESSAGE
        return f"扫描到 {len(found)} 个 QQ 表情目录"

    @Slot(str, result=str)
    def collect_qq(self, out_dir: str) -> str:
        """扫描 QQ 目录并把图片归一化到相册目录（后台线程，秒回）。"""
        if self._qq_thread is not None:
            return "QQ 扫描正在进行中。"
        thread = _QQThread(Path(out_dir), parent=self)
        thread.finished_ok.connect(self._on_qq_ok)
        thread.failed.connect(self._on_qq_failed)
        self._qq_thread = thread
        thread.start()
        return "开始扫描 QQ 表情目录…"

    def _on_qq_ok(self, summary: str) -> None:
        self._qq_thread = None
        self._window.emit_to_page("qqDone", summary)

    def _on_qq_failed(self, reason: str) -> None:
        self._qq_thread = None
        self._window.emit_to_page("qqFailed", reason)

    @Slot(str, result=str)
    def gallery_items(self, root_dir: str) -> str:
        """兼容旧调用方的同步相册清单接口。

        正式 WebEngine 前端使用 :meth:`gallery_items_async`，避免扫描和缩略图生成
        占用桥接所属的 UI 线程。
        """
        root = Path(root_dir) / "collected"
        if not root.is_dir():
            return "[]"
        labels = {"weixin": "微信", "qq": "QQ", "douyin": "抖音"}
        items = []
        for path in cli.collect_images(root):
            relative = path.relative_to(root)
            source = relative.parts[0] if relative.parts else ""
            items.append({
                "src": QUrl.fromLocalFile(str(path)).toString(),
                "name": path.name,
                "plat": labels.get(source, "其他"),
                "cat": "未分类",
            })
        return json.dumps(items, ensure_ascii=False)

    @Slot(str, result=str)
    def gallery_items_async(self, root_dir: str) -> str:
        """后台建立相册清单；结果通过 ``galleryDone`` 事件返回。"""
        if self._gallery_thread is not None:
            return "相册正在读取中。"
        thread = _GalleryThread(Path(root_dir) / "collected", parent=self)
        thread.finished_ok.connect(self._on_gallery_ok)
        thread.failed.connect(self._on_gallery_failed)
        self._gallery_thread = thread
        thread.start()
        return "开始读取相册…"

    def _on_gallery_ok(self, payload: str) -> None:
        self._gallery_thread = None
        self._window.emit_to_page("galleryDone", payload)

    def _on_gallery_failed(self, reason: str) -> None:
        self._gallery_thread = None
        self._window.emit_to_page("galleryFailed", reason)

    @Slot(result=str)
    def douyin_login_state(self) -> str:
        """抖音登录态（**只做本地判断**，不联网、不自动弹窗）。"""
        return "已登录" if has_cached_session() else "未登录"

    @Slot()
    def douyin_login(self) -> None:
        """弹出抖音扫码登录窗口（EM-005-B 已实现的 :class:`DouyinAuthWindow`）。

        **为什么必须有这个独立入口**（2026-10-21 修正）：原先前端把「登录」和
        「采集」塞进同一个按钮，导致未登录时点按钮只得到一行提示文字、
        **永远不会弹出扫码窗**（本模块压根没有暴露这个槽）；而已登录时又
        直接开采集，用户完全看不到发生了什么。旧``gui.py`` 里这两个本来就是
        两个独立控件，现在前端恢复同样的分离：登录一个按钮、采集另一个。

        :class:`DouyinAuthWindow` 打开即清本应用 profile 的 cookie（见该类
        文档字符串：既有 cookie 既读不回也不补发），因此**别在不必要时反复点**。
        登录成功后 emit ``douyinLogined`` 让前端把采集按钮解锁。
        """
        if self._auth_window is not None:
            self._auth_window.raise_()
            self._auth_window.activateWindow()
            return
        self._auth_window = DouyinAuthWindow()
        self._auth_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._auth_window.login_succeeded.connect(self._on_douyin_logined)
        self._auth_window.login_abandoned.connect(self._on_douyin_login_abandoned)
        self._auth_window.destroyed.connect(self._on_douyin_auth_window_destroyed)
        self._auth_window.show()

    def _on_douyin_logined(self) -> None:
        self._window.emit_to_page("douyinLogined", "登录成功，可以采集了。")

    def _on_douyin_login_abandoned(self, reason: str) -> None:
        """放弃 / 失败：``reason`` 是不透明短码，隐私铁律下 UI 层不解释细节。"""
        self._window.emit_to_page(
            "douyinLoginAbandoned", "这次没登录上，可以再点一次「扫码登录」。"
        )

    def _on_douyin_auth_window_destroyed(self) -> None:
        self._auth_window = None

    @Slot(str, result=str)
    def collect_douyin(self, out_dir: str) -> str:
        """采集抖音收藏表情到 ``out_dir``（后台线程，秒回）。

        登录与采集**严格分离**：本槽只管采集，没登录就返回提示，
        绝不代替用户去弹扫码窗（登录由 :meth:`douyin_login` 独立负责）。
        """
        if not has_cached_session():
            return "还没登录抖音，先点上面的「扫码登录」。"
        if self._collect_thread is not None:
            return "采集正在进行中。"
        target = Path(out_dir)
        thread = _CollectThread(target, parent=self)
        thread.finished_ok.connect(self._on_collect_ok)
        thread.failed.connect(self._on_collect_failed)
        self._collect_thread = thread
        thread.start()
        return "开始采集抖音收藏表情…"

    def _on_collect_ok(self, summary: str) -> None:
        self._collect_thread = None
        self._window.emit_to_page("collectDone", summary)

    def _on_collect_failed(self, reason: str) -> None:
        self._collect_thread = None
        self._window.emit_to_page("collectFailed", reason)

    @Slot(str, result=str)
    def export_weixin(self, out_dir: str) -> str:
        """导出微信表情到 ``out_dir``（后台线程，秒回）。"""
        if self._wx_thread is not None:
            return "微信导出正在进行中。"
        target = Path(out_dir)
        thread = _WeixinThread(target, parent=self)
        thread.stage.connect(lambda s: self._window.emit_to_page("weixinStage", s))
        thread.finished_ok.connect(self._on_weixin_ok)
        thread.failed.connect(self._on_weixin_failed)
        self._wx_thread = thread
        thread.start()
        return "开始导出微信表情，请勿关闭微信（约 1~5 分钟）…"

    def _on_weixin_ok(self, summary: str) -> None:
        self._wx_thread = None
        self._window.emit_to_page("weixinDone", summary)

    def _on_weixin_failed(self, reason: str) -> None:
        self._wx_thread = None
        self._window.emit_to_page("weixinFailed", reason)

    # -- 导出 ------------------------------------------------------------------

    @Slot(str, str, result=str)
    def export_memes(self, out_dir: str, platform: str) -> str:
        """导出选中的表情到 ``out_dir``，目标端 ``platform``。

        复用 :func:`emoji_migrator.cli._normalize_dir`——与旧 GUI、命令行
        **同一条**管线（嗅探 → 修后缀 → 去重 → 正确性守门）。
        退出码口径与 CLI 一致：``0`` = 守门通过，``1`` = 有违规。

        ⚠️ **来源与输出必须是两个目录**（``_normalize_dir`` 的入参语义：
        ``(source, out_dir, ...)``）。采集产物落在 ``<root>/<平台>/``，
        导出目标落在 ``<root>/export/``——绝不让输出目录等于输入目录，
        否则管线会自我覆盖。
        """
        try:
            plat = _PLATFORM_BY_LABEL.get(platform)
            if plat is None:
                plat = Platform(platform)     # 也接受英文标识
        except ValueError:
            return "不认识的目标端。"

        root = Path(out_dir)
        source = root / "collected"
        target = root / "export"
        if not source.is_dir():
            return "还没有可导出的表情，先去第 1 步捞一些。"
        target.mkdir(parents=True, exist_ok=True)

        try:
            code = cli._normalize_dir(source, target, plat, True)
        except (OSError, ValueError) as exc:
            return f"导出失败：{exc}"
        if code != 0:
            return "导出完成但正确性守门未通过，请查看日志；已产出的文件不建议导入。"
        count = sum(1 for _ in target.iterdir())
        return f"导出完成：{target}（{count} 个文件）"

    @Slot(str, str, str, str, result=str)
    def export_selected(
        self, out_dir: str, platform: str, selected_json: str, export_dir: str
    ) -> str:
        """后台导出选中的相册项目，进度通过 ``exportProgress`` 返回。"""
        try:
            plat = _PLATFORM_BY_LABEL.get(platform) or Platform(platform)
            selected = json.loads(selected_json)
            if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
                raise ValueError("选择清单格式不正确")
        except (TypeError, ValueError, json.JSONDecodeError):
            return "选择清单格式不正确。"
        if self._export_thread is not None:
            return "导出正在进行中。"
        target = Path(export_dir)
        source_root = (Path(out_dir) / "collected").resolve()
        try:
            target.resolve().relative_to(source_root)
        except ValueError:
            pass
        else:
            return "导出目录不能放在相册来源目录里面。"
        thread = _ExportThread(Path(out_dir), target, plat, selected, parent=self)
        thread.progress.connect(self._on_export_progress)
        thread.finished_ok.connect(self._on_export_ok)
        thread.failed.connect(self._on_export_failed)
        self._export_thread = thread
        thread.start()
        return "开始导出…"

    def _on_export_progress(self, percent: int, message: str) -> None:
        self._window.emit_to_page("exportProgress", json.dumps({
            "percent": percent,
            "message": message,
        }, ensure_ascii=False))

    def _on_export_ok(self, summary: str, code: int) -> None:
        self._export_thread = None
        self._window.emit_to_page("exportDone" if code == 0 else "exportFailed", summary)

    def _on_export_failed(self, reason: str) -> None:
        self._export_thread = None
        self._window.emit_to_page("exportFailed", reason)

    @Slot(str)
    def open_dir(self, path: str) -> None:
        """在系统文件管理器里打开目录。"""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(result=str)
    def choose_export_dir(self) -> str:
        """弹出系统目录选择器；取消时返回空字符串。"""
        return QFileDialog.getExistingDirectory(
            self._window,
            "选择表情导出目录",
            str(Path.home()),
        )

    @Slot(result=str)
    def default_out_dir(self) -> str:
        """返回本次会话的临时工作区。

        采集结果只是相册与导出的中间材料，不应该按启动次数永久堆在用户的
        ``C:\\Users\\...`` 下。使用系统临时目录，并在窗口关闭时由
        :meth:`cleanup_session` 清理；用户真正要保留的文件必须通过第 3 步导出。
        """
        if self._session_out_dir is None:
            self._session_temp = tempfile.TemporaryDirectory(prefix="emoji-migrator-session-")
            self._session_out_dir = Path(self._session_temp.name) / "workspace"
        return str(self._session_out_dir)

    def cleanup_session(self) -> None:
        """删除本次会话的采集结果、缩略图和临时中间文件。"""
        if self._session_temp is not None:
            self._session_temp.cleanup()
            self._session_temp = None
            self._session_out_dir = None

    @Slot(result=str)
    def platform_rules(self) -> str:
        """三个目标端的格式规则（给前端的悬浮说明用）。"""
        return (
            "微信｜支持 GIF / PNG｜自动修正后缀与尺寸，动图保留帧数与循环播放。\n"
            "QQ｜支持 GIF / PNG｜感知哈希去重，缩略图由 QQ 客户端自己生成。\n"
            "抖音｜支持 GIF｜WebP 自动转码，透明区域合成白底避免黑边。"
        )


class WebWindow(QMainWindow):
    """《把我的表情还回来》主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        # 必须在 QApplication 之前设好——但本模块可能被先 import 再建 QApplication，
        # 故在模块导入时就设一次（见 ``_enable_software_rendering``）。
        _enable_software_rendering()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 900)
        self.setMinimumSize(960, 680)

        self._view = QWebEngineView(self)
        self.setCentralWidget(self._view)

        self._bridge = WebBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)

        self._view.load(index_url())

    def emit_to_page(self, channel: str, payload: str) -> None:
        """把一条消息推给前端页面（前端监听 ``window.bridgeEvent``）。"""
        # JSON 字符串字面量比手写替换更完整，能正确处理引号、反斜杠和特殊 Unicode。
        script = (
            "window.dispatchEvent(new CustomEvent('bridgeEvent',"
            f"{{detail:{{channel:{json.dumps(channel, ensure_ascii=False)},"
            f"payload:{json.dumps(payload, ensure_ascii=False)}}}}}))"
        )
        self._view.page().runJavaScript(script)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关闭前确认：正在采集/导出时问一句，避免白跑。"""
        bridge = self._bridge
        busy = (
            bridge._collect_thread is not None
            or bridge._wx_thread is not None
            or bridge._qq_thread is not None
            or bridge._gallery_thread is not None
            or bridge._export_thread is not None
        )
        if busy:
            reply = QMessageBox.question(
                self,
                "还在跑",
                "有任务正在执行（采集或导出），现在关掉会中断它。\n确定要关吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        # 不让每次启动产生的采集目录长期留在用户 C 盘。先等后台线程结束，
        # 再删临时目录，避免 Windows 下仍有文件句柄时清理失败。
        for thread in (
            bridge._collect_thread,
            bridge._wx_thread,
            bridge._qq_thread,
            bridge._gallery_thread,
            bridge._export_thread,
        ):
            if thread is not None and thread.isRunning():
                thread.wait()
        bridge.cleanup_session()
        event.accept()


def _js_str(text: str) -> str:
    """把 Python 字符串安全地转成 JS 字面量。

    只转义反斜杠、引号与换行——不引入 ``json`` 以免多一个 import。
    """
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _enable_software_rendering() -> None:
    """配置 WebEngine 渲染模式，默认保留硬件加速。"""
    raw_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    disabled = {
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-software-rasterizer",
    }
    flags = " ".join(token for token in raw_flags.split() if token not in disabled)
    if os.environ.get("EMOJI_MIGRATOR_FORCE_SOFTWARE") == "1":
        flags = f"{flags} --disable-gpu --disable-gpu-compositing".strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags


def main() -> int:
    """GUI 入口（供 EM-006 打包后使用；源码形态也可 ``python -m emoji_migrator.webui``）。"""
    from PySide6.QtWidgets import QApplication

    _enable_software_rendering()
    app = QApplication.instance() or QApplication(sys.argv)
    window = WebWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
