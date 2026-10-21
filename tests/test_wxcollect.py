"""微信导出编排层（:mod:`emoji_migrator.wxcollect`）测试。

**全部用桩**：`scan` 打桩返回固定 seed、不发内存请求；wxgf 用合成裸H.265 桩。
**不碰真实微信进程**（真机验证在 RUN-012/-014 做过）。

守护三条线：
1. **只写自己的输出目录**——绝不写回微信目录（EM-003 硬约束）；
2. **原子写**——失败不留半个文件（半个 GIF 表现为「表情损坏」，用户看不出原因）；
3. **wxgf 走转码、其余直写**——wxgf 微信/QQ 都不认。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from PIL import Image

from emoji_migrator import wxcollect, wxdecrypt

SEED = 1234567890
WXID = "wxid_test12345678"


def _key() -> bytes:
    return wxdecrypt.derive_key(SEED, WXID)


def _encrypt(plain: bytes) -> bytes:
    pad = 16 - len(plain) % 16
    return AES.new(_key(), AES.MODE_CBC, _key()).encrypt(plain + bytes([pad]) * pad)


def _png(colour: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(buf, format="PNG")
    return buf.getvalue()


def _fake_tree(root: Path) -> Path:
    """造一棵 wxdir 能认的目录树，返回 emoticon 目录。"""
    wx = root / "xwechat_files" / f"{WXID}_cd34" / "business" / "emoticon"
    (wx / "Persist").mkdir(parents=True)
    (wx / "PersistStore").mkdir(parents=True)
    return wx
def _stub_scan(SEED_value: int = SEED):  # noqa: N803 - 局部桩
    def scan(wxid: str, sample: bytes) -> int:
        assert wxid == WXID
        assert len(sample) >= 16
        return SEED_value

    return scan


# -- 导出范围语义（2026-10-21 实测结论固化）-----------------------------------


def test_export_scope_is_persist_and_persiststore_only(monkeypatch, tmp_path: Path) -> None:
    """⚠️ **导出范围 = ``Persist`` + ``PersistStore``**，**不含 ``Thumb``**。

    实测结论（2026-10-21，用户真机反馈「还导出了好友发的」引出）：

    - 微信的 ``emoticon/`` 下有 4 个子目录：``Persist`` / ``PersistStore``
      （原图与整包）与 ``Thumb`` / ``ThumbStore``（**缩略图**）。
    - ``Thumb`` 的文件名 = ``Persist`` 同名 + **``.thumb`` 后缀**
      （实测交集 1140 / 只在 Persist 2 / 只在 Thumb 269）。
    - ⇒ 微信给**每个见过的表情**都留了缩略图；``Persist`` 存**原图**。

    关键推论：``Persist`` 里**不止是你主动收藏的**，还包括**好友发来、
    微信顺手缓存进去的**——用户实测确认了这一点（这是**正确行为**，不是 bug）。

    本测试钉住范围：只导 ``Persist`` + ``PersistStore``，
    **不导** ``Thumb``（缩略图质量低，且与 Persist 大量重复）。
    """
    emo = _fake_tree(tmp_path)
    # 在 Persist 放一个能解密成功的文件
    (emo / "Persist" / "a").mkdir()
    (emo / "Persist" / "a" / "x.png").write_bytes(_encrypt(_png()))
    # 在 Thumb 放一个「好友发的表情」——**不应被导出**
    (emo / "Thumb").mkdir()
    (emo / "Thumb" / "b").mkdir()
    (emo / "Thumb" / "b" / "y.png").write_bytes(_encrypt(_png()))

    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )

    assert result.single_files == 1, "只应导出 Persist 里的那一个"
    names = sorted(p.name for p in out.iterdir() if p.is_file())
    assert names == ["x.png"], f"产物应只有 x.png，实际 {names}"


def test_gui_hint_does_not_claim_only_own_favorites() -> None:
    """⚠️ **GUI 文案不得说「收藏的表情」**——那与实际导出范围不符。

    实际导的是 ``Persist`` 全量（含好友发来的），文案若说「收藏」会让用户
    以为产品出了 bug。用户真机反馈就是这么来的（2026-10-21）。
    """
    source = (
        Path(__file__).resolve().parents[1] / "src" / "emoji_migrator" / "gui.py"
    ).read_text(encoding="utf-8")
    assert "导出微信收藏的表情" not in source, (
        "GUI 文案不应说「导出微信收藏的表情」——实际导出含好友发来的表情，"
        "文案会让用户误以为是 bug"
    )


# -- 目录探测失败 --------------------------------------------------------------


def test_missing_directory_raises_readable_error(monkeypatch, tmp_path: Path) -> None:
    """探测不到目录 → WxError 且消息是中文指引。"""
    from emoji_migrator import wxdir

    def boom() -> Path:
        raise wxdir.WXDirsNotFoundError(wxdir.NOT_FOUND_MESSAGE)

    monkeypatch.setattr(wxdir, "detect_weixin_emoticon_dir", boom)
    with pytest.raises(wxcollect.WxError, match="手动选择"):
        wxcollect.collect_weixin_emoticons(tmp_path / "out")


def test_nonexistent_explicit_directory_raises(tmp_path: Path) -> None:
    """显式传入的目录不存在 → 明确报错。"""
    with pytest.raises(wxcollect.WxError, match="不存在"):
        wxcollect.collect_weixin_emoticons(
            tmp_path / "out", emoticon_dir=tmp_path / "nope"
        )


def test_empty_persist_raises_readable_error(tmp_path: Path) -> None:
    """Persist 里没有样本文件 → 说清是「没登录/没打开过表情」。"""
    emo = _fake_tree(tmp_path)
    with pytest.raises(wxcollect.WxError, match="样本"):
        wxcollect.collect_weixin_emoticons(
            tmp_path / "out", emoticon_dir=emo, scan=_stub_scan()
        )


# -- 微信未运行 / 没找到密钥 ----------------------------------------------------


def test_weixin_not_running_propagates_as_wxerror(tmp_path: Path) -> None:
    """微信没运行 → 扫密钥抛的异常被翻译成 WxError（消息中文）。"""
    from emoji_migrator import wxscan

    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "a.bin").write_bytes(_encrypt(_png()))

    def boom(wxid: str, sample: bytes) -> int:
        raise wxscan.WeixinNotRunningError(wxscan.WEIXIN_NOT_RUNNING_MESSAGE)

    with pytest.raises(wxcollect.WxError, match="微信"):
        wxcollect.collect_weixin_emoticons(
            tmp_path / "out", emoticon_dir=emo, scan=boom
        )


def test_seed_not_found_propagates_as_wxerror(tmp_path: Path) -> None:
    """扫不到密钥 → WxError。"""
    from emoji_migrator import wxscan

    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "a.bin").write_bytes(_encrypt(_png()))

    def boom(wxid: str, sample: bytes) -> int:
        raise wxscan.SeedNotFoundError(wxscan.NO_SEED_FOUND_MESSAGE)

    with pytest.raises(wxcollect.WxError, match="密钥"):
        wxcollect.collect_weixin_emoticons(
            tmp_path / "out", emoticon_dir=emo, scan=boom
        )


# -- 正常导出 ------------------------------------------------------------------


def test_exports_single_png_file(tmp_path: Path) -> None:
    """单个 PNG → 写出且字节一致。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(_png()))
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )
    assert result.single_files == 1
    assert result.written == 1
    written = sorted(out.glob("*.png"))
    assert len(written) == 1
    assert written[0].read_bytes() == _png()


