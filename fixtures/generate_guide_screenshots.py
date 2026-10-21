"""EM-004 指引截图生成器：用向导自身窗口的 offscreen 抓取当步骤示意图。

为什么用工具自己的窗口：EM-004 阶段的「图文指引」只需要**可操作**的示意图，
真实微信 / QQ 客户端截图属 EM-006 范围。截图以 PNG 随包发布在
``src/emoji_migrator/guides/<平台>/stepN.png``（文件名由 ``gui.GUIDES`` 枚举，
验收标准「截图文件名可枚举」即由该数据结构保证），另存一份到
``docs/em004-gui-evidence/`` 作验收证据。

导出的演示数据是 Pillow 现画的小合成集（不碰任何用户真实表情）。

用法（仓库根，Git Bash）：
    .venv/Scripts/python.exe fixtures/generate_guide_screenshots.py
"""

from __future__ import annotations

import contextlib
import os
import random
import sys
import tempfile
from pathlib import Path

#: Qt 在 QApplication 构造时读 QT_QPA_PLATFORM，必须先于 PySide6 import 设置。
os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUIDES_DIR = PROJECT_ROOT / "src" / "emoji_migrator" / "guides"
EVIDENCE_DIR = PROJECT_ROOT / "docs" / "em004-gui-evidence"

#: sys.path 兜底：正常情况下 emoji_migrator 已 editable 安装；换环境时退回 src 布局。
_SRC = str(PROJECT_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PIL import Image, ImageDraw  # noqa: E402

from PySide6.QtGui import QFont, QFontDatabase, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidgetItem  # noqa: E402

from emoji_migrator import gui  # noqa: E402
from emoji_migrator.platforms import Platform  # noqa: E402

#: 第 1 步截图里展示的**占位**路径（刻意不用真实 QQ 号/真实路径：隐私）。
DEMO_SOURCE_LABEL = "D:\\QQ\\聊天消息\\Tencent Files\\你的QQ号\\nt_qq\\nt_data\\Emoji\\personal_emoji\\Ori"
DEMO_DROP_LABEL = "D:\\我的表情\\QQ收藏"

#: offscreen 平台下字体库可能整个是空的（实测 ``families() == 0``，中文全成豆腐块），
#: 需要显式加载系统中文字体。真实显示环境下字体库天然可用，此函数是截图专用补丁。
_CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)


def _ensure_cjk_font(app: QApplication) -> str | None:
    """确保有中文字体可用；返回实际选用的字体族名（无需补救时返回 None）。"""
    if QFontDatabase.families():
        return None
    for candidate in _CJK_FONT_CANDIDATES:
        if not Path(candidate).is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(candidate)
        if font_id == -1:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 9))
            return families[0]
    return None


@contextlib.contextmanager
def _sanitized_temp():
    """把系统临时区指到 ``build/`` 下的假目录。

    为什么：第 3 步导出日志会原样打印来源与暂存目录（均 ``tempfile`` 家族，
    见 :func:`emoji_migrator.gui.stage_sources`），系统临时区落在本机用户名
    下（``%TEMP%``）。随包截图若带真实路径即泄露隐私，故演示全程改走
    ``build/em004-shot-sanitize/temp``（``build/`` 已在 .gitignore，不污染
    仓库）。``tempfile.tempdir`` 与 ``TEMP``/``TMP``/``TMPDIR`` 一起改：前者
    被 ``tempfile.gettempdir()`` 优先采用，后者兜底直读环境变量的库。
    """
    fake_temp = PROJECT_ROOT / "build" / "em004-shot-sanitize" / "temp"
    fake_temp.mkdir(parents=True, exist_ok=True)
    original_tempdir = tempfile.tempdir
    original_env = {key: os.environ.get(key) for key in ("TEMP", "TMP", "TMPDIR")}
    try:
        tempfile.tempdir = str(fake_temp)
        os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = str(fake_temp)
        yield fake_temp
    finally:
        tempfile.tempdir = original_tempdir
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def _sanitized_home():
    """把 HOME 指到 ``build/`` 下的假目录（TraceMemo 禁用态截图同一手法）。

    为什么：第 3 步的默认导出目录是 ``Path.home()/emoji-migrator-out/<时间戳>``
    （:func:`emoji_migrator.gui.default_output_dir`），会原样出现在导出日志里。
    只包住第 3 步导出段：第 1 步 TraceMemo 按钮的启用态取决于真实 HOME 下
    ``Documents\\TraceMemo\\Emojis`` 是否存在，假 HOME 会把它误绘成禁用态、
    改变原截图语义（禁用态另由 ``wizard-page1-tracememo-disabled.png`` 专门
    呈现）。
    """
    fake_home = PROJECT_ROOT / "build" / "em004-shot-sanitize" / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    original_home = Path.home
    try:
        Path.home = classmethod(lambda cls: fake_home)  # type: ignore[assignment]
        yield fake_home
    finally:
        Path.home = original_home  # type: ignore[assignment]


def _make_demo_source(root: Path) -> Path:
    """现画一个小合成集：动图（错乱后缀）、PNG（错乱后缀）、JPEG（后缀正确）。

    三张图刻意做得**视觉差异足够大**：演示截图里若出现「去重移除」，会让人
    误以为工具在乱删文件；噪声/斜纹/纯色块的感知哈希两两相距很远。
    """
    source = root / "demo-emoji"
    source.mkdir(parents=True)

    frames = []
    for index, color in enumerate(((30, 90, 200), (200, 90, 30))):
        frame = Image.new("RGB", (96, 96), color)
        ImageDraw.Draw(frame).ellipse(
            (10 + index * 20, 10, 90 - index * 20, 90), fill=(255, 255, 0)
        )
        frames.append(frame)
    frames[0].save(source / "demo_anim.jpg", format="GIF", save_all=True, append_images=frames[1:])

    png = Image.new("RGB", (120, 80), (60, 180, 90))
    draw = ImageDraw.Draw(png)
    for x in range(-80, 130, 12):
        draw.line((x, 0, x + 80, 80), fill=(20, 60, 30), width=4)
    png.save(source / "demo_pic.jpg", format="PNG")

    noise = Image.frombytes("RGB", (100, 100), random.Random(42).randbytes(100 * 100 * 3))
    ImageDraw.Draw(noise).ellipse((20, 20, 80, 80), outline=(255, 255, 255), width=6)
    noise.save(source / "demo_photo.jpg", format="JPEG", quality=92)
    return source


