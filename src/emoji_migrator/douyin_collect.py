"""抖音收藏表情采集（EM-005-C）：curl_cffi TLS 指纹 + 401 过期探测（D-15）。

设计依据（``docs/09-抖音登录链路实测.md``）：登录 cookie 由
``douyin_auth``（EM-005-B，QWebEngineView 官方登录）捕获并 DPAPI 加密缓存；
本模块只做**采集**——带 cookie 请求收藏列表、下载表情文件。HTTP 客户端用
``curl_cffi``（Chrome TLS 指纹）：spike 实测它能穿过抖音前两层风控（裸 curl
被「非法应用」拦），普通接口层（非登录页 bdms 签名层）是本模块的活动空间。

**A-4 已实测（2026-10-20，真人 cookie 真机联调，RUN-20261020-006）**：端点、
query、响应结构均已校准（见 :data:`LIST_URL` 与 :func:`_extract_stickers` 注释）；
a_bogus 做了**三组对照实验**（无 / 真 / 假签名均 200 + 业务码 0）——本端点
**当前不强制查询签名**，cookie + Chrome TLS 指纹是实际门槛；vendor 签名保留为
默认（防服务端未来收紧），直通签名器保留为显式降级选项。

**翻页（EM-005-D 新增）**：本包新增 :func:`fetch_page`——带 ``custom_cursor``
游标拉单页并返回 :class:`StickerPage`（含翻页信息）；:func:`fetch_sticker_list`
**保持原签名与语义不变**（取首页、返回 list），故既有调用方零改动。
⚠️ 翻页的响应结构**未在真机验证**——实测 41 条一页即 ``is_completed=True``，
超页场景从未发生。终止条件（空页 / ``has_more`` 假 / 取不到游标）**偏保守**：
宁可能少拿，不死循环；硬上限由调用方 :mod:`emoji_migrator.collect_flow` 兜底。

**D-15 口径**：登录态过期**以服务端 401 为准**（:class:`SessionExpired`），
不硬编码有效期天数——实际有效期由抖音决定，客户端猜数会误判。

**隐私铁律**：本模块任何代码路径不输出 cookie 值——异常消息只含 HTTP 状态码
与域名级信息，无 print / 无 logging；cookie 只作为请求参数在内存中流转。

**依赖方向**（``AGENTS.md`` §3）：叶子模块，只 import 标准库、curl_cffi 与包内
**vendor 叶子** ``abogus``（f2 a_bogus 签名，Apache-2.0；其 SM3 依赖以
``_gmssl_sm3`` / ``_gmssl_func`` 一并 vendor，BSD——见各自 provenance 块）。
cookie 由调用方（``cli`` / ``gui``）从 ``douyin_auth`` 读出后**注入**，
本模块不 import ``douyin_auth``（业务模块互不依赖）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from curl_cffi import requests as curl_requests

from emoji_migrator.abogus import ABogus

__all__ = [
    "CollectionError",
    "DownloadNotAllowedError",
    "IMPERSONATE",
    "LIST_URL",
    "PAGE_SIZE",
    "SESSION_EXPIRED_STATUS",
    "SessionExpired",
    "Signer",
    "Sticker",
    "StickerPage",
    "USER_AGENT",
    "download_sticker",
    "fetch_page",
    "fetch_sticker_list",
]

#: 收藏表情列表端点（**A-4 已实测**，2026-10-20 真人 cookie 校准）。
#:
#: 实测依据：占位端点 ``/aweme/v1/web/emotioncollection/item/list/`` 等 4 个
#: 候选全部 404 后，从主站 IM chunk（client-entry~5.46f927bf.js）grep 出
#: 「表情面板-我的收藏」的真实调用——``scenes=CUSTOM_STICKER_PAGE`` 打到
#: ``/aweme/v1/web/im/resource/list/aggregation``（GET）。真机验证：HTTP 200、
#: 业务码 0、41 条收藏一页返回（``is_completed=True``）。
#: 注意 IM 家族用 ``aid=1128``（不是主站 feed 的 6383）。
LIST_URL = "https://www.douyin.com/aweme/v1/web/im/resource/list/aggregation"

#: 下载白名单域后缀：抖音系图床 / 静态资源域。A-4 实测（2026-10-20）：收藏
#: 表情下载域实测落 ``p3/p26-im-emoticon-sign.byteimg.com``——``byteimg.com``
#: 后缀已覆盖。其余后缀保留（下载域随资源类型可能变化）——**只增不减**，
#: 白名单收紧不是本模块的权。
_DOWNLOAD_ALLOWED_SUFFIXES = (
    "douyinstatic.com",  # 抖音静态资源
    "douyinpic.com",  # 抖音图片 CDN
    "byteimg.com",  # 字节跳动统一图床（列表返回的 url 多落此域）
    "zjcdn.com",  # 字节 CDN
)

#: curl_cffi 的 Chrome TLS 指纹档位。spike 实测（2026-10-19，同版本 0.16.3）
#: ``chrome145`` 可穿过抖音前两层风控——裸 curl 被拦、指纹可进 4031 层。
IMPERSONATE = "chrome145"

#: 登录态过期的服务端判据（D-15）：HTTP 401。若真机联调发现抖音用 JSON
#: 业务码表达过期，在 :func:`_raise_for_status` 扩展映射——**不引入天数**。
SESSION_EXPIRED_STATUS = 401

#: 单页请求条数（``custom_page_size`` query 参数）。
#:
#: **EM-005-D 新增**：原先该字面量内联在 :func:`fetch_sticker_list` 的 query 里，
#: 翻页实现需要按它判断「本页是否满页」（满页才可能还有下一页），
#: 故提为模块常量——**调用方只需读它，不必复写这个数字**。
PAGE_SIZE = 50

#: 请求超时（秒）。列表与下载共用；网络侧失败抛常规传输异常，不吞。
_TIMEOUT_SECONDS = 15.0

#: 桌面 Chrome UA。TLS 指纹与 UA 同族是**设计依据而非实测结论**：spike 实测
#: （docs/09，2026-10-19）只证明指纹层可穿透前两层风控，「UA/指纹必须一致」
#: 是社区通行口径、未做不一致对照实验——真机联调（A-4）时一并验证。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)


class SessionExpired(Exception):
    """登录态已过期（服务端 401，D-15）。调用方应引导用户重新扫码。"""


class CollectionError(Exception):
    """采集侧一般失败：参数缺失、响应不可解析、下载失败等。"""


class DownloadNotAllowedError(CollectionError):
    """下载 URL 不在域名白名单内——拒绝出网，宁可失败不越界。"""


#: 查询签名器：``(query, user_agent) -> signed query``。
#:
#: 抖音普通接口普遍要求 ``a_bogus`` 等查询签名（spike G1：纯 Python 可生成）。
#: **D-17 已落地**（2026-10-20 人工授权）：默认签名器用 vendor 的 f2 ``ABogus``
#: （见 :func:`_abogus_signer` 与 :mod:`emoji_migrator.abogus` 的 provenance 块）。
#: 注入点保留：签名函数签名固定、不触碰 cookie（签名输入只有 query 与 UA），
#: 测试与降级排查可显式注入 :func:`_passthrough_signer`（不签名直通）。
Signer = Callable[[dict[str, str], str], dict[str, str]]


#: a_bogus 查询参数名。f2 ``ABogus.generate_abogus`` 返回 4 元组，a_bogus 在
#: 下标 [1]（上游源码 ``return (params, abogus, self.user_agent, body)``）。
_ABOGUS_PARAM_KEY = "a_bogus"


def _abogus_signer(query: dict[str, str], user_agent: str) -> dict[str, str]:
    """默认签名器（D-17）：vendor 的 f2 ``ABogus`` 为 query 追加 a_bogus。

    签名输入只有 query 与 UA：抖音查询签名不涉及 cookie，本函数结构上
    拿不到 cookie。实现口径：先 ``urlencode`` 出与最终请求一致的参数串再
    签名——``fetch_sticker_list`` 对返回字典再次 ``urlencode`` 时，dict 保序
    保证「原参数在前、a_bogus 收尾」，与签名输入串一致。

    ⚠️→✅ A-4 对照结论（2026-10-20）：无 / 真 / 假 a_bogus 三组请求均
    HTTP 200 + 业务码 0——**该端点当前不强制查询签名**。真实签名保留为默认
    （防服务端未来收紧；真签名实测兼容），直通保留为显式降级选项。
    """
    signed = dict(query)
    signed[_ABOGUS_PARAM_KEY] = ABogus(user_agent=user_agent).generate_abogus(
        urlencode(query)
    )[1]
    return signed


def _passthrough_signer(query: dict[str, str], user_agent: str) -> dict[str, str]:
    """直通签名器：原样返回 query（不签名）。

    D-17 落地后从「默认占位」降为**显式注入**选项（测试 / 降级排查用）；
    真机联调若证明列表接口暂不校验签名，它仍可作终态回退。
    """
    return dict(query)


@dataclass(frozen=True)
class Sticker:
    """一条收藏表情：业务 id + 下载地址。字段是提取后的最小集。"""

    sticker_id: str
    url: str


def _http_get(url: str, headers: dict[str, str], cookies: dict[str, str]) -> tuple[int, bytes]:
    """统一 GET 出口（测试在此打桩）：返回 ``(HTTP 状态码, 响应体)``。

    集中一个出口的好处：impersonate / timeout / cookie 的传递只写一遍，
    测试 monkeypatch 本函数即可拦截全部网络。
    """
    response = curl_requests.get(
        url,
        headers=headers,
        cookies=cookies,
        timeout=_TIMEOUT_SECONDS,
        impersonate=IMPERSONATE,
    )
    return int(response.status_code), response.content


def _raise_for_status(status: int) -> None:
    """状态码 → 语义异常：401 即登录态过期（D-15），其余由调用方处置。"""
    if status == SESSION_EXPIRED_STATUS:
        raise SessionExpired("登录态已过期（服务端 401），请重新扫码登录抖音")


def _require_cookies(cookies: dict[str, str] | None) -> dict[str, str]:
    """cookie 入参校验：缺失或全空值即失败，**不发无谓请求**。消息不含 cookie 内容。

    「全空值」也按缺失处理（``douyin_auth.load_cached_cookie`` 保证缓存里
    至少有一个非空会话 cookie，这里是对任意来源的防御）。
    """
    if not cookies or not any(cookies.values()):
        raise CollectionError("缺少抖音登录 cookie，请先完成扫码登录抖音")
    return cookies


def _headers() -> dict[str, str]:
    """请求头：UA 同族于 TLS 指纹（见 :data:`USER_AGENT` 注释）+ 主站 Referer。

    Referer 按浏览器同源请求的实况带上——真实页面发起该接口时必带，与其一致。
    """
    return {"User-Agent": USER_AGENT, "Referer": "https://www.douyin.com/"}


@dataclass(frozen=True)
class StickerPage:
    """一页收藏表情：条目 + 翻页游标信息（EM-005-D 新增）。

    :param stickers: 本页条目（可能为空——空页是翻页终止的正常信号）。
    :param next_cursor: 下一页游标；``None`` 表示**没有下一页**（翻页终止）。
    :param has_more: 服务端显式声明的「还有更多」。``None`` = 响应里没这个字段
        （不假设，交由调用方按「满页则继续」推断）。
    """

    stickers: tuple[Sticker, ...]
    next_cursor: str | None
    has_more: bool | None


def fetch_page(
    cookies: dict[str, str] | None,
    cursor: str = "0",
    signer: Signer = _abogus_signer,
) -> StickerPage:
    """拉取一页收藏表情（**EM-005-D 翻页能力**，向后兼容 :func:`fetch_sticker_list`）。

    与 :func:`fetch_sticker_list` 的差别只有两点：query 带 ``custom_cursor``
    游标、返回值多带翻页信息。其余流程（cookie 校验 → 签名 → GET → 401 判定
    → JSON 解析 → 提取）完全共用，不重复实现。

    **翻页口径（诚实边界）**：游标字段名以 ``next_cursor`` 为口径，
    该结构**未在真机验证**——A-4 实测单页41 条即 ``is_completed=True``，
    超页场景从未发生。取不到游标 / ``has_more`` 为假 / 本页为空，
    三者任一即返回 ``next_cursor=None`` 表示终止：宁可能少拿，
    不可死循环（上限由调用方兜底）。
    """
    verified = _require_cookies(cookies)
    query = signer(
        {
            "device_platform": "webapp",
            "version_code": "170400",
            "version_name": "17.4.0",
            "aid": "1128",  # IM 资源家族的固定 aid（主站 feed 是 6383）
            "app_id": "1128",
            "scenes": "CUSTOM_STICKER_PAGE",  # 表情面板-我的收藏
            "custom_cursor": cursor,
            "custom_page_size": str(PAGE_SIZE),
        },
        USER_AGENT,
    )
    status, body = _http_get(f"{LIST_URL}?{urlencode(query)}", _headers(), verified)
    _raise_for_status(status)
    if status != 200:
        raise CollectionError(f"收藏列表请求失败：HTTP {status}")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CollectionError("收藏列表响应不是合法 JSON（端点可能已变更，需校准）") from None
    return _build_page(payload)


def _build_page(payload: object) -> StickerPage:
    """从响应构造 :class:`StickerPage`：条目走精确提取，翻页字段宽容读取。"""
    stickers = _extract_stickers(payload)
    meta = _page_meta(payload)

    next_cursor = _first_str(meta, ("next_cursor", "cursor")) if meta else None
    has_more = meta.get("has_more") if meta else None
    if not isinstance(has_more, bool):
        has_more = None

    # 终止判定：空页 / 服务端说没有 / 取不到游标 → 都没有下一页。
    # 唯一「可能还有」的信号是「本页恰好满页」，此时必须有游标才能续。
    if not stickers or has_more is False or not next_cursor:
        return StickerPage(stickers=tuple(stickers), next_cursor=None, has_more=has_more)
    if len(stickers) < PAGE_SIZE and has_more is not True:
        return StickerPage(stickers=tuple(stickers), next_cursor=None, has_more=has_more)
    return StickerPage(stickers=tuple(stickers), next_cursor=next_cursor, has_more=has_more)


def _page_meta(payload: object) -> dict[str, object]:
    """取 ``custom_sticker_page_list`` 层的**翻页字段**，读不到返回空 dict。

    与 :func:`_extract_stickers` 分工：那里取条目（实测校准过的精确结构），
    这里只读翻页元信息（**未实测**，故宽容——字段缺失不抛异常，
    因为「读不到游标」只是「不翻页」，不该让整批采集失败）。
    """
    if not isinstance(payload, dict):
        return {}
    page = payload.get("custom_sticker_page_list")
    if not isinstance(page, dict):
        return {}
    return dict(page)


def fetch_sticker_list(
    cookies: dict[str, str] | None,
    signer: Signer = _abogus_signer,
) -> list[Sticker]:
    """拉取收藏表情**首页**列表，返回 :class:`Sticker` 列表（**单页**）。

    保留此函数是为向后兼容（GUI / 测试 / 既有调用方都依赖它）；
    需要翻页的调用方请用 :func:`fetch_page`。

    流程：cookie 校验 → 组装query → 签名器 → GET → 401 判定（D-15）→
    JSON 解析 → 宽容提取。任何失败都抛异常（不返回半截结果）。

    ⚠️ A-4 已实测（2026-10-20）：query 与响应结构均为真机校准后口径。
    """
    return list(fetch_page(cookies, cursor="0", signer=signer).stickers)


def _extract_stickers(payload: object) -> list[Sticker]:
    """从列表响应里提取表情条目（**A-4 实测后收紧为精确结构**）。

    实测结构（2026-10-20，41 条真机验证）::

        payload.custom_sticker_page_list.resources[].stickers[]
        条目: id_str（业务 id）+ static_url.url_list[0]（下载地址）
              另有 animate_url（webp 动图，同域名）/ width / height

    结构变更时按「需校准」报错——不猜、不返回半截结果；错误消息带结构提示、
    不带响应体（响应体可能包含用户数据，不外泄）。
    """
    if not isinstance(payload, dict):
        raise CollectionError("收藏列表响应结构无法解析（需校准）")
    page = payload.get("custom_sticker_page_list")
    if not isinstance(page, dict):
        raise CollectionError("收藏列表响应缺少 custom_sticker_page_list 层（需校准）")
    resources = page.get("resources")
    if not isinstance(resources, list):
        raise CollectionError("收藏列表响应缺少 resources 列表（需校准）")

    stickers: list[Sticker] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        items = resource.get("stickers")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            sticker_id = _first_str(item, ("id_str", "id"))
            static_url = item.get("static_url")
            url_list = static_url.get("url_list") if isinstance(static_url, dict) else None
            url = (
                url_list[0]
                if isinstance(url_list, list) and url_list and isinstance(url_list[0], str)
                else None
            )
            if sticker_id and url:
                stickers.append(Sticker(sticker_id=sticker_id, url=url))
    return stickers


def _first_str(item: dict[str, object], keys: tuple[str, ...]) -> str | None:
    """按候选键序取第一个非空字符串值。

    两处共用：条目提取（``id_str`` / ``id``）与翻页字段（``next_cursor`` / ``cursor``）。
    值必须是非空字符串——数字型游标会被跳过，宁可不翻页也不猜。
    """
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def download_sticker(
    sticker: Sticker,
    target: Path,
    cookies: dict[str, str] | None,
) -> Path:
    """下载单条表情到 ``target``，返回写入路径。

    白名单（:data:`_DOWNLOAD_ALLOWED_SUFFIXES`）按**域名后缀**判定：
    非白名单域直接拒绝（不出网、不写文件）。写文件在请求成功之后，
    失败路径不留半个文件。
    """
    verified = _require_cookies(cookies)
    host = urlsplit(sticker.url).hostname or ""
    host = host.casefold().lstrip(".")
    if not any(host == base or host.endswith("." + base) for base in _DOWNLOAD_ALLOWED_SUFFIXES):
        raise DownloadNotAllowedError(f"下载域名不在白名单内：{host or '(无法解析)'}")

    status, body = _http_get(sticker.url, _headers(), verified)
    _raise_for_status(status)
    if status != 200 or not body:
        raise CollectionError(f"表情下载失败：HTTP {status}（sticker_id={sticker.sticker_id}）")
    target.write_bytes(body)
    return target