def test_exports_gif_preserving_animation(tmp_path: Path) -> None:
    """动图导出后仍是多帧（动图存活是产品核心理由）。"""
    buf = io.BytesIO()
    imgs = [Image.new("RGB", (16, 16), (30 * (i + 1), 80, 200)) for i in range(3)]
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:])
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(buf.getvalue()))
    out = tmp_path / "out"
    wxcollect.collect_weixin_emoticons(out, emoticon_dir=emo, scan=_stub_scan())
    gifs = list(out.glob("*.gif"))
    assert len(gifs) == 1
    with Image.open(gifs[0]) as im:
        assert getattr(im, "n_frames", 1) == 3


def test_splits_container_into_multiple_files(tmp_path: Path) -> None:
    """容器整包 → 切成多个独立文件。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(_png((200, 30, 30))))
    (emo / "Persist" / "02").write_bytes(_encrypt(_png((30, 200, 30))))
    container = _png((10, 10, 200)) + _png((200, 200, 10)) + _png((20, 20, 20))
    (emo / "PersistStore" / "pack").write_bytes(_encrypt(container))
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )
    assert result.container_slices == 3
    assert result.written == 5  # 2 单文件 + 3 切片


def test_progress_callback_reports_stages(tmp_path: Path) -> None:
    """阶段回调被调用（GUI 靠它更新状态栏）。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(_png()))
    seen: list[str] = []
    wxcollect.collect_weixin_emoticons(
        tmp_path / "out",
        emoticon_dir=emo,
        scan=_stub_scan(),
        progress=seen.append,
    )
    assert any("扫描" in m for m in seen), f"应报告扫内存阶段，实际 {seen}"
    assert any("解密" in m for m in seen), f"应报告解密阶段，实际 {seen}"


