"""抖音扫码登录（EM-005-B）：内嵌浏览器走官方登录页 + DPAPI 加密缓存。

路由依据（``docs/09-抖音登录链路实测.md`` 的人工裁决 **R2**）：纯 HTTP 过不了
抖音 2026 现役的 secsdk/bdms 前端签名风控，故本模块用 ``QWebEngineView`` 打开
官方登录页，让浏览器自己完成风控与签名；本模块只做三件事——

1. **等 cookie**：监听 ``cookieStore().cookieAdded``，``sessionid`` /
   ``sessionid_ss`` 落库即判定登录成功；
2. **存 cookie**：把本次收到的全部抖音域名 cookie 经 DPAPI 加密后**原子写入**
   ``%APPDATA%\\emoji-migrator\\douyin_cookie.bin``（D-14 裁决优先 DPAPI：
   系统级加密、绑定当前 Windows 用户、无需自管密钥文件）；
3. **复用缓存**：:func:`has_cached_session` 只做本地判断，有缓存就不弹登录。

**隐私铁律**：本模块任何代码路径都不得输出 cookie 值或密钥片段——
读取失败静默返回 ``None``（不记录原因），保存失败只抛不带细节的异常，
窗口层只广播不透明短码（``timeout`` / ``user-closed`` / ``save-failed``）。

依赖方向（``AGENTS.md`` §3）：叶子模块，只 import 标准库与 PySide6，
不 import 包内其他模块；由 ``gui`` 向下调用（``gui → douyin_auth``）。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

if sys.platform == "win32":
    import ctypes.wintypes as wintypes
else:  # pragma: no cover - 本产品是 Windows 专用工具；非 Windows 上 DPAPI 不可用
    wintypes = None

__all__ = [
    "DouyinAuthWindow",
    "cookie_cache_path",
    "has_cached_session",
    "load_cached_cookie",
    "save_cookies",
]

#: 登录页入口（**2026-10-20 修正**）：直接开主站根路径，加载完成后由
#: :meth:`DouyinAuthWindow._auto_open_login_sheet` 自动点「登录」拉起扫码弹层。
#:
#: 选择依据是两轮真机实测的血泪对照：
#:
#: * 2026-10-19 spike 用 curl 实测 ``sso.douyin.com/login/`` 返回 200、标题
#:   「登录抖音短视频」——据此选定它为入口。**但 curl 不执行 JS，看不到前端
#:   重定向**：2026-10-20 用户真机发现二维码根本不出现，QWebEngineView 实测
#:   复验（请求拦截 + DOM 采集，四个候选入口对照）``sso.douyin.com`` 的
#:   ``/login/``、``/login/scan_qr_login/``、``/login/?service=…``、``/?type=2``
#:   **全部前端跳转到 ``www.douyin.com/jingxuan`` 主站**，get_qrcode 请求 0 条。
#: * 修正后的主站路线实测通过：加载 ``www.douyin.com`` → 点「登录」→ 扫码弹层
#:   出现，``passport/web/get_qrcode/`` 与 ``check_qrconnect/``（扫码状态轮询）
#:   全链路发出，二维码图片（178×178 data:image）正常渲染。
#:
#: 教训登记：入口 URL 这类「浏览器行为强依赖」的选择，curl 层 200 不算数，
#: 必须在目标运行环境（QWebEngineView）里复验。
_LOGIN_URL = "https://www.douyin.com/"

#: 打开主站后自动点「登录」前的等待（毫秒）：给 SPA 首屏渲染留时间——
#: 过早点不到按钮（实测 loadFinished 时主站头部尚未挂载完成）。
_AUTO_CLICK_DELAY_MS = 4_000

#: 登录等待上限：窗口打开起 5 分钟未出现会话 cookie 即放弃。
_TIMEOUT_MS = 5 * 60 * 1000

#: 出现其一即判定登录成功的会话 cookie 名（抖音 Web 通行口径）。
_SESSION_COOKIE_NAMES = frozenset({"sessionid", "sessionid_ss"})

#: 收集范围：抖音系域名（``douyin.com`` 主站系 + ``iesdouyin.com`` 旧 API 系）。
#: 显式枚举而非 ``"douyin" in domain`` 子串匹配——后者会把假想的
#: ``notdouyin.com`` 也算进来。
_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")

#: 缓存文件相对 ``%APPDATA%`` 的位置：每用户应用数据区——不进仓库、
#: 不进用户表情目录、不进临时目录（重启不丢）。
_COOKIE_RELATIVE_PATH = ("emoji-migrator", "douyin_cookie.bin")

#: DPAPI 描述串（UTF-16LE）：写进密文头部元数据，便于在系统侧识别这份数据
#: 的用途；不含任何秘密信息。
_DPAPI_DESCRIPTION = "emoji-migrator douyin session cookie"


class _DATA_BLOB(ctypes.Structure):
    """Windows ``DATA_BLOB``：DPAPI 的输入 / 输出载体（长度 + 字节指针）。

    字段类型用 ``ctypes`` 基础类型而非 ``wintypes``：本类在非 Windows 上
    也要能定义（模块 import 不炸），真正的 API 调用另有平台守卫。
    """

    _fields_ = [
        ("cbData", ctypes.c_ulong),  # DWORD：字节数
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),  # BYTE*：数据缓冲区
    ]


if wintypes is not None:
    # 用途：DPAPI 加 / 解密的 ctypes 绑定（D-14：抖音登录 cookie 落盘加密）。
    # 显式声明参数原型是 64 位安全的关键——不声明时 ctypes 把指针参数按 C int
    # 传递，高 32 位会被截断（经典踩坑点）；win32 API 一律显式声明。
    # DPAPI 是系统加密服务调用，不涉及 AGENTS.md §1 被禁清单内的任何 API。
    _CryptProtectData = ctypes.WINFUNCTYPE(
        wintypes.BOOL,  # 返回值：TRUE = 成功
        ctypes.POINTER(_DATA_BLOB),  # pDataIn：待加密数据
        wintypes.LPCWSTR,  # szDataDescr：用途描述串（UTF-16）
        ctypes.POINTER(_DATA_BLOB),  # pOptionalEntropy：附加熵（不用，None）
        wintypes.LPVOID,  # pvReserved：必须为 None
        wintypes.LPVOID,  # pPromptStruct：提示结构（不用，None）
        wintypes.DWORD,  # dwFlags：0
        ctypes.POINTER(_DATA_BLOB),  # pDataOut：密文输出
    )(("CryptProtectData", ctypes.windll.crypt32))

    _CryptUnprotectData = ctypes.WINFUNCTYPE(
        wintypes.BOOL,  # 返回值：TRUE = 成功
        ctypes.POINTER(_DATA_BLOB),  # pDataIn：密文
        ctypes.POINTER(wintypes.LPWSTR),  # ppszDataDescr：描述串输出（不用，None）
        ctypes.POINTER(_DATA_BLOB),  # pOptionalEntropy：附加熵（不用，None）
        wintypes.LPVOID,  # pvReserved：必须为 None
        wintypes.LPVOID,  # pPromptStruct：提示结构（不用，None）
        wintypes.DWORD,  # dwFlags：0
        ctypes.POINTER(_DATA_BLOB),  # pDataOut：明文输出
    )(("CryptUnprotectData", ctypes.windll.crypt32))

    # 用途：释放 DPAPI 输出缓冲（LocalFree）；不释放则每次加解密泄漏一份
    # 进程内存（密文/明文缓冲区由 crypt32 用 LocalAlloc 分配）。
    _LocalFree = ctypes.WINFUNCTYPE(wintypes.HANDLE, wintypes.HANDLE)(
        ("LocalFree", ctypes.windll.kernel32)
    )
else:  # pragma: no cover - 非 Windows 上三个原型均为不可用态
    _CryptProtectData = None
    _CryptUnprotectData = None
    _LocalFree = None


def _dpapi_protect(plaintext: bytes) -> bytes:
    """DPAPI 加密（``CryptProtectData``，绑定当前 Windows 用户）。

    D-14 裁决优先 DPAPI 而非自管密钥文件：系统级加密、无需保管密钥、
    换用户 / 换机器均无法解密。描述串写进密文头部（不含秘密信息），
    便于在系统侧识别这份数据的用途。
    """
    if _CryptProtectData is None:  # pragma: no cover - 非 Windows 不会走到这
        raise OSError("当前系统不支持 DPAPI 加密（仅 Windows）")
    source = ctypes.create_string_buffer(plaintext)
    blob_in = _DATA_BLOB(len(plaintext), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    blob_out = _DATA_BLOB()
    ok = _CryptProtectData(
        ctypes.byref(blob_in),
        _DPAPI_DESCRIPTION,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(f"DPAPI 加密失败：{ctypes.WinError()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _LocalFree(blob_out.pbData)


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    """DPAPI 解密（``CryptUnprotectData``），与 :func:`_dpapi_protect` 成对。

    换用户 / 换机器 / 密文损坏都会失败——调用方
    （:func:`load_cached_cookie`）的策略是整体作废返回 ``None``，
    不区分原因（隐私：不记录失败细节）。
    """
    if _CryptUnprotectData is None:  # pragma: no cover - 非 Windows 不会走到这
        raise OSError("当前系统不支持 DPAPI 解密（仅 Windows）")
    source = ctypes.create_string_buffer(ciphertext)
    blob_in = _DATA_BLOB(len(ciphertext), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    blob_out = _DATA_BLOB()
    ok = _CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(f"DPAPI 解密失败：{ctypes.WinError()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _LocalFree(blob_out.pbData)


def cookie_cache_path() -> Path:
    """cookie 缓存文件路径：``%APPDATA%\\emoji-migrator\\douyin_cookie.bin``。

    环境变量在**调用时**读取（而非 import 时）——测试才能用
    ``monkeypatch.setenv("APPDATA", tmp_path)`` 做隔离，不碰真实 APPDATA。
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("环境变量 APPDATA 缺失，无法定位 cookie 缓存目录")
    return Path(appdata).joinpath(*_COOKIE_RELATIVE_PATH)


