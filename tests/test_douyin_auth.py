"""抖音扫码登录测试（EM-005-B）。

offscreen 运行：``QT_QPA_PLATFORM`` 由 ``conftest.py`` 在导入 PySide6 之前统一设置。
本文件守护 ``src/emoji_migrator/douyin_auth.py``：

- DPAPI 往返与「密文 ≠ 明文」——加密真的发生了，且密文里翻不到明文 sessionid；
- save / load 往返与覆盖写、目录自动创建；
- 文件缺失 / 垃圾字节 / 内容不是 JSON / 缺 sessionid 字段 → ``None`` 且不抛；
- ``%APPDATA%`` 定向：全部用例 ``monkeypatch.setenv`` 隔离，**绝不碰真实 APPDATA**；
- offscreen 冒烟：``DouyinAuthWindow`` 真实实例化 + 离屏抓图（QtWebEngine 在
  offscreen 下可用，2026-10-19 本机实测 import / 实例化 / show / grab 均正常，
  故未降级为「仅 import」的假冒烟）；
- 「完成登录」全链路：喂一颗**假** sessionid cookie → 加密落盘 → 成功信号 →
  关窗，且重复到达不二次触发；
- 非抖音域名的 cookie 不进累积、不触发完成；
- 超时 / 手动关窗 → 放弃信号（不透明短码），以及超时计时器的真实接线。

隐私铁律：所有用例只用**假 cookie 值**（``FAKE_`` 前缀），绝不写真实 cookie。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineCookieStore
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from emoji_migrator import douyin_auth

#: 假 cookie 值（隐私红线：测试绝不出现真实 cookie 值）
_FAKE_SESSION = "FAKE_SESSION_VALUE_XYZ"
_FAKE_SESSION_SS = "FAKE_SESSION_SS_VALUE_456"
_FAKE_TTWID = "FAKE_TTWID_VALUE_789"
#: MINOR-3 守护用：代表「已存在的旧会话」，与 :data:`_FAKE_SESSION` 区分开，
#: 这样「旧内容未被破坏」断言不会被「两次写入相同内容」误判为通过。
_FAKE_SESSION_OLD = "FAKE_SESSION_VALUE_OLDER"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """本文件共用一个 QApplication（offscreen）。"""
    return QApplication.instance() or QApplication([])


def _fake_session_cookie(domain: str = ".douyin.com") -> QNetworkCookie:
    """造一颗假 sessionid cookie（域名可配，用来测域名过滤）。"""
    cookie = QNetworkCookie(b"sessionid", _FAKE_SESSION.encode("utf-8"))
    cookie.setDomain(domain)
    return cookie


# --------------------------------------------------------------------------------------
# DPAPI 加解密
# --------------------------------------------------------------------------------------


def test_dpapi_roundtrip() -> None:
    """DPAPI 往返：decrypt(encrypt(x)) == x（含非 ASCII 载荷，验证 UTF-8 路径）。"""
    # 载荷含非 ASCII 字符：验证「JSON UTF-8 编码 → DPAPI」链路不丢字节。
    secret = "fake-cookie-payload-抖音-🙂".encode()
    assert douyin_auth._dpapi_unprotect(douyin_auth._dpapi_protect(secret)) == secret


def test_dpapi_ciphertext_does_not_leak_plaintext() -> None:
    """密文 ≠ 明文：密文字节里不允许出现明文 sessionid 值。"""
    plaintext = _FAKE_SESSION.encode("utf-8")
    ciphertext = douyin_auth._dpapi_protect(plaintext)
    assert ciphertext != plaintext
    assert plaintext not in ciphertext


# --------------------------------------------------------------------------------------
# 缓存文件：定位、往返、失败面
# --------------------------------------------------------------------------------------


def test_cookie_cache_path_lives_under_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """缓存文件定向 ``%APPDATA%\\emoji-migrator\\douyin_cookie.bin``。

    环境变量在**调用时**读取——这里换掉 APPDATA 后路径必须立刻跟着变，
    否则测试隔离失效（会碰真实 APPDATA）。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert douyin_auth.cookie_cache_path() == tmp_path / "emoji-migrator" / "douyin_cookie.bin"


def test_save_and_load_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save / load 往返：load_cached_cookie() 返回的 dict 与存入的一致。

    同时锁定「落盘的是密文」：磁盘文件里不允许出现明文 sessionid 值——
    否则将来若有人把加密那步删掉，往返测试仍会绿（JSON 明文也能读回来），
    只有这条断言能抓住「没加密就落盘」。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cookies = {"sessionid": _FAKE_SESSION, "sessionid_ss": _FAKE_SESSION_SS, "ttwid": _FAKE_TTWID}
    douyin_auth.save_cookies(cookies)
    path = douyin_auth.cookie_cache_path()
    assert path.is_file()
    assert _FAKE_SESSION.encode("utf-8") not in path.read_bytes()
    assert douyin_auth.load_cached_cookie() == cookies