def test_scan_seconds_recorded(tmp_path: Path) -> None:
    """扫内存耗时被记录（GUI 要显示「为什么要等」）。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(_png()))
    result = wxcollect.collect_weixin_emoticons(
        tmp_path / "out", emoticon_dir=emo, scan=_stub_scan()
    )
    assert result.scan_seconds >= 0.0


# -- 只写自己的目录（EM-003 硬约束）--------------------------------------------


def test_never_writes_to_wechat_directory(tmp_path: Path) -> None:
    """⚠️ **核心守护**：导出**绝不写回**微信目录。

    症状若被破坏：微信表情文件被改坏，且用户完全不知道。
    """
    emo = _fake_tree(tmp_path)
    payload = _encrypt(_png())
    (emo / "Persist" / "01").write_bytes(payload)
    (emo / "PersistStore" / "pack").write_bytes(payload)
    before = {
        p: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in emo.rglob("*") if p.is_file()
    }
    wxcollect.collect_weixin_emoticons(
        tmp_path / "out", emoticon_dir=emo, scan=_stub_scan()
    )
    after = {
        p: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in emo.rglob("*") if p.is_file()
    }
    assert before == after, "微信目录下的文件被修改了（mtime 或大小变了）"


def test_output_dir_created_on_demand(tmp_path: Path) -> None:
    """输出目录不存在时自动创建（调用方不必先mkdir）。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(_png()))
    out = tmp_path / "deep" / "nested" / "out"
    wxcollect.collect_weixin_emoticons(out, emoticon_dir=emo, scan=_stub_scan())
    assert out.is_dir()


# -- 失败记账 ------------------------------------------------------------------


def test_corrupt_file_is_recorded_not_crashing(tmp_path: Path) -> None:
    """单个文件解密失败 → 记账后继续（不中断整批）。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "good").write_bytes(_encrypt(_png()))
    (emo / "Persist" / "bad").write_bytes(_encrypt(b"not an image" * 4))
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )
    assert result.single_files == 1
    assert result.rejected == 1
    assert result.failures, "失败必须有明细"


def test_non_block_aligned_file_is_recorded(tmp_path: Path) -> None:
    """密文长度非 16 倍数（损坏）→ 记账不崩。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "trunc").write_bytes(b"\x01" * 30)
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )
    assert result.single_files == 0
    assert result.failures


def test_failure_reason_has_no_paths_or_keys(tmp_path: Path) -> None:
    """失败明细不得含路径或密钥（隐私铁律）。"""
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "bad").write_bytes(_encrypt(b"garbage" * 8))
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )
    blob = " ".join(f"{n} {r}" for n, r in result.failures)
    assert _key().hex() not in blob
    assert str(emo) not in blob


# -- wxgf 转码 -----------------------------------------------------------------


def test_skip_wxgf_excludes_them(tmp_path: Path, monkeypatch) -> None:
    """``convert_wxgf=False`` 时跳过 wxgf（只导常规格式）。"""
    calls: list[int] = []
    monkeypatch.setattr(
        wxcollect, "convert_wxgf_to_gif",
        lambda payload, target, retries=1: calls.append(len(payload)),
    )
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "01").write_bytes(_encrypt(_png()))
    (emo / "Persist" / "02").write_bytes(
        _encrypt(wxdecrypt.WXGF_MAGIC + b"\x00\x00\x00\x01\x40\x01" + b"\xff" * 30)
    )
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan(), convert_wxgf=False
    )
    assert result.single_files == 1
    assert not calls, "跳过后不应调用转码"