def save_cookies(cookies: dict[str, str]) -> None:
    """把 cookie 字典 DPAPI 加密后**原子写入**缓存文件。

    原子写（同目录临时文件 + ``os.replace``）的目的是「绝不留下半个缓存
    文件」：写一半崩溃 / 断电时，磁盘上要么是旧缓存、要么什么都没有。
    load 侧对损坏文件的策略本是整体作废（见 :func:`load_cached_cookie`），
    但能不走那条路就不走。目录不存在时先创建（首次登录的机器）。
    """
    payload = json.dumps(cookies, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ciphertext = _dpapi_protect(payload)
    path = cookie_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix="douyin_cookie-", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(ciphertext)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def load_cached_cookie() -> dict[str, str] | None:
    """读取并解密缓存 cookie；**任何失败都返回 ``None``，绝不抛异常**。

    失败面（全部按「没有缓存」处理）：文件不存在、解密失败（换用户 / 换机器 /
    文件损坏）、JSON 损坏、结构不是 ``dict[str, str]``、缺少会话 cookie 字段。

    隐私铁律：失败原因不记录、不外抛——异常消息可能带文件路径，
    但本函数连日志都不打，调用方拿不到任何细节，更不可能碰到 cookie 值。
    """
    try:
        ciphertext = cookie_cache_path().read_bytes()
        cookies = json.loads(_dpapi_unprotect(ciphertext).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(cookies, dict):
        return None
    if not all(
        isinstance(name, str) and isinstance(value, str) for name, value in cookies.items()
    ):
        return None
    if not any(name in cookies for name in _SESSION_COOKIE_NAMES):
        return None
    return cookies


def has_cached_session() -> bool:
    """本地是否已有可用登录缓存（**不做网络探测**）。

    口径：只回答「有缓存就不弹登录」。cookie 是否仍然有效（401 判断，D-15）
    留给 EM-005-C 连采集时落地——本包刻意不碰网络。
    """
    return load_cached_cookie() is not None


def _is_douyin_domain(domain: str) -> bool:
    """域名判定：``douyin.com`` / ``iesdouyin.com`` 本身或其子域。

    前导点（``.douyin.com``）与大小写都归一化后再比——``QNetworkCookie``
    对显式 ``Domain`` 属性会保留前导点（实测）。
    """
    host = domain.casefold().lstrip(".")
    return any(host == base or host.endswith("." + base) for base in _DOUYIN_DOMAINS)


class DouyinAuthWindow(QWidget):
    """抖音扫码登录窗口：内嵌 ``QWebEngineView`` 走官方登录页，捕获会话 cookie。

    流程：打开即清空本 profile 的 cookie → 加载主站并自动点「登录」拉起扫码弹层
    → 监听 ``cookieAdded`` → 会话 cookie 落库即登录成功：提取全部抖音域名
    cookie → DPAPI 加密保存 → 发信号并关窗。

    **为什么打开先清 cookie**（2026-10-19 本机实测，PySide6 6.12.0）：
    ``QWebEngineCookieStore.loadAllCookies()`` **方法存在、可调用**（独立审核
    2026-10-20 用 ``hasattr`` + ``dir()`` 复核确认），但**不接受回调、无返回值**，
    拿不到既有 cookie —— 读取只能靠 ``cookieAdded`` 信号累积；而连接信号时
    **既有 cookie 不会补发**（审核方另做了播种复现：``setCookie()`` 写入一颗
    ``.douyin.com`` cookie 后连接中的 ``cookieAdded`` 不触发，事后调
    ``loadAllCookies()`` 也不触发）。不清空的话，「重新登录」可能永远等不到新的
    ``sessionid``，只能耗到 5 分钟超时。``deleteAllCookies()`` 之后加载的
    页面设置的 cookie 不受影响（实测存活），登录页自身的 cookie（ttwid 等）
    能正常收齐。副作用仅限本应用自用的 default profile，不碰用户系统浏览器。

    超时：窗口打开起 5 分钟未登录自动放弃（由信号表达，不弹骚扰对话框）；
    用户手动关窗同样视为放弃。
    """

    #: 登录成功（cookie 已捕获并加密保存）。
    login_succeeded = Signal()
    #: 放弃 / 失败。``reason`` 为不透明短码（timeout / user-closed / save-failed），
    #: 隐私铁律：绝不含 cookie 值或任何可能逼近密钥的细节。
    login_abandoned = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("抖音登录")
        self.resize(440, 640)

        self._cookies: dict[str, str] = {}
        self._finished = False

        self._hint = QLabel("请用抖音 App 扫码登录；登录成功后本窗口会自动关闭。")
        self._hint.setWordWrap(True)
        self._view = QWebEngineView()
        layout = QVBoxLayout(self)
        layout.addWidget(self._hint)
        layout.addWidget(self._view, stretch=1)

        # cookieStore 属于 defaultProfile：跨运行持久化，是「缓存复用」的
        # 浏览器侧基础。profile 与 store 的 Python 包装都必须持有强引用——
        # 实测不持有 profile 引用时，后续调用会报
        # "Internal C++ object already deleted"（包装器被 GC 殃及底层对象）。
        self._profile = QWebEngineProfile.defaultProfile()
        self._store = self._profile.cookieStore()
        self._store.cookieAdded.connect(self._on_cookie_added)
        self._view.loadFinished.connect(self._on_load_finished)

        self._open_login_page()

    # -- 登录页 ------------------------------------------------------------------

    def _open_login_page(self) -> None:
        """清空旧 cookie、打开主站，起 5 分钟超时计时器。

        入口 URL 依据见模块常量 :data:`_LOGIN_URL` 上方注释（2026-10-20 实测）。
        清 cookie 的必要性见类文档字符串（``loadAllCookies`` 存在但无回调、
        拿不到既有 cookie；``cookieAdded`` 也不补发既有 cookie）。
        """
        self._store.deleteAllCookies()
        self._view.load(QUrl(_LOGIN_URL))
        QTimer.singleShot(_TIMEOUT_MS, self._on_timeout)

    #: 自动点「登录」注入的 JS：找第一个可见且文案恰为「登录」的元素点击。
    #: best-effort——页面改版、按钮文案变化时静默不命中，兜底走提示文案。
    _CLICK_LOGIN_JS = (
        "(() => { const els = [...document.querySelectorAll('button, a, div, span')]"
        ".filter(e => e.offsetWidth > 0 && e.innerText && e.innerText.trim() === '登录')"
        "; if (els.length) { els[0].click(); return true; } return false; })()"
    )

    def _auto_open_login_sheet(self) -> None:
        """主站加载完成后自动点「登录」，拉起扫码弹层（best-effort）。

        2026-10-20 实测：点击后 ``get_qrcode`` / ``check_qrconnect`` 全链路发出、
        二维码渲染成功。选择器失效时静默放弃——提示文案已引导用户手动点击，
        自动化只是省一步，不是唯一通路。
        """
        if self._finished:
            return
        self._view.page().runJavaScript(self._CLICK_LOGIN_JS)

    def _on_load_finished(self, ok: bool) -> None:
        """页面加载结果只改提示文案；失败时给中文可读原因（不含技术细节）。"""
        if self._finished:
            return
        if ok:
            self._hint.setText(
                "请用抖音 App 扫码登录；登录成功后本窗口会自动关闭。\n"
                "如未自动显示二维码，请点击页面右上角「登录」。"
            )
            QTimer.singleShot(_AUTO_CLICK_DELAY_MS, self._auto_open_login_sheet)
        else:
            self._hint.setText("登录页面加载失败，请检查网络连接后关闭本窗口重试。")

    # -- cookie 捕获 ---------------------------------------------------------------

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        """``cookieAdded`` 回调：累积抖音域名 cookie，会话 cookie 出现即完成登录。

        会话 cookie 名（:data:`_SESSION_COOKIE_NAMES`）出现**且值非空**才算数；
        重复到达时后者覆盖前者（cookie 刷新是正常现象）。
        """
        if self._finished:
            return
        if not _is_douyin_domain(cookie.domain()):
            return
        name = bytes(cookie.name()).decode("utf-8", "replace")
        value = bytes(cookie.value()).decode("utf-8", "replace")
        self._cookies[name] = value
        if name in _SESSION_COOKIE_NAMES and value:
            self._complete_login()

    def _complete_login(self) -> None:
        """登录完成：加密保存 cookie → 发成功信号 → 关窗。

        保存失败时改发放弃信号（短码 ``save-failed``）——隐私：异常细节
        （消息里只有文件路径，但也不冒这个险）不外抛、不记录。
        """
        self._finished = True
        try:
            save_cookies(self._cookies)
        except Exception:
            self.login_abandoned.emit("save-failed")
        else:
            self.login_succeeded.emit()
        self.close()

    def _on_timeout(self) -> None:
        """5 分钟超时：放弃并关闭（返回态由信号表达）。"""
        if self._finished:
            return
        self._finished = True
        self.login_abandoned.emit("timeout")
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """用户手动关窗 = 放弃；已完成 / 已放弃过的不重复发信号。"""
        if not self._finished:
            self._finished = True
            self.login_abandoned.emit("user-closed")
        super().closeEvent(event)