def test_save_overwrites_previous_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """重复登录覆盖旧缓存：load 拿到的是最后一次存入的内容。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    douyin_auth.save_cookies({"sessionid": "FAKE_OLD_SESSION"})
    douyin_auth.save_cookies({"sessionid": _FAKE_SESSION, "ttwid": _FAKE_TTWID})
    assert douyin_auth.load_cached_cookie() == {"sessionid": _FAKE_SESSION, "ttwid": _FAKE_TTWID}


def test_save_creates_missing_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """save 时先建目录：``%APPDATA%\\emoji-migrator`` 在首次登录的机器上不存在。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    douyin_auth.save_cookies({"sessionid": _FAKE_SESSION})
    assert douyin_auth.cookie_cache_path().is_file()


def test_load_returns_none_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """文件不存在 → None（不抛），has_cached_session 同步为 False。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert douyin_auth.load_cached_cookie() is None
    assert douyin_auth.has_cached_session() is False


def test_load_returns_none_on_garbage_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """损坏文件（垃圾字节）：解密失败 → None，且不抛。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = douyin_auth.cookie_cache_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00garbage-not-a-dpapi-blob\xff")
    assert douyin_auth.load_cached_cookie() is None
    assert douyin_auth.has_cached_session() is False


def test_load_returns_none_when_payload_is_not_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """解密成功但内容不是 JSON：同样整体作废返回 None。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = douyin_auth.cookie_cache_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(douyin_auth._dpapi_protect(b"this is not json"))
    assert douyin_auth.load_cached_cookie() is None


def test_load_returns_none_when_session_field_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """字段不全：没有 sessionid / sessionid_ss 的缓存视为没有缓存。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    douyin_auth.save_cookies({"ttwid": _FAKE_TTWID})
    assert douyin_auth.load_cached_cookie() is None
    assert douyin_auth.has_cached_session() is False


def test_has_cached_session_reflects_local_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """has_cached_session 只反映本地缓存文件（不碰网络）：存入后为 True。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert douyin_auth.has_cached_session() is False
    douyin_auth.save_cookies({"sessionid": _FAKE_SESSION})
    assert douyin_auth.has_cached_session() is True


# --------------------------------------------------------------------------------------
# 登录窗口（offscreen 真实实例化）
# --------------------------------------------------------------------------------------


def test_auth_window_instantiates_offscreen(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """offscreen 冒烟：``DouyinAuthWindow`` 可真实实例化并离屏抓图。

    QtWebEngine 在 offscreen 平台插件下可用是**实测结论**（2026-10-19：
    import / 实例化 / show / grab 均正常），故这里不降级成「仅 import」的
    假冒烟。页面加载被猴子补丁挡掉——测试不联网、不碰真实抖音。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(douyin_auth.DouyinAuthWindow, "_open_login_page", lambda self: None)

    window = douyin_auth.DouyinAuthWindow()
    assert isinstance(window._view, QWebEngineView)
    assert window.windowTitle() == "抖音登录"
    assert window._finished is False

    pixmap = window.grab()
    assert not pixmap.isNull()
    assert pixmap.width() > 100


