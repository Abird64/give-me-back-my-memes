"""抖音采集编排层（:mod:`emoji_migrator.collect_flow`）测试（EM-005-D）。

三条主线，对应工作包的三项验收：

1. **无 cookie 时的中文提示**——不含cookie 值、不含十六进制错误码；
2. **翻页**——按 ``fetch_page`` 给的游标推进，按 ``sticker_id`` 去重，
   达到 :data:`MAX_PAGES` 上限时停下并置 :attr:`CollectOutcome.hit_page_limit`；
3. **下载 → WebP 转换串联**——失败逐条记账，安全边界异常上抛。

**全部用桩，不碰真实 cookie、不发真实请求**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emoji_migrator import collect_flow
from emoji_migrator.collect_flow import (
    MAX_PAGES,
    NO_COOKIE_HINT,
    PAGE_LIMIT_HINT,
    CollectOutcome,
    collect_douyin_stickers,
)
from emoji_migrator.douyin_collect import (
    CollectionError,
    DownloadNotAllowedError,
    SessionExpired,
    Sticker,
    StickerPage,
)

FAKE_COOKIES = {"sessionid": "fake-value", "passport_csrf_token": "another-fake"}


def _sticker(index: int) -> Sticker:
    return Sticker(
        sticker_id=f"id{index}",
        url=f"https://p3.byteimg.com/sticker/id{index}.webp",
    )


def _page(
    stickers: list[Sticker],
    next_cursor: str | None = None,
    has_more: bool | None = None,
) -> StickerPage:
    return StickerPage(
        stickers=tuple(stickers), next_cursor=next_cursor, has_more=has_more
    )


class _Recorder:
    """记录下载/转换调用的桩，兼作写文件以验证清理逻辑。"""

    def __init__(
        self,
        *,
        fail_ids: set[str] | None = None,
        convert_fail_ids: set[str] | None = None,
    ) -> None:
        self.fail_ids = fail_ids or set()
        self.convert_fail_ids = convert_fail_ids or set()
        self.downloaded: list[str] = []
        self.converted: list[str] = []
        self.remaining_raw: list[str] = []

    def download(self, sticker: Sticker, target: Path) -> None:
        if sticker.sticker_id in self.fail_ids:
            raise CollectionError(f"表情下载失败：HTTP 500（sticker_id={sticker.sticker_id}）")
        target.write_bytes(b"RIFF....WEBPfake")
        self.downloaded.append(sticker.sticker_id)

    def convert(self, source: Path, target: Path) -> None:
        sticker_id = source.stem
        if sticker_id in self.convert_fail_ids:
            raise OSError("Pillow 炸了")
        target.write_bytes(b"GIF89a")
        self.converted.append(sticker_id)

    def raw_left(self, raw_dir: Path) -> list[str]:
        return sorted(p.name for p in raw_dir.iterdir()) if raw_dir.is_dir() else []


# -- 无 cookie ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, {}, {"sessionid": "", "a": ""}])
def test_missing_or_empty_cookies_fails_before_any_request(bad, tmp_path: Path) -> None:
    """缺失 / 全空 cookie 都要在**发请求之前**失败，文案是中文引导语。"""

    def never_called(*args, **kwargs):  # pragma: no cover - 不该被调用
        raise AssertionError("无 cookie 时不得发出任何请求")

    with pytest.raises(CollectionError) as excinfo:
        collect_douyin_stickers(
            bad,
            tmp_path / "out",
            fetcher=never_called,
            downloader=never_called,
            converter=never_called,
        )
    message = str(excinfo.value)
    assert NO_COOKIE_HINT in message


def test_cookie_error_message_never_leaks_cookie_values(tmp_path: Path) -> None:
    """隐私铁律：即使传入带值的 cookie，失败消息里也不得出现其值。

    构造「cookie 非空但取页抛错」的场景：错误消息只应含 HTTP 状态码。
    """
    secret = "super-secret-session-value"

    def exploding_fetcher(cursor: str) -> StickerPage:
        raise CollectionError("收藏列表请求失败：HTTP 500")

    with pytest.raises(CollectionError) as excinfo:
        collect_douyin_stickers(
            {"sessionid": secret},
            tmp_path / "out",
            fetcher=exploding_fetcher,
            downloader=lambda s, t: None,
            converter=lambda s, t: None,
        )
    assert secret not in str(excinfo.value)


def test_error_message_has_no_hex_error_code(tmp_path: Path) -> None:
    """失败文案不得含十六进制错误码（验收要求：用户能看懂的中文）。"""

    def failing(cursor: str) -> StickerPage:
        raise CollectionError("收藏列表请求失败：HTTP 500")

    with pytest.raises(CollectionError) as excinfo:
        collect_douyin_stickers(
            dict(FAKE_COOKIES),
            tmp_path / "out",
            fetcher=failing,
            downloader=lambda s, t: None,
            converter=lambda s, t: None,
        )
    message = str(excinfo.value)
    assert "0x" not in message.lower()
    assert "HTTP 500" in message


# -- 单页采集 ----------------------------------------------------------------


def test_single_page_downloads_converts_and_counts(tmp_path: Path) -> None:
    """单页 3 条 → 全部下载 + 转换，计数与产物目录正确。"""
    recorder = _Recorder()
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(i) for i in range(3)]),
        downloader=recorder.download,
        converter=recorder.convert,
    )
    assert outcome.pages == 1
    assert outcome.listed == 3
    assert outcome.downloaded == 3
    assert outcome.converted == 3
    assert outcome.failed == 0
    assert outcome.duplicates == 0
    assert outcome.hit_page_limit is False
    assert outcome.ok is True
    assert sorted(p.name for p in (tmp_path / "out").glob("*.gif")) == [
        "id0.gif",
        "id1.gif",
        "id2.gif",
    ]
    # 转换成功的原始 WebP 应被清掉——用户不需要中间态。
    assert recorder.raw_left(outcome.raw_dir) == []


def test_out_dir_and_raw_dir_are_created(tmp_path: Path) -> None:
    """产物目录与原始目录都应自动创建（调用方不必先mkdir）。"""
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "deep" / "out",
        fetcher=lambda cursor: _page([_sticker(1)]),
        downloader=_Recorder().download,
        converter=_Recorder().convert,
    )
    assert outcome.out_dir.is_dir()
    assert outcome.raw_dir.is_dir()


def test_raw_dir_can_be_overridden(tmp_path: Path) -> None:
    """显式传入 raw_dir 时不得被 out_dir/_raw 覆盖。"""
    custom = tmp_path / "elsewhere"
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        raw_dir=custom,
        fetcher=lambda cursor: _page([_sticker(1)]),
        downloader=_Recorder().download,
        converter=_Recorder().convert,
    )
    assert outcome.raw_dir == custom
    assert custom.is_dir()


# -- 翻页 --------------------------------------------------------------------


def test_pagination_follows_next_cursor_until_exhausted(tmp_path: Path) -> None:
    """3 页数据，逐页按 next_cursor 推进；第 3 页无游标 → 停止。"""
    seen_cursors: list[str] = []
    pages = {
        "0": _page([_sticker(i) for i in range(2)], next_cursor="c1"),
        "c1": _page([_sticker(i) for i in range(2, 4)], next_cursor="c2"),
        "c2": _page([_sticker(4)]),
    }

    def fetcher(cursor: str) -> StickerPage:
        seen_cursors.append(cursor)
        return pages[cursor]

    recorder = _Recorder()
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=fetcher,
        downloader=recorder.download,
        converter=recorder.convert,
    )
    assert seen_cursors == ["0", "c1", "c2"], "游标必须按响应推进，不能原地重试"
    assert outcome.pages == 3
    assert outcome.listed == 5
    assert outcome.converted == 5
    assert outcome.hit_page_limit is False


def test_pagination_dedupes_by_sticker_id_across_pages(tmp_path: Path) -> None:
    """跨页重复的同一id 只处理一次，重复数记进 duplicates。"""
    pages = {
        "0": _page([_sticker(1), _sticker(2)], next_cursor="c1"),
        "c1": _page([_sticker(2), _sticker(3)], next_cursor="c2"),
        "c2": _page([_sticker(1), _sticker(3)]),
    }
    recorder = _Recorder()
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: pages[cursor],
        downloader=recorder.download,
        converter=recorder.convert,
    )
    assert outcome.listed == 6
    assert outcome.converted == 3
    assert outcome.duplicates == 3
    assert sorted(recorder.converted) == ["id1", "id2", "id3"]


def test_empty_first_page_stops_immediately(tmp_path: Path) -> None:
    """空页是正常终止信号，不是一次失败。"""
    calls: list[str] = []

    def fetcher(cursor: str) -> StickerPage:
        calls.append(cursor)
        return _page([])

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=fetcher,
        downloader=_Recorder().download,
        converter=_Recorder().convert,
    )
    assert calls == ["0"], "空页后不得继续翻页"
    assert outcome.pages == 1
    assert outcome.listed == 0
    assert outcome.converted == 0
    assert outcome.ok is False


def test_empty_page_in_the_middle_stops_pagination(tmp_path: Path) -> None:
    """中途空页必须停——且**在计数上留下痕迹**。

    **这是突变 M10 逼出来的守护**。第一版写错了：断言只看 ``calls``，
    但「空页后break」和「空页因无游标而 break」两条路径的 ``calls`` 完全一样，
    突变后仍全绿。现在改为断言**内层空页检查真的执行过**
    （用 ``outcome.pages`` 与游标序列的组合，突变掉空页检查时
    会因继续请求下一页而让 pages 变成 3）。
    """
    calls: list[str] = []
    # 第二页给空页**且带游标**：这样「只有空页检查」能拦住它；
    # 若把空页检查改成if False，就会继续取第三页，calls 变3个。
    pages = {
        "0": _page([_sticker(1)], next_cursor="c1"),
        "c1": _page([], next_cursor="c2"),  # 空页但服务端仍给游标
        "c2": _page([_sticker(2)]),
    }

    def fetcher(cursor: str) -> StickerPage:
        calls.append(cursor)
        return pages.get(cursor, _page([]))

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=fetcher,
        downloader=_Recorder().download,
        converter=_Recorder().convert,
    )
    assert calls == ["0", "c1"], "空页后不得再取下一页"
    assert outcome.pages == 2
    assert outcome.listed == 1
    assert outcome.converted == 1


@pytest.mark.parametrize("budget", [0, -1])
def test_non_positive_page_budget_still_fetches_first_page(budget, tmp_path: Path) -> None:
    """上限为 0 或负数时视为 1——不允许「静默什么都不取」。"""
    calls: list[str] = []

    def fetcher(cursor: str) -> StickerPage:
        calls.append(cursor)
        return _page([_sticker(1)])

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=fetcher,
        downloader=_Recorder().download,
        converter=_Recorder().convert,
        max_pages=budget,
    )
    assert calls == ["0"]
    assert outcome.pages == 1


def test_hitting_page_limit_is_flagged_not_silent(tmp_path: Path) -> None:
    """服务端一直给游标时：到上限即停，且 hit_page_limit 置位（不静默截断）。"""
    calls: list[str] = []

    def fetcher(cursor: str) -> StickerPage:
        calls.append(cursor)
        return _page([_sticker(len(calls))], next_cursor=f"c{len(calls)}")

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=fetcher,
        downloader=_Recorder().download,
        converter=_Recorder().convert,
        max_pages=5,
    )
    assert len(calls) == 5, "绝不能无限翻页"
    assert outcome.pages == 5
    assert outcome.hit_page_limit is True


def test_default_page_budget_is_the_module_constant(tmp_path: Path) -> None:
    """默认预算就是 MAX_PAGES（不在调用点复写数字）。"""
    signature = collect_douyin_stickers.__defaults__ or ()
    assert signature[-1] == MAX_PAGES


def test_page_limit_hint_mentions_the_limit() -> None:
    """上限提示必须显式告知用户，不静默截断。"""
    assert str(MAX_PAGES) in PAGE_LIMIT_HINT
    assert "上限" in PAGE_LIMIT_HINT


# -- 失败路径 ----------------------------------------------------------------


def test_download_failure_is_recorded_and_collection_continues(tmp_path: Path) -> None:
    """单条下载失败只记账，后续条目照常处理。"""
    recorder = _Recorder(fail_ids={"id1"})
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(i) for i in range(3)]),
        downloader=recorder.download,
        converter=recorder.convert,
    )
    assert outcome.failed == 1
    assert outcome.converted == 2
    assert [f[0] for f in outcome.failures] == ["id1"]
    assert outcome.failures[0][1] == "下载"


def test_convert_failure_keeps_raw_webp_for_debugging(tmp_path: Path) -> None:
    """转换失败保留原始 WebP——删了用户只剩一条错误信息。"""
    recorder = _Recorder(convert_fail_ids={"id1"})
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(i) for i in range(3)]),
        downloader=recorder.download,
        converter=recorder.convert,
    )
    assert outcome.convert_failed == 1
    assert outcome.converted == 2
    assert recorder.raw_left(outcome.raw_dir) == ["id1.webp"]
    assert [f[0] for f in outcome.failures] == ["id1"]
    assert outcome.failures[0][1] == "转换"


def test_failed_download_leaves_no_half_file(tmp_path: Path) -> None:
    """下载失败不得留下半个文件。"""

    def failing_download(sticker: Sticker, target: Path) -> None:
        target.write_bytes(b"partial")
        raise CollectionError("下载中断")

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(1)]),
        downloader=failing_download,
        converter=_Recorder().convert,
    )
    assert outcome.failed == 1
    assert recorder_files(outcome.raw_dir) == []


def recorder_files(raw_dir: Path) -> list[str]:
    return sorted(p.name for p in raw_dir.iterdir()) if raw_dir.is_dir() else []


#-- 网络异常处理（真机实测逼出来的）------------------------------------------


def _curl_error(name: str):
    """造一个真实的 ``curl_cffi`` 异常实例（不联网，纯构造）。"""
    from curl_cffi.requests import exceptions as curl_exc

    return getattr(curl_exc, name)("curl: (28) Operation timed out after 15009 ms")


def test_curl_connection_error_is_caught_not_raised(tmp_path: Path) -> None:
    """**核心守护**：`curl_cffi``ConnectionError` 不得冒泡成 traceback。

    真机实测（2026-10-20）：本机代理对 douyin.com 返回 502，
    `curl_cffi` 抛 ``ConnectionError``——它**不继承 OSError**，
    原实现 `except (CollectionError, OSError)` 抓不住，
    异常一路冒泡，用户看到的是一整屏 traceback。
    """
    def failing_download(sticker: Sticker, target: Path) -> None:
        raise _curl_error("ConnectionError")

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(1)]),
        downloader=failing_download,
        converter=_Recorder().convert,
    )
    assert outcome.failed == 1
    assert outcome.converted == 0
    assert "网络连接失败" in outcome.failures[0][2]


def test_curl_timeout_is_caught_not_raised(tmp_path: Path) -> None:
    """下载超时也要接住（真机跑 41 条时实测到3 个超时）。"""

    def slow_download(sticker: Sticker, target: Path) -> None:
        raise _curl_error("Timeout")

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(1), _sticker(2)]),
        downloader=slow_download,
        converter=_Recorder().convert,
    )
    assert outcome.failed == 2
    assert "超时" in outcome.failures[0][2]


def test_network_failure_reason_never_leaks_raw_curl_string(tmp_path: Path) -> None:
    """失败原因不得透传 curl 原始串（可能含代理地址 / 内网主机名）。"""

    def failing_download(sticker: Sticker, target: Path) -> None:
        raise _curl_error("ConnectionError")

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(1)]),
        downloader=failing_download,
        converter=_Recorder().convert,
    )
    reason = outcome.failures[0][2]
    assert "curl" not in reason.lower()
    assert "15009" not in reason
    assert "0x" not in reason.lower()


def test_fetch_network_failure_aborts_whole_batch(tmp_path: Path) -> None:
    """取页失败 → 整批中止并给中文原因（列表都拿不到，逐条重试没意义）。"""

    def failing_fetch(cursor: str) -> StickerPage:
        raise _curl_error("ConnectionError")

    with pytest.raises(CollectionError) as excinfo:
        collect_douyin_stickers(
            dict(FAKE_COOKIES),
            tmp_path / "out",
            fetcher=failing_fetch,
            downloader=_Recorder().download,
            converter=_Recorder().convert,
        )
    message = str(excinfo.value)
    assert "采集列表失败" in message
    assert "网络连接失败" in message


def test_fetch_failure_message_has_no_proxy_address(tmp_path: Path) -> None:
    """取页失败文案不得含代理地址（本机实测 127.0.0.1:10322）。"""

    def failing_fetch(cursor: str) -> StickerPage:
        raise _curl_error("ProxyError")

    with pytest.raises(CollectionError) as excinfo:
        collect_douyin_stickers(
            dict(FAKE_COOKIES),
            tmp_path / "out",
            fetcher=failing_fetch,
            downloader=_Recorder().download,
            converter=_Recorder().convert,
        )
    assert "127.0.0.1" not in str(excinfo.value)
    assert "代理" in str(excinfo.value)


def test_fetch_collection_error_still_propagates_unchanged(tmp_path: Path) -> None:
    """业务异常（不是网络异常）保持原样上抛，不被翻译成网络文案。"""

    def failing_fetch(cursor: str) -> StickerPage:
        raise CollectionError("收藏列表响应结构无法解析（需校准）")

    with pytest.raises(CollectionError, match="校准"):
        collect_douyin_stickers(
            dict(FAKE_COOKIES),
            tmp_path / "out",
            fetcher=failing_fetch,
            downloader=_Recorder().download,
            converter=_Recorder().convert,
        )


def test_network_error_types_contains_curl_base_class() -> None:
    """元组必须含 `RequestException` 基类（上游新增异常类型也接得住）。"""
    from curl_cffi.requests import exceptions as curl_exc

    assert collect_flow._init_network_errors() is None
    assert curl_exc.RequestException in collect_flow._NETWORK_ERRORS


def test_readable_reason_maps_known_types() -> None:
    """已知异常类型映射成中文；未知类型给兜底但不泄露原文。"""
    assert "网络" in collect_flow._readable_reason(_curl_error("ConnectionError"))
    assert "超时" in collect_flow._readable_reason(_curl_error("Timeout"))
    assert "代理" in collect_flow._readable_reason(_curl_error("ProxyError"))
    assert "证书" in collect_flow._readable_reason(_curl_error("SSLError"))
    fallback = collect_flow._readable_reason(ValueError("secret-path"))
    assert "ValueError" in fallback
    assert "secret-path" not in fallback


def test_collect_survives_all_items_failing(tmp_path: Path) -> None:
    """全部条目都超时也要正常收尾（不是崩在半路）。"""

    def always_timeout(sticker: Sticker, target: Path) -> None:
        raise _curl_error("Timeout")

    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(i) for i in range(5)]),
        downloader=always_timeout,
        converter=_Recorder().convert,
    )
    assert outcome.failed == 5
    assert outcome.downloaded == 0
    assert outcome.ok is False
    assert len(outcome.failures) == 5


def test_session_expired_propagates_not_swallowed(tmp_path: Path) -> None:
    """401 要上抛（调用方据此引导重新登录），不可逐条吞掉。"""

    def expiring_download(sticker: Sticker, target: Path) -> None:
        raise SessionExpired("登录态已过期")

    with pytest.raises(SessionExpired):
        collect_douyin_stickers(
            dict(FAKE_COOKIES),
            tmp_path / "out",
            fetcher=lambda cursor: _page([_sticker(1)]),
            downloader=expiring_download,
            converter=_Recorder().convert,
        )


def test_download_not_allowed_propagates(tmp_path: Path) -> None:
    """域名白名单是安全边界，异常必须上抛。"""

    def blocked_download(sticker: Sticker, target: Path) -> None:
        raise DownloadNotAllowedError("下载域名不在白名单内：evil.example")

    with pytest.raises(DownloadNotAllowedError):
        collect_douyin_stickers(
            dict(FAKE_COOKIES),
            tmp_path / "out",
            fetcher=lambda cursor: _page([_sticker(1)]),
            downloader=blocked_download,
            converter=_Recorder().convert,
        )


def test_outcome_ok_is_false_when_any_failure(tmp_path: Path) -> None:
    """有失败即 ok=False（即使大部分成功）。"""
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(i) for i in range(2)]),
        downloader=_Recorder(fail_ids={"id0"}).download,
        converter=_Recorder().convert,
    )
    assert outcome.downloaded == 1
    assert outcome.failed == 1
    assert outcome.ok is False


def test_total_collected_counts_converted_plus_convert_failed(tmp_path: Path) -> None:
    """总产出 = 转换成功 + 转换失败但已下载。"""
    recorder = _Recorder(convert_fail_ids={"id0"})
    outcome = collect_douyin_stickers(
        dict(FAKE_COOKIES),
        tmp_path / "out",
        fetcher=lambda cursor: _page([_sticker(i) for i in range(3)]),
        downloader=recorder.download,
        converter=recorder.convert,
    )
    assert outcome.converted == 2
    assert outcome.convert_failed == 1
    assert outcome.total_collected == 3


def test_collect_outcome_defaults_are_sane() -> None:
    """空结果的默认值全为 0 / False（不该有未初始化的真值）。"""
    outcome = CollectOutcome()
    assert outcome.pages == 0
    assert outcome.converted == 0
    assert outcome.failures == []
    assert outcome.ok is False


# -- 落点 --------------------------------------------------------------------


def test_flow_module_lives_in_src_and_is_importable() -> None:
    """落点守护：编排层必须在包内（放临时脚本或根目录都算破约定）。"""
    module_file = Path(collect_flow.__file__).resolve()
    assert module_file.parent.name == "emoji_migrator"
    assert module_file.name == "collect_flow.py"


def test_cli_and_gui_can_both_import_flow() -> None:
    """编排层存在的理由：cli 与 gui 共用。两者必须能import 它。"""
    import emoji_migrator.cli
    import emoji_migrator.gui

    assert emoji_migrator.cli is not None
    assert emoji_migrator.gui is not None
