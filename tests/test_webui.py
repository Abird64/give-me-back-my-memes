r"""webui 前端壳的守护测试（EM-011）。

守护目标（每条都对应一个**真实踩过的坑**，不是形式主义）：

1. **模块树形状**：``webui.py`` 必须在包内，且是第 23 个模块（闸门在 test_compliance）。
2. **前端资源随包**：``webui/index.html`` 与字体目录必须存在，且**不联网**
   （正式产品不能依赖 Google Fonts CDN——用户可能离线）。
3. **官方平台图标**：三个平台的矢量图标必须内联在 HTML 里，且**不得**用
   emoji（💬🐧🎵）冒充平台 logo——那是原型阶段的偷懒。
4. **抖音图标的两个坑**（实测踩过）：
   - 父级 ``<g transform>`` 链必须保留（只扒 ``<path>`` 会丢变换 → 渲染空白）；
   - ``fill-rule:evenodd`` 必须保留（丢了音符就被填实 → 视觉上消失）。
5. **JS 字符串转义**：回传给页面的中文文案里可能带引号/换行/反斜杠
   （如 ``C:\Users\...``），必须正确转义成合法 JS 字面量。
6. **导出目录不得自我覆盖**：``_normalize_dir(source, out_dir)`` 的入参
   必须是**两个不同目录**——传同一个会让管线自我覆盖。
7. **零 Qt 依赖的编排层不反向依赖**：``collect_flow`` 等编排层不得 import webui。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from emoji_migrator import config

WEBUI_PY = Path(config.PACKAGE_ROOT) / "webui.py"
WEBUI_DIR = Path(config.PACKAGE_ROOT) / "webui"
INDEX_HTML = WEBUI_DIR / "index.html"
FONTS_DIR = WEBUI_DIR / "fonts"


def _strip_js_comments(text: str) -> str:
    """剥掉 JS/HTML 注释，只留代码。

    用于「检查代码里有没有某种写法」这类断言——注释里常引用旧写法做说明，
    逐行过滤挡不住跨行块注释的中间行。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "/*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if two == "//":
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text[i] == '"' or text[i] == "'":
            quote = text[i]
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- 模块树


def test_webui_module_exists() -> None:
    """webui.py 必须在包内。"""
    assert WEBUI_PY.is_file(), f"缺前端壳模块：{WEBUI_PY}"


def test_webui_is_in_module_manifest() -> None:
    """模块清单必须含 webui（与 test_module_all 的清单一致）。"""
    from tests.test_module_all import MODULE_NAMES

    assert "emoji_migrator.webui" in MODULE_NAMES


# ---------------------------------------------------------------- 前端资源


def test_index_html_exists() -> None:
    assert INDEX_HTML.is_file(), f"缺前端页面：{INDEX_HTML}"


