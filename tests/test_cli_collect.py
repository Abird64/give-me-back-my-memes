"""``collect-douyin`` 子命令的 CLI 层测试（EM-005-D）。

分工：本文件只测**命令行层**（参数解析、前置条件、退出码、打印口径），
采集编排本身在 :mod:`tests.test_collect_flow`；本文件全部打桩，
**不碰真实 cookie、不发真实请求**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emoji_migrator import cli
from emoji_migrator.collect_flow import CollectOutcome
from emoji_migrator.douyin_collect import CollectionError, SessionExpired

FAKE_COOKIES = {"sessionid": "fake", "passport_csrf_token": "fake2"}


@pytest.fixture
def stub_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """桩掉 douyin_auth：返回可控 cookie 状态，不碰真实 APPDATA。"""

    class _FakeAuth:
        def __init__(self) -> None:
            self.cookies: dict[str, str] | None = None
            self.cache_exists = False

        def load_cached_cookie(self):
            return self.cookies

        def cookie_cache_path(self) -> Path:
            return tmp_path / "douyin_cookie.bin"

    fake = _FakeAuth()
    monkeypatch.setitem(
        __import__("sys").modules,
        "emoji_migrator.douyin_auth",
        fake,
    )
    monkeypatch.setattr(
        "emoji_migrator.douyin_auth.load_cached_cookie",
        fake.load_cached_cookie,
        raising=False,
    )
    return fake


def _outcome(**kwargs) -> CollectOutcome:
    """构造一个默认「干净完成」的 CollectOutcome。"""
    base = {
        "downloaded": 3,
        "converted": 3,
        "pages": 1,
        "listed": 3,
        "raw_dir": Path("_raw"),
        "out_dir": Path("out"),
    }
    base.update(kwargs)
    return CollectOutcome(**base)


def _stub_collect(monkeypatch: pytest.MonkeyPatch, result, recorder: list | None = None):
    def fake_collect(cookies, out_dir, **kwargs):
        if recorder is not None:
            recorder.append((cookies, out_dir, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cli, "collect_douyin_stickers", fake_collect)


# -- 参数解析 ----------------------------------------------------------------


def test_collect_subcommand_is_registered() -> None:
    """子命令必须在解析器里（消掉「模块是孤岛」这个老问题）。"""
    parser = cli.build_parser()
    args = parser.parse_args(["collect-douyin"])
    assert args.command == "collect-douyin"
    assert args.out == Path("_out/douyin")


def test_collect_out_and_max_pages_are_configurable() -> None:
    args = cli.build_parser().parse_args(
        ["collect-douyin", "-o", "D:/tmp/x", "--max-pages", "7"]
    )
    assert args.out == Path("D:/tmp/x")
    assert args.max_pages == 7


def test_default_max_pages_comes_from_module_constant() -> None:
    """默认值不在调用点复写数字——口径单一真源。"""
    from emoji_migrator import collect_flow

    args = cli.build_parser().parse_args(["collect-douyin"])
    assert args.max_pages == collect_flow.MAX_PAGES


def test_cli_references_collect_flow() -> None:
    """落点守护：cli 必须真引用编排层（这就是本包的核心目的）。"""
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "collect_douyin_stickers" in source
    assert "collect_flow" in source


# -- 前置条件：cookie --------------------------------------------------------


def test_no_cookie_cache_returns_exit_code_2_with_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """无缓存 → 退出码 2 + 中文提示，走 stderr。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: None)
    monkeypatch.setattr(
        douyin_auth,
        "cookie_cache_path",
        lambda: tmp_path / "nope.bin",
    )
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 2
    assert cli.DOUYIN_NO_COOKIE_HINT in captured.err


def test_corrupt_cache_distinguishes_from_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """缓存文件存在但解不开 → 给**不同的**中文提示（引导重扫，不是「从没登录」）。"""
    from emoji_migrator import douyin_auth

    cache = tmp_path / "douyin_cookie.bin"
    cache.write_bytes(b"corrupted")

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: None)
    monkeypatch.setattr(douyin_auth, "cookie_cache_path", lambda: cache)
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 2
    assert cli.DOUYIN_CORRUPT_HINT in captured.err
    assert cli.DOUYIN_NO_COOKIE_HINT not in captured.err


