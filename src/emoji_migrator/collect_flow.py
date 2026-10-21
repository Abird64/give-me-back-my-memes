"""抖音采集编排（EM-005-D）：翻页 → 下载 → WebP 转换。

本模块是 **cli / gui 共用的编排层**，把三个业务模块串起来：

1. :mod:`~emoji_migrator.douyin_auth` —— 由调用方读出DPAPI 缓存的登录 cookie
   （本模块不import它：**cookie 注入是编排细节，不进业务层**）；
2. :mod:`~emoji_migrator.douyin_collect` —— 拉收藏页、下载文件（curl_cffi 指纹）；
3. :mod:`~emoji_migrator.webp_gif` —— WebP 转 GIF（抖音动图就是 WebP）。

**依赖方向**（``AGENTS.md`` §3）：本模块是**编排层**，允许 import 上述业务模块；
自身不实现算法——翻页判定在 ``fetch_page``、转换在 ``webp_gif``，
本模块只负责**串起来 + 计数 + 错误文案**。

**为什么单独成模块而不是写进 ``cli.py``**：`gui` 与 `cli` 都要用它。
写进 ``cli`` 会让 gui 反向依赖 ``cli`` 的私有函数。（``gui.run_export``
已复用 ``cli._normalize_dir``，那是EM-004 的既有约定，不在本包扩大。）

**隐私铁律**：本模块不持有、不打印、不写盘任何 cookie 值；cookie 只作为参数
在内存中流转。异常与 :attr:`CollectOutcome.failures` 只含业务语义
（sticker_id + 原因），**不含 cookie、不含响应体**。

**分页口径**：游标来自 :class:`~emoji_migrator.douyin_collect.StickerPage`，
**不自己造游标**。翻页在三种情况终止：服务端不再给游标 / 本页为空 /
达到 :data:`MAX_PAGES` 硬上限（打印提示，不静默截断）。上限是**防呆**——
防的是「服务端行为异常时客户端无限循环」，不是产品语义。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from emoji_migrator.douyin_collect import (
    CollectionError,
    DownloadNotAllowedError,
    SessionExpired,
    Sticker,
    StickerPage,
    download_sticker,
    fetch_page,
)
from emoji_migrator.webp_gif import convert_webp_to_gif

__all__ = [
    "MAX_PAGES",
    "NO_COOKIE_HINT",
    "PAGE_LIMIT_HINT",
    "CollectOutcome",
    "collect_douyin_stickers",
]

#: 翻页硬上限。**防呆用，不是产品语义**——实测 41 条一页拿完，正常用户远够不到。
#: 按每页 50 条算，:data:`MAX_PAGES` 页对应上万条，远超任何人的收藏量。
#: 设它的唯一目的：服务端若忽略游标反复返回同一页，客户端不至于无限循环。
MAX_PAGES = 200

#: 无 cookie 时的中文提示。**用户能看懂，不含十六进制错误码**。
NO_COOKIE_HINT = "请先运行 GUI 点「登录抖音」完成扫码，再回来采集。"

#: 命中翻页上限时的提示（显式告知，不静默截断）。
PAGE_LIMIT_HINT = (
    f"已达翻页上限（{MAX_PAGES} 页），可能未取全；"
    "若收藏确实更多，请告知以便核对服务端分页行为。"
)

#: 原始下载（WebP）的目录名——转换成功即删除，故放在产物目录下的子目录，
#: 用户一眼能看出那是中间态。转换失败的文件**保留**在此目录供排查。
_RAW_DIR_NAME = "_raw"


@dataclass
class CollectOutcome:
    """一次采集的完整结果，供 CLI 打印计数、GUI 显示摘要。"""

    #: 下载成功（拿到 WebP 字节）的条目数
    downloaded: int = 0
    #: WebP → GIF 转换成功数
    converted: int = 0
    #: 下载失败条目数
    failed: int = 0
    #: 转换失败条目数（原始 WebP 保留在 :attr:`raw_dir` 供排查）
    convert_failed: int = 0
    #: 实际翻页数
    pages: int = 0
    #: 各页条目累计（**含跨页重复**，去重前）
    listed: int = 0
    #: 跨页重复被丢弃数（按 ``sticker_id`` 去重）
    duplicates: int = 0
    #: 是否命中翻页上限（即可能被截断）
    hit_page_limit: bool = False
    #: 失败明细 ``(sticker_id, 阶段, 原因)``——原因不含 cookie / 响应体
    failures: list[tuple[str, str, str]] = field(default_factory=list)
    #: 原始下载目录
    raw_dir: Path | None = None
    #: 转换产物目录
    out_dir: Path | None = None

    @property
    def total_collected(self) -> int:
        """本轮真正产出的文件数（转换成功 + 转换失败但已下载）。"""
        return self.converted + self.convert_failed

    @property
    def ok(self) -> bool:
        """本轮是否「干净完成」：至少拿到一个条目，且**没有任何失败**。

        用于退出码：全部成功 → 0；有任何一条失败或什么都没拿到 → 1。
        区分「一个都没拿到」（空收藏 / 未登录）与「拿到了但部分失败」。
        """
        return self.downloaded > 0 and not self.failures


def collect_douyin_stickers(
    cookies: dict[str, str] | None,
    out_dir: Path,
    raw_dir: Path | None = None,
    signer: Callable[..., dict[str, str]] | None = None,
    downloader: Callable[[Sticker, Path], None] | None = None,
    converter: Callable[[Path, Path], None] | None = None,
    fetcher: Callable[..., StickerPage] | None = None,
    max_pages: int = MAX_PAGES,
) -> CollectOutcome:
    """采集 → 下载 → WebP 转 GIF，返回 :class:`CollectOutcome`。

    :param cookies: 由 :func:`~emoji_migrator.douyin_auth.load_cached_cookie`
        解出的 cookie 字典。``None`` / 全空 → 抛
        :class:`~emoji_migrator.douyin_collect.CollectionError`
        （消息含 :data:`NO_COOKIE_HINT`）。
    :param out_dir: 转换产物目录（GIF）。自动创建。
    :param raw_dir: 原始 WebP 目录。``None`` 时用 ``out_dir / "_raw"``。
    :param signer: 查询签名器，透传给 ``fetch_page``。
    :param downloader: 下载函数 ``(Sticker, Path) -> None``，测试打桩用。
    :param converter: 转换函数 ``(source, target) -> None``，测试打桩用。
    :param fetcher: 取页函数，测试打桩用（可返回构造好的 :class:`StickerPage`）。
    :param max_pages: 翻页硬上限。**``0`` 与负数视为 1**（只取首页）——
        上限是防呆参数，不允许「不翻页」这种隐式行为。

    **异常口径**：401 由 :class:`~emoji_migrator.douyin_collect.SessionExpired`
    与 :class:`DownloadNotAllowedError` **直接上抛**（前者要引导重新登录，
    后者是安全边界，都不该被逐条吞掉）；逐条下载失败（网络错 / 超时）
    记入 :attr:`CollectOutcome.failures` 后继续下一条，原因压成中文短句。

    **取页失败**（网络不通 / 代理拦截）**整批上抛** :class:`CollectionError`——
    列表都拿不到时逐条重试没有意义，给用户一个中文原因即可。
    """
    if not cookies or not any(cookies.values()):
        raise CollectionError(NO_COOKIE_HINT)

    _init_network_errors()

    resolved_out = Path(out_dir)
    resolved_raw = Path(raw_dir) if raw_dir is not None else resolved_out / _RAW_DIR_NAME
    resolved_out.mkdir(parents=True, exist_ok=True)
    resolved_raw.mkdir(parents=True, exist_ok=True)

    do_fetch = fetcher if fetcher is not None else _bound_fetcher(cookies, signer)
    do_download = downloader if downloader is not None else _bound_downloader(cookies)
    do_convert = converter if converter is not None else _default_converter

    outcome = CollectOutcome(raw_dir=resolved_raw, out_dir=resolved_out)
    seen: set[str] = set()
    cursor = "0"
    page_budget = max(1, max_pages)

    for _ in range(page_budget):
        try:
            page = do_fetch(cursor)
        except (SessionExpired, DownloadNotAllowedError, CollectionError):
            raise
        except (*_NETWORK_ERRORS, OSError) as exc:
            # 取页失败 → 整批中止并给中文原因（列表都拿不到，逐条重试没意义）。
            # 真实message 不外泄：可能含代理地址、内网主机名。
            raise CollectionError(f"采集列表失败：{_readable_reason(exc)}") from None
        outcome.pages += 1
        outcome.listed += len(page.stickers)
        if not page.stickers:
            break

        for sticker in page.stickers:
            if sticker.sticker_id in seen:
                outcome.duplicates += 1
                continue
            seen.add(sticker.sticker_id)
            _handle_one(sticker, resolved_raw, resolved_out, do_download, do_convert, outcome)

        if not page.next_cursor:
            break
        cursor = page.next_cursor
    else:
        # for-else：循环自然走完 = 还有下一页但预算耗尽 = 可能被截断
        outcome.hit_page_limit = True

    return outcome


def _handle_one(
    sticker: Sticker,
    raw_dir: Path,
    out_dir: Path,
    download: Callable[[Sticker, Path], None],
    convert: Callable[[Path, Path], None],
    outcome: CollectOutcome,
) -> None:
    """单条：下载 → 转换。逐条失败只记账不抛（除安全边界异常外）。

    文件名用 ``sticker_id``（业务 id，非 URL）——URL 里带查询串且长度不受控，
    直接当文件名会踩 Windows 路径限制。同 id 不重复处理（调用方已去重）。
    """
    _init_network_errors()  # 幂等：直接调本函数（测试）时也要确保 except 元组可用
    source = raw_dir / f"{sticker.sticker_id}.webp"
    target = out_dir / f"{sticker.sticker_id}.gif"
    try:
        download(sticker, source)
    except (DownloadNotAllowedError, SessionExpired):
        raise
    except (*_NETWORK_ERRORS, CollectionError, OSError) as exc:
        outcome.failed += 1
        outcome.failures.append((sticker.sticker_id, "下载", _readable_reason(exc)))
        source.unlink(missing_ok=True)
        return

    outcome.downloaded += 1
    try:
        convert(source, target)
    except (OSError, ValueError) as exc:
        # 转换失败保留原始 WebP 供排查——删了用户就只剩一条错误信息。
        outcome.convert_failed += 1
        outcome.failures.append((sticker.sticker_id, "转换", _readable_reason(exc)))
        return

    outcome.converted += 1
    # 转换成功即清掉中间态：原始 WebP 对用户没有用途，留着只会让人以为
    # 那是可导入的表情（微信/QQ 都不收 WebP）。
    source.unlink(missing_ok=True)


#: 传输层异常类型的**占位**（真正取值见 :func:`_init_network_errors`）。
#:
#: 为何不在 import 期填：``curl_cffi`` 是可选依赖，import 期抓会让
#: 「只跑 QQ 功能」的机器直接起不来。
_NETWORK_ERRORS: tuple[type[BaseException], ...] = ()


def _init_network_errors() -> None:
    """首次采集时填充 :data:`_NETWORK_ERRORS`（原地赋值，供 ``except`` 子句用）。

    真机实测（2026-10-20）：代理不可达时 ``curl_cffi`` 抛
    ``curl_cffi.requests.exceptions.ConnectionError``——它**不继承 OSError**，
    ``except (CollectionError, OSError)`` 抓不住，异常一路冒泡成 traceback
    给用户看（实测症状）。
    """
    global _NETWORK_ERRORS
    if _NETWORK_ERRORS:
        return
    try:
        from curl_cffi.requests import exceptions as curl_exc
    except ImportError:  # pragma: no cover - 未装 curl_cffi 的环境
        _NETWORK_ERRORS = ()
        return
    base = getattr(curl_exc, "RequestException", None)
    names = (
        "ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout",
        "SSLError", "ProxyError", "TooManyRedirects", "HTTPError",
    )
    found = tuple(getattr(curl_exc, name) for name in names if hasattr(curl_exc, name))
    if base is not None and base not in found:
        found = (*found, base)
    _NETWORK_ERRORS = found


def _readable_reason(exc: BaseException) -> str:
    """把异常压成**用户能看懂**的中文短句。

    真机实测踩过的两种难读消息：
    - ``curl: (28) Operation timed out after 15009 milliseconds
      with 296221 of 748700 bytes received`` → 超时
      （含「收到一半」这种细节，用户不需要）
    - ``curl: (7) CONNECT tunnel failed, response 502``
      → 代理/网络不通（对用户就是「网络不通」）

    只保留异常**类型名**与一句中文，**不透传原始串**——原始串可能含
    URL、代理地址、内网主机名等不该出现在用户面前的信息。
    """
    name = type(exc).__name__
    mapping = {
        "ConnectionError": "网络连接失败（可能被代理或防火墙拦截）",
        "Timeout": "下载超时（文件较大或网络较慢，可重试）",
        "ConnectTimeout": "连接超时（网络不通或被代理拦截）",
        "ReadTimeout": "读取超时（网络较慢，可重试）",
        "SSLError": "HTTPS 连接失败（证书或代理问题）",
        "ProxyError": "代理不可用（请检查网络设置）",
        "TooManyRedirects": "重定向次数过多（服务端异常）",
        "HTTPError": "HTTP 请求失败",
    }
    return mapping.get(name, f"{name}（详见技术日志）")


def _bound_fetcher(
    cookies: dict[str, str],
    signer: Callable[..., dict[str, str]] | None,
) -> Callable[..., StickerPage]:
    """绑定 cookie 的取页闭包，调用方只需传 ``cursor``。"""
    use_signer = signer if signer is not None else _default_signer

    def fetch(cursor: str) -> StickerPage:
        return fetch_page(cookies, cursor=cursor, signer=use_signer)

    return fetch


def _bound_downloader(cookies: dict[str, str]) -> Callable[[Sticker, Path], None]:
    """绑定 cookie 的下载闭包。"""

    def download(sticker: Sticker, target: Path) -> None:
        download_sticker(sticker, target, cookies)

    return download


def _default_signer(query: dict[str, str], user_agent: str) -> dict[str, str]:
    """默认查询签名器（D-17 vendor f2 a_bogus）。

    经 ``getattr`` 取而非直接 import 私有名：``_abogus_signer`` 是模块私有，
    跨模块直连等于把私有约定写进公共契约；这里做一次显式转接，
    并在测试里断言它确实是带签名的那个。
    """
    from emoji_migrator import douyin_collect

    return douyin_collect._abogus_signer(query, user_agent)  # noqa: SLF001


def _default_converter(source: Path, target: Path) -> None:
    """默认转换：WebP → GIF（Pillow 的解码/编码异常由调用方记入 failures）。"""
    convert_webp_to_gif(source, target)