def test_frontend_does_not_rely_on_remote_fonts() -> None:
    """前端**不得**引用 Google Fonts CDN——正式产品要能离线用。

    展示体（ZCOOL KuaiLe）与拉丁体（Archivo Black）已随包放在 ``fonts/``。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html, "前端仍在引用 Google Fonts CDN"
    assert "fonts.gstatic.com" not in html, "前端仍在引用 Google Fonts CDN"


def test_fonts_are_shipped_locally() -> None:
    """字体必须随包（woff2 + fonts.css），且体积受控。"""
    assert (FONTS_DIR / "fonts.css").is_file(), "缺 fonts.css"
    woff2 = list(FONTS_DIR.glob("*.woff2"))
    assert woff2, "没打包任何字体文件"
    total = sum(p.stat().st_size for p in woff2)
    assert total < 400 * 1024, f"字体包过大（{total/1024:.0f} KB），应只带展示体"


def test_font_css_points_to_local_files() -> None:
    """fonts.css 里只应引用相对路径 fonts/xxx.woff2。"""
    css = (FONTS_DIR / "fonts.css").read_text(encoding="utf-8")
    assert "http://" not in css and "https://" not in css, "fonts.css 里混进了外链"
    assert css.count("url('fonts/") == len(list(FONTS_DIR.glob("*.woff2")))


# ---------------------------------------------------------------- 平台图标


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _css() -> str:
    """页面内联样式表（``<style>`` 到 ``</style>`` 之间）。"""
    html = _html()
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    assert m, "页面没有内联 <style>"
    return m.group(1)


@pytest.mark.parametrize(
    ("name", "marker"),
    [
        ("微信", "M8.691 2.188"),
        ("QQ", "M21.395 15.035"),
    ],
)
def test_official_brand_icons_inlined(name: str, marker: str) -> None:
    """微信/QQ 官方矢量必须内联（simple-icons 路径特征）。"""
    html = _html()
    assert marker in html, f"{name} 官方图标未内联进页面"


def test_douyin_icon_keeps_transform_chain() -> None:
    """抖音图标必须保留父级 ``<g transform>`` 链。

    坑：只把 ``<path>`` 扒出来塞进新 svg，会丢掉 transform →
    坐标不缩放 → 图形跑出 viewBox → **渲染成空白**。
    """
    html = _html()
    assert "matrix(0.00859214" in html, "抖音图标丢了父级 transform 链（会渲染成空白）"


def test_douyin_icon_keeps_fill_rule() -> None:
    """抖音图标必须保留 ``fill-rule:evenodd``。

    坑：该 path 是「外圈圆 + 内音符」双子路径，靠 evenodd 挖空音符；
    丢了 evenodd 音符被填实 → 视觉上「消失」。
    """
    html = _html()
    assert "fill-rule:evenodd" in html, "抖音图标丢了 fill-rule:evenodd（音符会被填没）"


def test_platform_icons_are_not_emoji() -> None:
    """平台图标**不得**用 emoji 冒充（💬🐧🎵 是原型阶段的偷懒）。"""
    html = _html()
    for emo in ("💬", "🐧", "🎵"):
        assert f'class="glyph">{emo}' not in html, f"平台图标还在用 emoji {emo}"
        assert f'class="ico">{emo}' not in html, f"平台图标还在用 emoji {emo}"


def test_empty_gallery_does_not_use_bottle_emoji() -> None:
    """空相册使用 CSS 空状态图形，不把瓶子 emoji 当品牌视觉。"""
    html = _html()
    assert "🫙" not in html
    assert 'class="empty-mark"' in html


def test_primary_navigation_uses_clear_workflow_names() -> None:
    """主导航按真实工作流命名，避免俚语造成理解成本。"""
    html = _html()
    assert '>添加</button>' in html
    assert '>整理</button>' in html
    assert '>导出</button>' in html
    assert '第 1 步 · 添加表情来源' in html
    assert '第 2 步 · 整理我的表情' in html
    assert '第 3 步 · 导出到目标端' in html
    assert '捞表情' not in html
    assert '送回去' not in html


def test_export_can_be_restarted_after_completion() -> None:
    """导出完成后必须提供明确的再次导出/继续选择入口。"""
    html = _html()
    assert '再次选择目录并导出' in html
    assert '继续选择表情' in html
    assert 'function continueExport()' in html


def test_official_icons_are_six_inlined_svgs() -> None:
    """三个平台 × 两处（导入页 / 导出页）= 6 个内联图标。"""
    html = _html()
    count = html.count('<svg class="pico"')
    assert count == 6, f"内联平台图标数应为 6（3 平台 × 2 处），实际 {count}"


# ---------------------------------------------------------------- 文案与产品名


def test_brand_uses_memes_not_emoji() -> None:
    """英文标语用 **MEMES** 不是 EMOJI。

    坑：emoji 特指 Unicode 里那种通用小表情；本产品搬的是**表情包/梗图**（GIF 动图）。
    """
    html = _html()
    assert "GIVE ME BACK MY MEMES" in html
    assert "EMOJI" not in html.upper().replace("EMOJI-MIGRATOR", ""), "标语里还有 EMOJI"


def test_design_notes_not_in_product() -> None:
    """正式产品**不展示**设计说明/设计思路（那是给文档看的，不是给用户看的）。"""
    html = _html()
    assert "<details" not in html, "产品页面里不该有折叠的设计说明块"
    assert "设计说明" not in html


def test_format_rules_are_collapsed_by_default() -> None:
    """三端格式差异说明默认隐藏，悬浮才显示（面向小白）。"""
    html = _html()
    assert html.count('class="tipbox"') == 3, "三张目标卡都该有一个 tipbox"
    css = html
    assert "visibility:hidden" in css, "tipbox 默认应隐藏"


# ---------------------------------------------------------------- 桥接与转义


def test_js_string_escaping() -> None:
    """回传文案里的引号/换行/反斜杠必须转义成合法 JS 字面量。"""
    from emoji_migrator.webui import _js_str

    assert _js_str("a\"b") == '"a\\"b"'
    assert _js_str("a\\b") == '"a\\\\b"'
    assert _js_str("a\nb") == '"a\\nb"'
    assert _js_str("中文") == '"中文"'


def test_bridge_slots_are_exposed() -> None:
    """前端要用到的桥接槽必须存在（@Slot 装饰过）。"""
    from emoji_migrator.webui import WebBridge

    for name in (
        "scan_qq",
        "gallery_items_async",
        "douyin_login",
        "douyin_login_state",
        "collect_douyin",
        "export_weixin",
        "export_memes",
        "export_selected",
        "choose_export_dir",
        "open_dir",
        "default_out_dir",
    ):
        assert hasattr(WebBridge, name), f"桥接缺槽：{name}"


def test_frontend_calls_bridge_methods() -> None:
    """前端必须真的调桥接（而不是只在 Python 侧有槽）。"""
    html = _html()
    for call in ("window.bridge.gallery_items_async", "window.bridge.export_selected",
                 "window.bridge.choose_export_dir",
                 "window.bridge.douyin_login_state", "window.bridge.open_dir",
                 "window.bridge.douyin_login"):
        assert call in html, f"前端没调用 {call}"


def test_douyin_uses_one_stateful_primary_button() -> None:
    """抖音登录与添加收藏共用一个按钮，并通过文案显示当前状态。"""
    html = _html()
    assert 'id="b-dy"' in html, "抖音缺主按钮"
    assert 'id="b-dy-login"' not in html, "抖音不应再占用第二颗按钮"
    assert "onclick=\"douyinPrimary()\"" in html, "抖音主按钮没绑定状态动作"
    assert "未登录 · 点击扫码登录" in html
    assert "已登录 · 添加抖音表情" in html
    assert "async function douyinLogin(" in html
    assert "async function douyinPrimary(" in html
    assert "async function ingest(k)" in html


def test_platform_buttons_share_height_and_add_wording() -> None:
    """三个来源按钮要同高，默认和运行态都用统一的「添加」语气。"""
    html = _html()
    css = _css()
    assert "height:52px" in css, "平台按钮没有统一高度"
    for label in ("添加微信表情", "添加 QQ 表情", "未登录 · 点击扫码登录"):
        assert label in html
    assert "markRunning(k, '正在添加…')" in html
    assert "✓ 已添加" in html


def test_album_toolbar_has_no_dead_search_and_keeps_actions_on_filter_row() -> None:
    """相册没有无效的文件名搜索，分类与批量操作放在同一工具行。"""
    html = _html()
    css = _css()
    assert 'id="q"' not in html
    assert ".search" not in css
    assert 'class="filter-group"' in html
    assert 'class="bar-actions"' in html
    assert "getElementById('q')" not in html


def test_session_collection_uses_temporary_workspace() -> None:
    """采集相册不应按启动次数永久堆在用户主目录。"""
    source = WEBUI_PY.read_text(encoding="utf-8")
    assert 'TemporaryDirectory(prefix="emoji-migrator-session-")' in source
    assert "def cleanup_session" in source
    assert 'Path.home() / "emoji-migrator-out"' not in source


def test_platform_main_button_uses_id_not_query_selector() -> None:
    """主按钮必须按 id 取，不能用 ``card.querySelector('.btn')``。

    坑：抖音卡片有两个 ``.btn``（登录 + 采集），``querySelector`` 抓到的是
    第一个（登录），于是点「采集」禁用的却是登录按钮。
    """
    html = _html()
    body = _strip_js_comments(html)
    assert "MAIN_BTN" in body, "缺 MAIN_BTN 映射表"
    assert "card.querySelector('.btn')" not in body, (
        "还在用 querySelector('.btn') 抓主按钮——抖音卡片有两个 .btn，会抓错"
    )


def test_long_task_has_visible_progress_feedback() -> None:
    """微信导出实测 289 秒，必须有**明确**的「正在做…」视觉反馈。

    坑（2026-10-21 用户反馈「点了好像反应不大」）：原先点下去按钮只是禁用、
    文字一闪就没了，既不知道在跑什么也不知道要跑多久。
    """
    html = _html()
    for token in ('id="bar-wx"', 'id="t-wx"', "markRunning", "clearRunning"):
        assert token in html, f"缺长任务反馈件：{token}"
    body = _strip_js_comments(html)
    assert re.search(r"markRunning\(", body), "ingest 没调 markRunning"
    assert re.search(r"clearRunning\(", body), "没有对应的 clearRunning 收尾"


def test_page_has_no_fixed_background_attachment() -> None:
    """页面渐变背景**不能**用 ``background-attachment:fixed``。

    实测（2026-10-21，无 GPU 软件渲染，真实 Qt WebEngine 内滚动
    728 张贴纸 / 页高 15537px）：

    ==================  ========  =======  ========  ==========
    方案                 平均/帧  p95     >32ms 帧数  最长任务
    ==================  ========  =======  ========  ==========
    attachment:fixed     62.4ms   130.7ms  105       176ms
    挂在 html 上         16.0ms   17.0ms   1         130ms
    ==================  ========  =======  ========  ==========

    慢 3.9 倍，肉眼就是"滑动时顿"。fixed 背景每帧都要重做整屏三段渐变合成。
    """
    css = _strip_js_comments(_css())
    assert "background-attachment:fixed" not in css.replace(" ", ""), (
        "页面渐变背景不能用 background-attachment:fixed —— 滚动会慢 3.9 倍"
    )
    # 渐变必须挂在 html 上（按视口尺寸计算、由 canvas 铺满滚动区域）
    assert re.search(r"html\{[^}]*background-image", css), "渐变背景应挂在 html 上"


def test_bridge_accepts_chinese_platform_labels() -> None:
    """前端传的是**中文**平台名（"微信"/"QQ"/"抖音"），桥接必须认。

    坑：``Platform`` 枚举的值是**英文**（``"wechat"``/``"qq"``/``"douyin"``）。
    若桥接直接 ``Platform(中文)``，会抛 ValueError → 前端点「开始导出」**必然失败**。
    """
    from emoji_migrator import webui

    for label, expected in (
        ("微信", "wechat"),
        ("QQ", "qq"),
        ("抖音", "douyin"),
    ):
        assert webui._PLATFORM_BY_LABEL[label].value == expected

    # 英文标识也要认（后端内部用）
    for ident in ("wechat", "qq", "douyin"):
        assert webui._PLATFORM_BY_LABEL[ident.replace("wechat", "微信")
                                        .replace("qq", "QQ")
                                        .replace("douyin", "抖音")].value == ident


def test_frontend_passes_chinese_labels_to_bridge() -> None:
    """前端传给桥接的目标端必须是中文标签（与 ``_PLATFORM_BY_LABEL`` 的键一致）。"""
    html = _html()
    # 前端的目标卡用 data-t="微信" 这类中文值
    for label in ("微信", "QQ", "抖音"):
        assert f'data-t="{label}"' in html, f"前端没有 {label} 目标卡"

    # pickT 传的是卡片上的 data-t 值
    m = re.search(r"function pickT\(t\)\{", html)
    assert m, "前端缺 pickT"
    # 导出调用把 target 原样传给桥接（参数名可能随实现调整，用正则更稳）
    assert re.search(r"window\.bridge\.export_selected\([^)]*target", html), (
        "导出没把目标端传给桥接"
    )


# ---------------------------------------------------------------- 导出目录语义


def test_export_uses_distinct_source_and_target_dirs(tmp_path_factory) -> None:
    """导出时 source 与 out_dir 必须是**两个不同目录**。

    坑：``cli._normalize_dir(source, out_dir, ...)`` 的入参语义是
    「从 source 归一化到 out_dir」；传同一个目录会让管线**自我覆盖**。

    验的是**行为**（真的调一次导出），不是看源码字符串——
    源码断言在重构后容易变成"看着像对其实没跑"。
    """
    import inspect

    from emoji_migrator import cli, webui

    seen: dict[str, Path] = {}

    def fake_normalize(source: Path, out_dir: Path, platform, do_dedupe: bool) -> int:
        seen["source"] = Path(source)
        seen["out"] = Path(out_dir)
        return 0

    # 造两个目录：来源目录有图，导出目录空
    root = Path(tmp_path_factory.mktemp("webui-export"))
    (root / "collected").mkdir(parents=True)
    (root / "collected" / "a.gif").write_bytes(b"GIF89a")
    (root / "export").mkdir(parents=True)

    bridge = webui.WebBridge.__new__(webui.WebBridge)   # 不建 QObject，只调纯逻辑
    monkey = pytest.MonkeyPatch()
    monkey.setattr(webui.cli, "_normalize_dir", fake_normalize)
    try:
        msg = bridge.export_memes(str(root), "微信")
    finally:
        monkey.undo()

    assert "导出完成" in msg, f"导出失败：{msg}"
    assert seen["source"] != seen["out"], "来源与输出目录相同，管线会自我覆盖"
    assert seen["source"].name == "collected"
    assert seen["out"].name == "export"
    # 确认真的走的是受测编排（而不是自己实现了一套）
    assert inspect.signature(cli._normalize_dir).parameters.keys() >= {
        "source",
        "out_dir",
        "platform",
        "do_dedupe",
    }


# ---------------------------------------------------------------- 依赖方向


def test_orchestration_layers_do_not_import_webui() -> None:
    """编排层（零 Qt 依赖的那些）不得反向 import webui。"""
    pkg = Path(config.PACKAGE_ROOT)
    for name in ("collect_flow.py", "cli.py", "wxcollect.py", "config.py", "platforms.py"):
        text = (pkg / name).read_text(encoding="utf-8")
        assert "webui" not in text, f"{name} 反向 import 了 webui，破坏依赖方向"


def test_webui_does_not_reimplement_business_logic() -> None:
    """webui 只做壳与桥，不得自带嗅探/去重/守门实现。"""
    from emoji_migrator import webui

    code = webui.__doc__ or ""
    assert "cli._normalize_dir" in code or "复用" in code
    # 不得出现自行实现的格式嗅探函数
    assert "def sniff" not in Path(webui.__file__).read_text(encoding="utf-8")
    assert "def _normalize" not in Path(webui.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 品牌资源


def test_brand_assets_directory() -> None:
    """assets/ 下应保留三个平台的独立图标源文件（便于换肤/二次编辑）。"""
    assets = Path(r"D:/code/mymeme/design-prototype/assets")
    if not assets.is_dir():
        pytest.skip("设计原型资源目录不在本次交付范围")
    for name in ("wechat", "qq", "douyin"):
        assert (assets / f"icon-{name}.svg").is_file()


# ---------------------------------------------------------------- 桥接初始化（三个实测踩过的坑）


def test_frontend_includes_qwebchannel_script() -> None:
    """前端必须引 ``qwebchannel.js``。

    坑：不引它，``window.QWebChannel`` 全局不存在 → 桥接永远连不上。
    """
    html = _html()
    assert "qwebchannel.js" in html, "前端没引 qwebchannel.js"


def test_frontend_instantiates_qwebchannel() -> None:
    """必须 ``new QWebChannel(transport, cb)``。

    坑：只引脚本不够——那只是定义全局类；不 new 就不会握手。
    """
    html = _html()
    assert "new window.QWebChannel(" in html, "前端没有 new QWebChannel"


def test_frontend_reads_bridge_from_channel_objects() -> None:
    """桥接对象在 ``channel.objects.bridge``，**不在** ``window.bridge``。

    坑（实测）：Qt 6 的 qwebchannel.js 把对象放进 ``channel.objects``，
    不会自动挂到 window；直接读 ``window.bridge`` 永远是 undefined。
    """
    html = _html()
    assert "objs.bridge" in html or "channel.objects" in html, (
        "前端没从 channel.objects 取 bridge（Qt 6 不挂 window）"
    )


def test_bridge_calls_are_awaited() -> None:
    """所有桥接调用必须 ``await``。

    坑：QWebChannel 槽调用返回 **Promise**（跨进程必须异步），
    当同步值用会拿到 ``[object Promise]``。
    """
    html = _html()
    # 先把 JS 注释**整段剥掉**再检查——注释里会引用旧写法做说明，
    # 逐行过滤挡不住跨行块注释的中间行。
    code = _strip_js_comments(html)
    # 规则：桥接槽调用要么被 `await` 包裹，要么**所在函数的返回值就是 Promise**
    # （此时调用方 await 该函数——同样是正确的异步用法）。
    # 只禁止「同步上下文里把 Promise 当普通值用」。
    for m in re.finditer(r"window\.bridge\.(\w+)\(", code):
        name = m.group(1)
        head = code[max(0, m.start() - 80):m.start()]
        if "await" in head:
            continue
        # 统一约定：**所有**桥接调用点都显式 await（前端全部用 async function）
        raise AssertionError(
            f"window.bridge.{name}( 没有 await（QWebChannel 槽返回 Promise，"
            f"同步用会拿到 [object Promise]）"
        )
    hits = len(re.findall(r"window\.bridge\.\w+\(", code))
    assert hits >= 4, f"实际桥接调用点太少（{hits}），测试没生效"
    assert "await " in html, "前端应有 await 用法"


def test_no_sync_bridge_call() -> None:
    """不得出现「把桥接返回值当字符串直接用」的老写法。"""
    html = _html()
    assert "String(window.bridge." not in html
    assert "= window.bridge.default_out_dir();" not in html


# ---------------------------------------------------------------- 无 GPU 环境


def test_rendering_mode_is_gpu_by_default_and_can_fallback(monkeypatch) -> None:
    """默认不强制软件渲染，确实需要时可显式切换兼容模式。"""
    import os

    from emoji_migrator import webui

    monkeypatch.delenv("EMOJI_MIGRATOR_FORCE_SOFTWARE", raising=False)
    webui._enable_software_rendering()
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    assert "--disable-gpu" not in flags
    assert "--disable-gpu-compositing" not in flags
    assert "--disable-software-rasterizer" not in flags

    monkeypatch.setenv("EMOJI_MIGRATOR_FORCE_SOFTWARE", "1")
    webui._enable_software_rendering()
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    assert "--disable-gpu" in flags
    assert "--disable-gpu-compositing" in flags
    assert "--disable-software-rasterizer" not in flags