def test_session_cookie_completes_login_and_saves(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """喂一颗假 sessionid cookie → 完成登录全链路：加密落盘 + 成功信号 + 关窗。

    重复到达同一 cookie 不二次触发（``_finished`` 守门）。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(douyin_auth.DouyinAuthWindow, "_open_login_page", lambda self: None)

    window = douyin_auth.DouyinAuthWindow()
    succeeded: list[bool] = []
    window.login_succeeded.connect(lambda: succeeded.append(True))

    window._on_cookie_added(_fake_session_cookie())
    assert succeeded == [True]
    assert douyin_auth.load_cached_cookie() == {"sessionid": _FAKE_SESSION}

    window._on_cookie_added(_fake_session_cookie())  # 第二次：不重复触发
    assert succeeded == [True]

    window.close()  # 已完成后再关窗：不再发放弃信号
    assert succeeded == [True]


def test_non_douyin_cookies_are_ignored(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """非抖音域名（含域名缺失）的 cookie 不进累积、不触发完成登录。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(douyin_auth.DouyinAuthWindow, "_open_login_page", lambda self: None)

    window = douyin_auth.DouyinAuthWindow()
    succeeded: list[bool] = []
    window.login_succeeded.connect(lambda: succeeded.append(True))

    window._on_cookie_added(_fake_session_cookie(domain="example.com"))
    window._on_cookie_added(_fake_session_cookie(domain=""))  # 域名缺失：跳过

    assert window._cookies == {}
    assert succeeded == []
    assert douyin_auth.load_cached_cookie() is None


def test_timeout_abandons_login(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """超时处理器：发放弃信号（timeout）且不重复触发。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(douyin_auth.DouyinAuthWindow, "_open_login_page", lambda self: None)

    window = douyin_auth.DouyinAuthWindow()
    reasons: list[str] = []
    window.login_abandoned.connect(reasons.append)

    window._on_timeout()
    window._on_timeout()  # 第二次：不重复发
    assert reasons == ["timeout"]


def test_close_emits_abandoned(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """用户手动关窗 = 放弃（原因码 user-closed），且不写任何缓存文件。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(douyin_auth.DouyinAuthWindow, "_open_login_page", lambda self: None)

    window = douyin_auth.DouyinAuthWindow()
    reasons: list[str] = []
    window.login_abandoned.connect(reasons.append)

    window.close()
    assert reasons == ["user-closed"]
    assert not douyin_auth.cookie_cache_path().exists()


def test_open_login_page_starts_timeout_timer(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """超时计时器真实接线：``_open_login_page`` 起的计时器到点后自动放弃关窗。

    把 5 分钟改短到 50ms 验证（不真等 5 分钟）；网络加载与真实 profile 的
    清空都被类级猴子补丁挡掉——测试不联网、不动真实 cookie。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(douyin_auth, "_TIMEOUT_MS", 50)
    monkeypatch.setattr(QWebEngineView, "load", lambda self, url: None)
    monkeypatch.setattr(QWebEngineCookieStore, "deleteAllCookies", lambda self: None)

    window = douyin_auth.DouyinAuthWindow()
    reasons: list[str] = []
    window.login_abandoned.connect(reasons.append)

    deadline = time.monotonic() + 10
    while not reasons and time.monotonic() < deadline:
        qapp.processEvents()
    assert reasons == ["timeout"]


def test_save_is_atomic_replace_keeps_old_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MINOR-3：原子写有守护——``os.replace`` 失败时旧缓存不被截断、临时文件被清理。

    独立审核突变实验发现：把原子写换成直接 ``path.write_bytes()`` 时本文件 17 个用例
    全绿（改坏不红）——``save_cookies`` 的 docstring 承诺「绝不留下半个缓存文件」
    却没有测试锁定。本用例通过 monkeypatch ``os.replace`` 抛异常，
    证明写失败时目标文件仍是**旧内容**（而非半截密文），且临时文件已删除。

    归因关键：若实现退化为直接写目标文件，``os.replace`` 根本不会被调用，
    monkeypatch 失效 → 目标文件被新密文覆盖 → 断言 ``旧内容`` 失败 → 变红。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    douyin_auth.save_cookies({"sessionid": _FAKE_SESSION_OLD})
    cache_path = douyin_auth.cookie_cache_path()
    old_bytes = cache_path.read_bytes()
    assert old_bytes, "前置：旧缓存应已落盘"

    def _boom(src: object, dst: object) -> None:
        raise OSError("模拟 replace 失败（磁盘满/占用）")

    monkeypatch.setattr(douyin_auth.os, "replace", _boom)
    with pytest.raises(OSError):
        douyin_auth.save_cookies({"sessionid": _FAKE_SESSION})

    assert cache_path.read_bytes() == old_bytes, "写失败后旧缓存被破坏（原子性丢失）"
    leftovers = [p.name for p in cache_path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"临时文件未清理：{leftovers}"


def test_open_login_page_clears_cookies_first(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MINOR-4a：``deleteAllCookies()`` 有守护——打开登录页必先清 cookie。

    独立审核突变实验发现：删掉 ``_open_login_page`` 里的这一行，全文件仍全绿。
    但它是「重新登录能触发新 sessionid」的**唯一保障**——PySide6 6.12.0 实测
    ``cookieAdded`` 不补发既有cookie（见类文档字符串），删了这行症状是
    「点重新登录必然耗满 5 分钟超时」，极难排查。

    本用例 spy 住 ``deleteAllCookies`` 记录调用顺序，断言它**在``load`` 之前**
    被调用过（顺序是语义的一部分：先清后载）。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        QWebEngineCookieStore,
        "deleteAllCookies",
        lambda self: calls.append("deleteAllCookies"),
    )
    monkeypatch.setattr(
        QWebEngineView, "load", lambda self, url: calls.append("load")
    )

    window = douyin_auth.DouyinAuthWindow()
    try:
        assert "deleteAllCookies" in calls, "未调用 deleteAllCookies（重新登录将必然超时）"
        assert calls.index("deleteAllCookies") < calls.index("load"), (
            "必须先清 cookie 再加载页面，否则新页面的 cookie 会与旧 cookie 混杂"
        )
    finally:
        window._finished = True
        window.close()