def test_convert_failure_leaves_no_partial_file(tmp_path: Path, monkeypatch) -> None:
    """⚠️ **原子写守护**：转码失败**不留半个文件**。

    半个 GIF 在微信里表现为「表情损坏」，用户看不出是导出出了问题。
    """
    def failing_convert(payload: bytes, target: Path, retries: int = 1) -> None:
        target.write_bytes(b"GIF89a-half")  # 模拟中途失败留下半截
        raise wxcollect.WxError("动图转码失败")

    monkeypatch.setattr(wxcollect, "convert_wxgf_to_gif", failing_convert)
    emo = _fake_tree(tmp_path)
    (emo / "Persist" / "wx").write_bytes(
        _encrypt(wxdecrypt.WXGF_MAGIC + b"\x00\x00\x00\x01\x40\x01" + b"\xff" * 30)
    )
    out = tmp_path / "out"
    result = wxcollect.collect_weixin_emoticons(
        out, emoticon_dir=emo, scan=_stub_scan()
    )
    assert result.wxgf_failed == 1
    assert not list(out.glob("*.gif")), "失败不应留下 .gif"
    leftovers = list(out.glob("*.part"))
    assert not leftovers, f"临时文件未清理：{leftovers}"


def test_convert_retry_is_attempted(monkeypatch, tmp_path: Path) -> None:
    """转码失败会**重试**（批量实测 1009 个里35 个首次失败但重试即成）。"""
    attempts: list[int] = []

    class _Proc:
        returncode = 1
        stderr = b""

    import subprocess as sp

    def fake_run(cmd, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return _Proc()
        # 第二次：写出一个「成功」的产物
        out = Path(cmd[-1])
        out.write_bytes(b"GIF89a-ok")
        _Proc.returncode = 0
        return _Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    target = tmp_path / "o.gif"
    wxcollect.convert_wxgf_to_gif(wxdecrypt.WXGF_MAGIC + b"\x00" * 32, target)
    assert len(attempts) == 2, "应重试一次"
    assert target.read_bytes() == b"GIF89a-ok"


def test_convert_gives_up_after_retries(monkeypatch, tmp_path: Path) -> None:
    """重试用尽仍失败 → 抛 WxError（消息中文、不含路径）。"""
    import subprocess as sp

    class _Proc:
        returncode = 1
        stderr = b"/secret/temp/path"

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Proc())
    with pytest.raises(wxcollect.WxError) as excinfo:
        wxcollect.convert_wxgf_to_gif(b"wxgf" + b"\x00" * 32, tmp_path / "o.gif")
    assert "/secret/temp/path" not in str(excinfo.value)


# -- 防弹黑框守护（2026-10-21，用户真机实测报障）------------------------------


def test_convert_passes_create_no_window_flag(monkeypatch, tmp_path: Path) -> None:
    """⚠️ **必须传 ``creationflags=CREATE_NO_WINDOW``**——否则每转一个动图弹一次黑框。

    用户真机症状（2026-10-21）：点「导出微信表情」后**反复弹命令行黑窗口
    然后秒关**，持续很久没结果。

    根因：打包后的 exe 是 ``console=False``（windowed，本进程无控制台），
    Windows 上这类程序启动子进程时不指定该 flag，就会给子进程**分配新控制台**
    ⇒ 每调一次 ffmpeg 弹一个黑框。974 个 wxgf = 弹 974 次。

    实测对照（同一份 windowed 探针 exe，各跑 5 次）：
    不传 flag → 观察到 5/5 个 ffmpeg 窗口；传 flag → 0/5。

    本测试直接断言 ``subprocess.run`` 收到了这个 flag——
    删掉它就变红，**这是防回归的闸门**。
    """
    import subprocess as sp

    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        Path(cmd[-1]).write_bytes(b"GIF89a-ok")
        return _Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    wxcollect.convert_wxgf_to_gif(wxdecrypt.WXGF_MAGIC + b"\x00" * 32, tmp_path / "o.gif")

    assert "creationflags" in captured, "subprocess.run 必须显式传 creationflags"
    expected = getattr(sp, "CREATE_NO_WINDOW", 0)
    assert captured["creationflags"] == expected, (
        f"creationflags 应为 CREATE_NO_WINDOW({expected:#x})，"
        f"实际 {captured['creationflags']!r}"
    )


def test_create_no_window_constant_matches_subprocess_on_windows() -> None:
    """模块常量必须与 ``subprocess.CREATE_NO_WINDOW`` 一致（防止写死错值）。

    非 Windows 平台该常量不存在 → 期望值为 0（等价「不指定」）。
    """
    import subprocess as sp

    expected = getattr(sp, "CREATE_NO_WINDOW", 0)
    assert wxcollect._CREATE_NO_WINDOW == expected
    if sys.platform == "win32":
        assert expected == 0x08000000, "Windows 的 CREATE_NO_WINDOW 固定值"