def test_missing_cookie_message_has_no_path_or_hex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """提示不得泄露缓存路径，也不得含十六进制错误码。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: None)
    monkeypatch.setattr(
        douyin_auth, "cookie_cache_path", lambda: tmp_path / "deep" / "cookie.bin"
    )
    cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert "0x" not in err.lower()
    assert "douyin_cookie.bin" not in err


def test_collection_error_returns_exit_code_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """编排层抛 CollectionError → 退出码 2 + 消息走 stderr。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(monkeypatch, CollectionError("缺少抖音登录 cookie"))
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 2
    assert "缺少抖音登录 cookie" in captured.err


def test_session_expired_returns_exit_code_2_with_rescan_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """401 → 退出码 2 + 明确引导「重新扫码」（D-15）。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(monkeypatch, SessionExpired("登录态已过期"))
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 2
    assert "重新扫码" in captured.err


# -- 成功与计数 --------------------------------------------------------------


def test_clean_collect_returns_zero_and_prints_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """干净完成 → 退出码 0，并打印六项计数。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(monkeypatch, _outcome())
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 0
    assert "翻页数：1 页" in out
    assert "列表条目：3 个" in out
    assert "下载成功：3 个" in out
    assert "转换成功：3 个" in out
    assert "产出合计：3 个" in out


def test_failures_return_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """有失败 → 退出码 1（与 normalize 的「守门不通过」口径一致）。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(
        monkeypatch,
        _outcome(downloaded=2, converted=2, listed=3, failed=1,
                 failures=[("idX", "下载", "HTTP 500")]),
    )
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    assert code == 1


def test_nothing_collected_returns_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """一条都没拿到 → 退出码 1 + 明确说明（别让用户以为成功了）。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(monkeypatch, _outcome(downloaded=0, converted=0, listed=0))
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 1
    assert "没有表情" in out


def test_page_limit_hit_is_reported_not_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """命中翻页上限必须显式告知（不静默截断）。"""
    from emoji_migrator import collect_flow, douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(monkeypatch, _outcome(hit_page_limit=True, pages=collect_flow.MAX_PAGES))
    cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    assert collect_flow.PAGE_LIMIT_HINT in capsys.readouterr().out


def test_max_pages_is_passed_through_to_orchestrator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--max-pages 必须真正传到编排层（不能解析了却不生效）。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    calls: list = []
    _stub_collect(monkeypatch, _outcome(), recorder=calls)
    cli.main(["collect-douyin", "-o", str(tmp_path / "out"), "--max-pages", "9"])
    assert calls and calls[0][2]["max_pages"] == 9


def test_cookie_is_injected_not_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """隐私守护：cookie 走注入路径，且**任何输出里都不出现其值**。"""
    from emoji_migrator import douyin_auth

    secret = "TOP-SECRET-SESSION"
    monkeypatch.setattr(
        douyin_auth, "load_cached_cookie", lambda: {"sessionid": secret}
    )
    seen: list = []
    _stub_collect(monkeypatch, _outcome(), recorder=seen)
    cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert seen[0][0]["sessionid"] == secret, "cookie 必须注入到编排层"


def test_convert_failure_is_reported_with_raw_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """转换失败要打印原始文件所在目录（用户要能自己去看）。"""
    from emoji_migrator import douyin_auth

    monkeypatch.setattr(douyin_auth, "load_cached_cookie", lambda: dict(FAKE_COOKIES))
    _stub_collect(
        monkeypatch,
        _outcome(
            downloaded=2, converted=1, convert_failed=1,
            failures=[("idY", "转换", "Pillow 炸了")],
            raw_dir=Path("D:/out/_raw"),
        ),
    )
    code = cli.main(["collect-douyin", "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 1
    assert "转换失败：1 个" in out
    # 断言目录名片段而非完整路径：Windows 下 print 会把 "/" 规范化成 "\"。
    assert "_raw" in out
    assert "[转换失败] idY" in out

