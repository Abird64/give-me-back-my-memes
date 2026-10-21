"""抖音收藏采集测试（EM-005-C）：全部网络打桩在 ``_http_get``，零真实出网。

命题清单：

- D-15：HTTP 401 → :class:`SessionExpired`，其余状态不走该语义；
- cookie 缺失 → 不发请求直接失败；
- 签名器可注入且输入只有 query + UA（结构上碰不到 cookie）；
- 下载域名白名单：非白名单域拒绝出网；
- 隐私铁律：任何失败路径的异常消息都不含 cookie 值。

响应样本以 A-4 真机实测结构（2026-10-20）为模板自造，值全部占位，不依赖
任何真机数据、不真实出网。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emoji_migrator import douyin_collect as dc

#: 假 cookie（结构真实、值是占位符）——隐私红线：真实 QQ 号 / 真实 cookie 不入仓。
FAKE_COOKIES = {"sessionid": "fake-session-value", "ttwid": "fake-ttwid-value"}

_LIST_RESPONSE = (
    b'{"custom_sticker_page_list": {"resources": [{'
    b'"stickers": ['
    b'{"id_str": "7400000000000000001", '
    b'"static_url": {"url_list": ["https://p3-im-emoticon-sign.byteimg.com/a.webp"], "uri": "a"}}, '
    b'{"id_str": "7400000000000000002", '
    b'"static_url": {"url_list": ["https://p26-im-emoticon-sign.byteimg.com/b.png"], "uri": "b"}}'
    b']}], "is_completed": true, "total_counts": 2}, "status_code": 0}'
)


class _Stub:
    """``_http_get`` 打桩器：按 URL 前缀返回预设响应并记录调用。"""

    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str], cookies: dict[str, str]):
        self.calls.append((url, headers, cookies))
        if not self.responses:
            raise AssertionError(f"意外多发请求：{url}")
        return self.responses.pop(0)


@pytest.fixture()
def stub_http(monkeypatch: pytest.MonkeyPatch):
    """安装打桩器并返回工厂：``stub([(...)]) -> Stub``。"""

    def install(responses: list[tuple[int, bytes]]) -> _Stub:
        spy = _Stub(responses)
        monkeypatch.setattr(dc, "_http_get", spy)
        return spy

    return install


# -- D-15：401 过期探测 ---------------------------------------------------------


def test_401_raises_session_expired(stub_http) -> None:
    """服务端 401 → SessionExpired（D-15：以服务端为准，不猜天数）。"""
    stub_http([(401, b"")])
    with pytest.raises(dc.SessionExpired):
        dc.fetch_sticker_list(dict(FAKE_COOKIES))


def test_500_is_collection_error_not_session_expired(stub_http) -> None:
    """非 401 的失败是一般采集错误——只有 401 才触发「重新扫码」语义。"""
    stub_http([(500, b"server error")])
    with pytest.raises(dc.CollectionError) as excinfo:
        dc.fetch_sticker_list(dict(FAKE_COOKIES))
    assert not isinstance(excinfo.value, dc.SessionExpired)


def test_download_401_also_raises_session_expired(stub_http, tmp_path: Path) -> None:
    """下载链路同样适用 401 判定（口径统一，D-15）。"""
    stub_http([(401, b"")])
    sticker = dc.Sticker(sticker_id="s1", url="https://p3-sign.douyinpic.com/a.webp")
    with pytest.raises(dc.SessionExpired):
        dc.download_sticker(sticker, tmp_path / "a.webp", dict(FAKE_COOKIES))


# -- cookie 入参与隐私 ----------------------------------------------------------


def test_missing_cookies_fails_without_request(stub_http) -> None:
    """cookie 缺失直接失败，绝不发无谓请求。"""
    spy = stub_http([])
    for bad in (None, {}, {"sessionid": ""}):
        with pytest.raises(dc.CollectionError):
            dc.fetch_sticker_list(bad)
    assert spy.calls == []


@pytest.mark.parametrize(
    ("func", "args"),
    [
        pytest.param("fetch", (), id="list"),
        pytest.param(
            "download",
            (dc.Sticker("s", "https://p.douyinpic.com/a.webp"), None),
            id="download",
        ),
    ],
)
def test_missing_cookies_message_contains_no_cookie_value(func, args) -> None:
    """失败消息零 cookie 内容（隐私铁律）。"""
    with pytest.raises(dc.CollectionError) as excinfo:
        if func == "fetch":
            dc.fetch_sticker_list(None)
        else:
            sticker, _ = args
            dc.download_sticker(sticker, Path("unused.webp"), None)
    assert "fake-session-value" not in str(excinfo.value)
    assert "fake-ttwid-value" not in str(excinfo.value)


def test_failure_messages_never_leak_cookie_values(stub_http, tmp_path: Path) -> None:
    """遍历所有失败路径：异常消息不含 cookie 值。

    口径注：cookie 作为**请求参数**在内存流转是正当的（``_http_get`` 入参
    必然携带）；隐私铁律约束的是「输出」——异常消息、日志、回显。这里对
    全部异常消息逐个断言。
    """
    stub_http([(500, b"err"), (403, b"forbidden")])
    with pytest.raises(dc.CollectionError) as exc_list:
        dc.fetch_sticker_list(dict(FAKE_COOKIES))
    sticker = dc.Sticker("s", "https://p.douyinpic.com/a.webp")
    with pytest.raises(dc.CollectionError) as exc_download:
        dc.download_sticker(sticker, tmp_path / "x.webp", dict(FAKE_COOKIES))
    for exc in (exc_list.value, exc_download.value):
        assert "fake-session-value" not in str(exc)
        assert "fake-ttwid-value" not in str(exc)


# -- 列表解析与签名器 ------------------------------------------------------------


def test_list_parses_calibrated_structure(stub_http) -> None:
    """A-4 实测结构（custom_sticker_page_list.resources[].stickers[]）正确提取 id + url。"""
    stub_http([(200, _LIST_RESPONSE)])
    stickers = dc.fetch_sticker_list(dict(FAKE_COOKIES))
    assert [s.sticker_id for s in stickers] == ["7400000000000000001", "7400000000000000002"]
    assert stickers[0].url.startswith("https://p3-im-emoticon-sign.byteimg.com/")
    assert stickers[1].url.startswith("https://p26-im-emoticon-sign.byteimg.com/")


def test_list_unknown_structure_raises_clean_error(stub_http) -> None:
    """未知结构给「需校准」错误——不猜、不返回半截结果（原子：无证据不写支持）。"""
    stub_http([(200, b'{"weird": {"nested": 1}}')])
    with pytest.raises(dc.CollectionError, match="校准"):
        dc.fetch_sticker_list(dict(FAKE_COOKIES))


def test_list_non_json_raises_clean_error(stub_http) -> None:
    stub_http([(200, b"<html>login page</html>")])
    with pytest.raises(dc.CollectionError, match="JSON"):
        dc.fetch_sticker_list(dict(FAKE_COOKIES))


# -- 翻页（EM-005-D）-------------------------------------------------------------
#
# ⚠️ 本节全部由**突变实验逼出来**：初版一个都没有（突变 M11「fetch_page 忽略游标」
# 与 M12「next_cursor 恒不返回」都改坏了却全绿）。翻页是本包的核心交付，
# 没有守护等于没做——教训见 AGENTS.md §6「跑绿不等于有保护」。


def _page_response(
    ids: list[str],
    *,
    next_cursor: str | None = None,
    has_more: bool | None = None,
) -> bytes:
    """构造一个 ``custom_sticker_page_list`` 响应（翻页场景用）。

    ``stickers`` 必须是**真正的 list 对象**，不能是字符串化的 JSON 片段——
    写成 f-string 嵌进 dict 会变成 str，提取函数直接跳过（曾栽过一次，
    表现为「造满页数据但页面返回 0 条」）。
    """
    import json

    stickers = [
        {"id_str": i, "static_url": {"url_list": [f"https://p3.byteimg.com/{i}.webp"]}}
        for i in ids
    ]
    meta: dict[str, object] = {}
    if next_cursor is not None:
        meta["next_cursor"] = next_cursor
    if has_more is not None:
        meta["has_more"] = has_more
    body = {
        "custom_sticker_page_list": {
            "resources": [{"stickers": stickers}],
            **meta,
        }
    }
    return json.dumps(body).encode("utf-8")


def _full_page(count: int) -> list[str]:
    """造恰好 ``PAGE_SIZE`` 条（满页）。"""
    return [f"id{i:04d}" for i in range(count)]


def test_fetch_page_sends_cursor_in_query(stub_http) -> None:
    """游标必须真的进 query——否则翻页只是循环同一个请求。

    这是突变 M11 的守护：``custom_cursor`` 被写死 "0" 时本测试必须变红。
    """
    spy = stub_http([(200, _page_response(["a", "b"]))])
    dc.fetch_page(dict(FAKE_COOKIES), cursor="CURSOR-XYZ")
    url = spy.calls[0][0]
    assert "custom_cursor=CURSOR-XYZ" in url, f"游标没进 query：{url}"


def test_fetch_page_first_page_uses_zero_cursor(stub_http) -> None:
    """首页游标恒为 "0"（实测口径），不带调用方传入值。"""
    spy = stub_http([(200, _page_response(["a"]))])
    dc.fetch_page(dict(FAKE_COOKIES))
    assert "custom_cursor=0" in spy.calls[0][0]


def test_fetch_page_sends_page_size_constant(stub_http) -> None:
    """页容量用模块常量，不在两处各写一个数字。"""
    spy = stub_http([(200, _page_response(["a"]))])
    dc.fetch_page(dict(FAKE_COOKIES))
    assert f"custom_page_size={dc.PAGE_SIZE}" in spy.calls[0][0]


def test_full_page_with_cursor_exposes_next_cursor(stub_http) -> None:
    """满页 + 有游标 → 暴露 next_cursor（供调用方翻页）。"""
    stub_http([(200, _page_response(_full_page(dc.PAGE_SIZE), next_cursor="CUR1"))])
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="0")
    assert len(page.stickers) == dc.PAGE_SIZE
    assert page.next_cursor == "CUR1"


def test_partial_page_ends_pagination(stub_http) -> None:
    """不满页 = 最后一页，即便响应里带了游标也不该继续。"""
    stub_http([(200, _page_response(["a", "b"], next_cursor="CUR1"))])
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="0")
    assert page.next_cursor is None


def test_has_more_false_ends_pagination_even_on_full_page(stub_http) -> None:
    """服务端明说没有更多 → 终止（哪怕满页且带游标）。"""
    stub_http(
        [(200, _page_response(_full_page(dc.PAGE_SIZE), next_cursor="CUR1", has_more=False))]
    )
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="0")
    assert page.next_cursor is None
    assert page.has_more is False


def test_has_more_true_overrides_partial_page(stub_http) -> None:
    """``has_more=True`` 是显式信号，即便不满页也续翻。"""
    stub_http([(200, _page_response(["a"], next_cursor="CUR1", has_more=True))])
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="0")
    assert page.next_cursor == "CUR1"


def test_missing_next_cursor_ends_pagination(stub_http) -> None:
    """取不到游标 → 终止（保守：宁可能少拿，不死循环）。"""
    stub_http([(200, _page_response(_full_page(dc.PAGE_SIZE)))])
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="0")
    assert page.next_cursor is None


def test_empty_page_ends_pagination(stub_http) -> None:
    """空页 → 无条目、无游标。"""
    stub_http([(200, _page_response([]))])
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="CUR1")
    assert page.stickers == ()
    assert page.next_cursor is None


def test_non_string_cursor_is_ignored(stub_http) -> None:
    """数字型游标不猜——跳过它，宁可不翻页（值必须是非空 str）。"""
    stub_http([(200, _page_response(_full_page(dc.PAGE_SIZE), next_cursor=12345))])
    page = dc.fetch_page(dict(FAKE_COOKIES), cursor="0")
    assert page.next_cursor is None


def test_sticker_page_is_exported_and_frozen() -> None:
    """``StickerPage`` 是公开契约：必须可导出、不可变（返回值语义）。"""
    assert "StickerPage" in dc.__all__
    assert "fetch_page" in dc.__all__
    page = dc.StickerPage(stickers=(), next_cursor=None, has_more=None)
    with pytest.raises((AttributeError, TypeError)):
        page.next_cursor = "x"  # type: ignore[misc]


def test_fetch_sticker_list_still_returns_plain_list(stub_http) -> None:
    """向后兼容：旧函数签名与返回类型不变（GUI / 既有调用方零改动）。"""
    stub_http([(200, _LIST_RESPONSE)])
    result = dc.fetch_sticker_list(dict(FAKE_COOKIES))
    assert isinstance(result, list)
    assert not isinstance(result, tuple)
    assert len(result) == 2


def test_fetch_sticker_list_forwards_signer(stub_http) -> None:
    """旧函数仍接受 signer 注入（既有测试与降级排查依赖它）。"""
    seen: list[dict[str, str]] = []

    def signing_signer(query, ua):
        seen.append(query)
        return {**query, "X-Signed": "1"}

    stub_http([(200, _LIST_RESPONSE)])
    dc.fetch_sticker_list(dict(FAKE_COOKIES), signer=signing_signer)
    assert seen, "signer 必须被调用"


def test_fetch_page_raises_session_expired_on_401(stub_http) -> None:
    """翻页请求同样要走 D-15 的 401 判定（不能只首页生效）。"""
    stub_http([(401, b"")])
    with pytest.raises(dc.SessionExpired):
        dc.fetch_page(dict(FAKE_COOKIES), cursor="CUR1")


def test_fetch_page_requires_cookies_without_request(stub_http) -> None:
    """无 cookie 不发请求（翻页路径同样守这条）。"""
    stub = stub_http([])
    with pytest.raises(dc.CollectionError):
        dc.fetch_page(None, cursor="0")
    with pytest.raises(dc.CollectionError):
        dc.fetch_page({"sessionid": ""}, cursor="0")
    assert not stub.calls


def test_signer_injects_signed_query_without_touching_cookies(stub_http) -> None:
    """签名器输出合并进最终 URL；签名器输入只有 query + UA（拿不到 cookie）。"""

    def signing_signer(query: dict[str, str], user_agent: str) -> dict[str, str]:
        assert "sessionid" not in query  # 结构上碰不到 cookie
        assert user_agent == dc.USER_AGENT
        return {**query, "a_bogus": "signed-by-test"}

    spy = stub_http([(200, _LIST_RESPONSE)])
    dc.fetch_sticker_list(dict(FAKE_COOKIES), signer=signing_signer)
    url = spy.calls[0][0]
    assert "a_bogus=signed-by-test" in url
    assert url.startswith(dc.LIST_URL)


def test_default_signer_appends_real_abogus(stub_http) -> None:
    """默认签名器（D-17）用真实 ABogus：URL 追加 a_bogus，原参数保持在前。"""
    spy = stub_http([(200, _LIST_RESPONSE)])
    dc.fetch_sticker_list(dict(FAKE_COOKIES))
    url = spy.calls[0][0]
    assert url.startswith(f"{dc.LIST_URL}?device_platform=webapp")
    assert "a_bogus=" in url
    # A-4 校准后的 query 全集（IM 家族 aid=1128 + CUSTOM_STICKER_PAGE），
    # a_bogus 只增不改（签名不改写业务参数）
    from urllib.parse import parse_qs, urlsplit

    params = parse_qs(urlsplit(url).query)
    assert set(params) == {
        "device_platform",
        "version_code",
        "version_name",
        "aid",
        "app_id",
        "scenes",
        "custom_cursor",
        "custom_page_size",
        "a_bogus",
    }
    assert params["aid"] == ["1128"] and params["app_id"] == ["1128"]
    assert params["scenes"] == ["CUSTOM_STICKER_PAGE"]
    assert params["custom_cursor"] == ["0"] and params["custom_page_size"] == ["50"]
    assert params["a_bogus"] and params["a_bogus"][0]


def test_passthrough_signer_still_available_as_explicit_opt_in(stub_http) -> None:
    """直通签名器降为显式注入选项仍可用：URL 无 a_bogus（D-17 落地后口径）。"""
    spy = stub_http([(200, _LIST_RESPONSE)])
    dc.fetch_sticker_list(dict(FAKE_COOKIES), signer=dc._passthrough_signer)
    assert "a_bogus" not in spy.calls[0][0]


def test_abogus_signer_input_is_only_query_and_ua(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认签名器结构上碰不到 cookie：入参只有 query 与 UA（间谍类捕获构造参数）。"""
    captured: dict[str, object] = {}

    class _SpyABogus:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def generate_abogus(self, params: str, body: str = "") -> tuple:
            return (params, "ab-from-spy", captured.get("user_agent"), body)

    monkeypatch.setattr(dc, "ABogus", _SpyABogus)
    out = dc._abogus_signer({"device_platform": "webapp"}, "UA-TEST")
    assert captured == {"user_agent": "UA-TEST"}
    assert out == {"device_platform": "webapp", "a_bogus": "ab-from-spy"}