def _grab(widget) -> QPixmap:
    widget.repaint()
    QApplication.processEvents()
    return widget.grab()


def _save(pixmap: QPixmap, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"截图保存失败：{path}")
    print(f"  写出 {path.relative_to(PROJECT_ROOT)}")
    return path


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    font_family = _ensure_cjk_font(app)
    if font_family:
        print(f"offscreen 字体库为空，已加载中文字体：{font_family}")
    written: list[Path] = []

    # 脱敏：导出日志里的临时路径不含真实用户名（见 _sanitized_temp）
    with _sanitized_temp(), tempfile.TemporaryDirectory(prefix="em004-demo-") as tmp:
        demo_source = _make_demo_source(Path(tmp))

        window = gui.WizardWindow()
        window.resize(760, 560)
        window.show()
        QApplication.processEvents()

        source_page = window.page(0)
        # 第 1 步：模拟一次成功的自动扫描（占位路径，不含任何真实个人信息）
        source_page._state.sources.append(Path(DEMO_SOURCE_LABEL))
        source_page._list.addItem(QListWidgetItem(DEMO_SOURCE_LABEL))
        source_page._list.addItem(QListWidgetItem(DEMO_DROP_LABEL))
        source_page._status.setText("自动扫描到 1 个 QQ 表情目录，另拖入 1 个文件夹")
        page1 = _grab(window)

        # 第 2 步（微信）
        window.next()
        QApplication.processEvents()
        platform_page = window.page(1)
        page2_wechat = _grab(window)

        # 第 2 步（QQ 高亮）
        platform_page._qq_radio.setChecked(True)
        page2_qq = _grab(window)
        platform_page._wechat_radio.setChecked(True)

        # 第 3 步：跑一次真实导出（演示数据是现画的合成集），再抓窗口
        # HOME 也脱敏：默认导出目录在 Path.home() 下，会原样进日志（见 _sanitized_home）
        with _sanitized_home():
            window.next()
            QApplication.processEvents()
            export_page = window.page(2)
            export_page._state.sources.clear()
            export_page._state.sources.append(demo_source)
            export_page._export_button.click()
            QApplication.processEvents()
            page3 = _grab(window)
        if export_page._state.export_code != 0:
            print("警告：演示导出的正确性守门未通过，截图日志可能不含“通过”", file=sys.stderr)

        # 随包指引截图先落盘：指引对话框要从磁盘加载这些图，
        # 写盘晚于抓图会嵌进上一轮的旧图（实测踩过）。
        print("随包指引截图（src/emoji_migrator/guides/）：")
        written.append(_save(page1, GUIDES_DIR / "wechat" / "step1.png"))
        written.append(_save(page2_wechat, GUIDES_DIR / "wechat" / "step2.png"))
        written.append(_save(page3, GUIDES_DIR / "wechat" / "step3.png"))
        written.append(_save(page1, GUIDES_DIR / "qq" / "step1.png"))
        written.append(_save(page2_qq, GUIDES_DIR / "qq" / "step2.png"))
        written.append(_save(page3, GUIDES_DIR / "qq" / "step3.png"))

        # 指引窗口本体（验收证据）
        guide_wechat = gui.GuideDialog(gui.guide_for(Platform.WECHAT), title="微信导入指引")
        guide_wechat.show()
        QApplication.processEvents()
        guide_wechat_pix = _grab(guide_wechat)
        guide_wechat.close()

        guide_qq = gui.GuideDialog(gui.guide_for(Platform.QQ), title="QQ导入指引")
        guide_qq.show()
        QApplication.processEvents()
        guide_qq_pix = _grab(guide_qq)
        guide_qq.close()

        # TraceMemo 缺失时的禁用态（临时把 HOME 指到空目录）
        original_home = Path.home
        with tempfile.TemporaryDirectory(prefix="em004-home-") as fake_home_dir:
            Path.home = classmethod(lambda cls: Path(fake_home_dir))  # type: ignore[assignment]
            try:
                window2 = gui.WizardWindow()
                window2.resize(760, 560)
                window2.show()
                QApplication.processEvents()
                tracememo_pix = _grab(window2)
                window2.close()
            finally:
                Path.home = original_home  # type: ignore[assignment]

    print("验收证据截图（docs/em004-gui-evidence/）：")
    written.append(_save(page1, EVIDENCE_DIR / "wizard-page1-source.png"))
    written.append(_save(page2_wechat, EVIDENCE_DIR / "wizard-page2-wechat.png"))
    written.append(_save(page2_qq, EVIDENCE_DIR / "wizard-page2-qq.png"))
    written.append(_save(page3, EVIDENCE_DIR / "wizard-page3-export.png"))
    written.append(_save(guide_wechat_pix, EVIDENCE_DIR / "guide-wechat.png"))
    written.append(_save(guide_qq_pix, EVIDENCE_DIR / "guide-qq.png"))
    written.append(_save(tracememo_pix, EVIDENCE_DIR / "wizard-page1-tracememo-disabled.png"))

    print(f"完成：共 {len(written)} 张（随包 6 + 证据 7）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