def test_source_contains_no_console_window_bug_pattern() -> None:
    """**源码级**兜底：``subprocess.run`` 调用处必须带 ``creationflags``。

    上一条靠打桩拦；若有人改用别的调用方式（如 ``Popen``），打桩就拦不住了。
    这条直接读源码，要求每个 ``subprocess.run`` 调用都出现 ``creationflags``。
    """
    import ast

    source = (
        Path(__file__).resolve().parents[1] / "src" / "emoji_migrator" / "wxcollect.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "run"):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "subprocess":
            continue
        kwargs = {kw.arg for kw in node.keywords}
        if "creationflags" not in kwargs:
            problems.append(f"第 {node.lineno} 行的 subprocess.run 缺 creationflags")
    assert not problems, (
        "subprocess.run 必须传 creationflags=CREATE_NO_WINDOW（否则 windowed exe 会弹黑框）：\n"
        + "\n".join(problems)
    )


def test_timeout_becomes_wxerror_not_a_crash(monkeypatch, tmp_path: Path) -> None:
    """⚠️ **超时必须转成 ``WxError``**，不能穿透。

    调用方只 catch ``WxError``；``subprocess.TimeoutExpired`` 属于
    ``SubprocessError``，**不是** ``WxError`` ⇒ 会一路穿出去，
    **整轮导出崩掉**（而不是「这个表情失败、其余继续」）。

    实测踩过：机器负载高时单个 wxgf 转码超 90 秒。
    """
    import subprocess as sp

    def raising_run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd, timeout=kwargs.get("timeout", 90))

    monkeypatch.setattr(sp, "run", raising_run)
    with pytest.raises(wxcollect.WxError) as excinfo:
        wxcollect.convert_wxgf_to_gif(wxdecrypt.WXGF_MAGIC + b"\x00" * 32, tmp_path / "o.gif")
    assert "超时" in str(excinfo.value), "错误文案应说明是超时"
    assert not (tmp_path / "o.gif").exists(), "失败不应留下产物"


def test_oserror_becomes_wxerror(monkeypatch, tmp_path: Path) -> None:
    """ffmpeg 起不来（OSError，如文件被删/无权限）也必须转成 ``WxError``。"""
    import subprocess as sp

    def raising_run(cmd, **kwargs):
        raise OSError("No such file or directory")

    monkeypatch.setattr(sp, "run", raising_run)
    with pytest.raises(wxcollect.WxError) as excinfo:
        wxcollect.convert_wxgf_to_gif(wxdecrypt.WXGF_MAGIC + b"\x00" * 32, tmp_path / "o.gif")
    assert "ffmpeg" in str(excinfo.value)


def test_strips_wxgf_magic_before_transcoding(monkeypatch, tmp_path: Path) -> None:
    """转码输入是**去掉 4 字节 magic** 后的裸 H.265 流。"""
    seen: list[bytes] = []

    class _Proc:
        returncode = 0
        stderr = b""

    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raw = Path(cmd[cmd.index("-i") + 1])
        seen.append(raw.read_bytes())
        Path(cmd[-1]).write_bytes(b"GIF89a")
        return _Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    body = b"\x00\x00\x00\x01\x40\x01\xff\xff"
    wxcollect.convert_wxgf_to_gif(wxdecrypt.WXGF_MAGIC + body, tmp_path / "o.gif")
    assert seen == [body], "magic 应被剥掉"


# -- 结果统计 ------------------------------------------------------------------


def test_outcome_total_sums_single_and_container() -> None:
    """``total`` = 单文件 + 容器切片。"""
    result = wxcollect.WxCollectOutcome(single_files=3, container_slices=4)
    assert result.total == 7


def test_wx_error_is_runtime_error_subclass() -> None:
    """``WxError`` 是 ``RuntimeError`` 子类（调用方按 RuntimeError 兜即可）。"""
    assert issubclass(wxcollect.WxError, RuntimeError)


def test_outcome_defaults_are_zero() -> None:
    """空结果默认值全为 0 /空列表（不该有未初始化的真值）。"""
    result = wxcollect.WxCollectOutcome()
    assert result.written == 0
    assert result.failures == []
    assert result.total == 0