# -- 下载白名单 ------------------------------------------------------------------


def test_download_rejects_non_whitelisted_host(stub_http, tmp_path: Path) -> None:
    """非白名单域拒绝出网：不发请求、不写文件。"""
    spy = stub_http([])
    sticker = dc.Sticker("evil", "https://evil.example.com/payload.webp")
    target = tmp_path / "evil.webp"
    with pytest.raises(dc.DownloadNotAllowedError):
        dc.download_sticker(sticker, target, dict(FAKE_COOKIES))
    assert spy.calls == []
    assert not target.exists()


def test_download_writes_file_for_whitelisted_host(stub_http, tmp_path: Path) -> None:
    stub_http([(200, b"webp-bytes-here")])
    sticker = dc.Sticker("s1", "https://p3-sign.douyinpic.com/a.webp")
    target = tmp_path / "a.webp"
    returned = dc.download_sticker(sticker, target, dict(FAKE_COOKIES))
    assert returned == target
    assert target.read_bytes() == b"webp-bytes-here"


def test_download_host_match_is_exact_suffix_not_substring(stub_http, tmp_path: Path) -> None:
    """白名单按域名后缀判定：``notdouyinpic.com`` 这类假亲戚必须被拒。"""
    spy = stub_http([])
    sticker = dc.Sticker("x", "https://notdouyinpic.com/a.webp")
    with pytest.raises(dc.DownloadNotAllowedError):
        dc.download_sticker(sticker, tmp_path / "a.webp", dict(FAKE_COOKIES))
    assert spy.calls == []
