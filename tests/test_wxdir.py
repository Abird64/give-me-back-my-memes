"""微信表情目录探测（:mod:`emoji_migrator.wxdir`）测试。

**全部用桩目录**（``tmp_path``），**不碰真实微信安装、不读真实注册表**——
:func:`wxdir.detect_weixin_emoticon_dir` 在测试里绝不调用未打桩的版本。

守护三条线：
1. **向上找**（实测关键：注册表给的是 ``<根>\\Weixin``，数据在**兄弟目录** ``<根>\\聊天记录``）；
2. **不越界**：不做全盘搜索，向上级数有上限；
3. **wxid 推导的保守性**：结构不自洽时**原样返回**（宁可解不开，不静默解错）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emoji_migrator import wxdir


def _make_tree(root: Path, *, data_level: int = 1, chat_sub: bool = True) -> Path:
    """造一棵「程序目录 + 数据目录」的树，返回 xwechat_files 路径。

    :param data_level: 数据目录相对程序目录**向上**几级（1 = 兄弟目录）。
    :param chat_sub: 是否套一层中文 ``聊天记录`` 子目录。

    ⚠️ 必须真的把数据目录 ``mkdir`` 出来——``find_xwechat_roots`` 用
    ``is_dir()`` 判定，只造路径不建目录会得到空列表（第一版就栽在这，
    三个向上查找的用例全红looked like模块 bug，实为helper 漏建目录）。
    """
    install = root / "Weixin"
    install.mkdir(parents=True)
    (install / "Weixin.exe").write_bytes(b"MZ")  # 程序目录特征文件
    base = install
    for _ in range(data_level - 1):
        base = base.parent
    data = base / (wxdir.CHAT_DIR_NAME if chat_sub else "") / wxdir.XWECHAT_FILES_NAME
    data.mkdir(parents=True)  # ←关键：真的建出来
    return data


def _make_account(xwechat: Path, name: str = "wxid_abc12345678_cd34") -> Path:
    """在 xwechat 下造一个账号的 emoticon 目录，返回它。"""
    emoticon = xwechat / name / "business" / "emoticon"
    (emoticon / wxdir.PERSIST_DIR_NAME).mkdir(parents=True)
    return emoticon


# -- wxid 推导 ---------------------------------------------------------------


def test_wxid_of_real_machine_value() -> None:
    """本机实测值：去掉5 字符尾后缀。"""
    assert wxdir.wxid_of("wxid_example_ab12_cd34") == "wxid_example_ab12"


def test_wxid_of_other_suffix_variant() -> None:
    """开源项目里见到 ``_b487``——同为 5 字符，须同样处理（版本差异不能挂）。"""
    assert wxdir.wxid_of("wxid_abc_b487") == "wxid_abc"


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("wxid_", "空主体，不该动"),
        ("wxid_x", "主体仅 1 字符，不足下限"),
        ("wxid_ab", "主体仅 2 字符，不足下限"),
        ("other_name", "压根不是 wxid 目录"),
        ("", "空串"),
    ],
)
def test_wxid_of_returns_unchanged_when_implausible(raw: str, why: str) -> None:
    """结构不自洽时**原样返回**——宁可让 key 不匹配，也不瞎猜 wxid 去算 key。

    瞎猜的后果是「解出错误内容」= 静默失败，比「解不开」严重得多。
    """
    assert wxdir.wxid_of(raw) == raw, why


def test_wxid_of_shorter_than_suffix_untouched() -> None:
    """名字比后缀还短时不做切片（否则会切出空串）。"""
    assert wxdir.wxid_of("wxid") == "wxid"


# -- 逐级向上 ---------------------------------------------------------------


def test_finds_data_dir_as_sibling_of_install(tmp_path: Path) -> None:
    """关键场景（实测形态）：数据在程序目录的**兄弟**里。

    注册表给 ``<根>\\Weixin``，数据在 ``<根>\\聊天记录\\xwechat_files``——
    不向上找就会漏（初版就是这么漏的，探测链返回空列表）。
    """
    xwechat = _make_tree(tmp_path, data_level=1, chat_sub=True)
    install = tmp_path / "Weixin"
    assert wxdir.find_xwechat_roots(install) == [xwechat]


def test_finds_data_dir_without_chinese_subdir(tmp_path: Path) -> None:
    """无中文子目录的形态：``<根>\\xwechat_files`` 直接挂在同级。"""
    xwechat = _make_tree(tmp_path, data_level=1, chat_sub=False)
    install = tmp_path / "Weixin"
    assert wxdir.find_xwechat_roots(install) == [xwechat]


def test_finds_data_dir_two_levels_up(tmp_path: Path) -> None:
    """数据在程序目录**上两级**（部分安装形态）。"""
    xwechat = _make_tree(tmp_path, data_level=2, chat_sub=True)
    install = tmp_path / "Weixin"
    assert wxdir.find_xwechat_roots(install) == [xwechat]


def test_ascend_is_bounded(tmp_path: Path) -> None:
    """向上查找**有级数上限**，不做无界上溯（那是全盘搜索的变体）。"""
    install = tmp_path / "a" / "b" / "c" / "Weixin"
    install.mkdir(parents=True)
    levels = wxdir._ascend_levels(install)
    assert len(levels) <= wxdir.ASCEND_LEVELS
    assert levels[0] == install, "第一级必须是安装目录自身"


def test_ascend_stops_at_drive_root() -> None:
    """级数够到达盘根时，返回的最后一级**就是**盘根，不越界。

    ``C:/a/b/Weixin`` 向上 3 级 = ``C:/a``；再用 ``ASCEND_LEVELS=4``
    时才到 ``C:/``。本测试直接验证两件事：
    1. 返回的每一级都是上一级的 ``parent``（链式正确）；
    2. 级数够时最后一级恰好是盘根（不越界到 ``C:/..`` 之类）。
    """
    deep = Path("C:/a/b/Weixin")
    default_levels = wxdir._ascend_levels(deep)
    assert default_levels[0] == deep
    for prev, nxt in zip(default_levels, default_levels[1:], strict=False):
        assert nxt == prev.parent, "每一级都必须是上一级的 parent"

    # 级数放大到 4 时应恰好停在盘根（Path 的盘根 parent 是自身）
    monkey = wxdir.ASCEND_LEVELS
    try:
        wxdir.ASCEND_LEVELS = 4
        levels = wxdir._ascend_levels(deep)
        assert levels[-1] == Path("C:/"), "4 级应到盘根"
        assert len(levels) == 4
    finally:
        wxdir.ASCEND_LEVELS = monkey


def test_no_false_positive_when_nothing_matches(tmp_path: Path) -> None:
    """没有数据目录时返回空列表（不猜、不报错）。"""
    install = tmp_path / "Weixin"
    install.mkdir()
    assert wxdir.find_xwechat_roots(install) == []


# -- 账号与表情目录 ----------------------------------------------------------


def test_find_emoticon_dirs_per_account(tmp_path: Path) -> None:
    """多账号时全部返回，按路径排序（选择顺序稳定）。"""
    xwechat = tmp_path / wxdir.XWECHAT_FILES_NAME
    _make_account(xwechat, "wxid_bbbbbbbbbb_cd34")
    _make_account(xwechat, "wxid_aaaaaaaaaa_cd34")
    found = wxdir.find_emoticon_dirs(xwechat)
    assert len(found) == 2
    assert found == sorted(found, key=lambda p: str(p)), "必须按路径排序"


def test_find_emoticon_dirs_ignores_non_wxid_dirs(tmp_path: Path) -> None:
    """非 ``wxid_*`` 目录一律不看（避免误入 all_users 等）。

    ⚠️ 关键：``all_users`` 下**必须真的建出** ``business/emoticon/Persist``。
    突变实验 M5「删掉 wxid 前缀过滤」第一版是零覆盖——因为本用例当初只
    ``mkdir`` 了空的 ``all_users/``，它下面没有 emoticon 目录，于是
    ``emoticon.is_dir()`` 挡住了突变，行为看起来没变。
    **造桩必须造到能让突变真正暴露的深度**，否则守护是假的。
    """
    xwechat = tmp_path / wxdir.XWECHAT_FILES_NAME
    _make_account(xwechat)
    # 非账号目录，但**结构与账号完全相同**——只靠 is_dir() 区分不了，
    # 必须靠 wxid_ 前缀过滤。
    decoy = xwechat / "all_users" / "business" / "emoticon" / wxdir.PERSIST_DIR_NAME
    decoy.mkdir(parents=True)
    found = wxdir.find_emoticon_dirs(xwechat)
    assert len(found) == 1, "非wxid 目录即使结构完整也不得被当成账号"
    assert "all_users" not in str(found[0])


def test_find_emoticon_dirs_tolerates_unreadable_root(tmp_path: Path) -> None:
    """根目录不可访问时返回空列表（不抛异常）。"""
    missing = tmp_path / "does-not-exist"
    assert wxdir.find_emoticon_dirs(missing) == []


# -- 完整探测链 ---------------------------------------------------------------


def test_detect_chain_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """注册表 → 程序目录 → 向上找 → 账号表情目录，全链打通。"""
    xwechat = _make_tree(tmp_path, data_level=1, chat_sub=True)
    emoticon = _make_account(xwechat)
    install = tmp_path / "Weixin"
    monkeypatch.setattr(wxdir, "read_weixin_install_dir", lambda: install)
    monkeypatch.setattr(wxdir, "winreg", object())  # 非None 即视为 Windows 可用
    assert wxdir.detect_weixin_emoticon_dirs() == [emoticon]


def test_detect_returns_empty_when_registry_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册表读不到 → 空列表（由调用方决定报不报错，与 qqdir 同一约定）。"""
    monkeypatch.setattr(wxdir, "read_weixin_install_dir", lambda: None)
    monkeypatch.setattr(wxdir, "winreg", object())
    assert wxdir.detect_weixin_emoticon_dirs() == []


def test_detect_returns_empty_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """非Windows 上恒不可用（``winreg`` 为 None）。"""
    monkeypatch.setattr(wxdir, "winreg", None)
    assert wxdir.detect_weixin_emoticon_dirs() == []


def test_detect_single_raises_chinese_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单目录探测失败时抛中文可读异常，且消息含「手动选择」指引。"""
    monkeypatch.setattr(wxdir, "detect_weixin_emoticon_dirs", lambda: [])
    with pytest.raises(wxdir.WXDirsNotFoundError) as excinfo:
        wxdir.detect_weixin_emoticon_dir()
    assert "手动选择" in str(excinfo.value)


def test_detect_single_returns_first_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多账号时取**排序后**的第一个（顺序稳定，不随文件系统返回顺序变）。

    ⚠️ 本函数只保证「取列表首个」，排序责任在
    :func:`detect_weixin_emoticon_dirs`（已单测）——这里用已排序的列表验证取值。
    """
    a = Path("D:/wx/x/business/emoticon")
    b = Path("D:/wx/y/business/emoticon")
    monkeypatch.setattr(wxdir, "detect_weixin_emoticon_dirs", lambda: [a, b])
    assert wxdir.detect_weixin_emoticon_dir() == a


# -- 注册表读取（打桩winreg）--------------------------------------------------


def test_read_install_dir_returns_none_when_winreg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 Windows 上恒返回 None。"""
    monkeypatch.setattr(wxdir, "winreg", None)
    assert wxdir.read_weixin_install_dir() is None


def test_read_install_dir_rejects_nonexistent_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册表声称的目录必须**真实存在**——指到卸载残留时不返回（保守策略）。

    桩的结构要点：``QueryValueEx`` 是 **winreg 模块级函数**（不是 key 的方法），
    ``OpenKey`` 返回可作 context manager 的对象。第一版把 ``QueryValueEx``
    挂在 key 类上，模块级调用直接 ``AttributeError``——桩写错，
    症状看起来却像模块 bug。
    """

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeWinreg:
        HKEY_CURRENT_USER = "HKCU"
        HKEY_LOCAL_MACHINE = "HKLM"

        @staticmethod
        def OpenKey(root, key_path):  # noqa: N802 - 模拟 winreg API
            return _Key()

        @staticmethod
        def QueryValueEx(key, name):  # noqa: N802 - 模拟 winreg API
            return (r"D:\definitely\not\here", 1)

    monkeypatch.setattr(wxdir, "winreg", _FakeWinreg)
    assert wxdir.read_weixin_install_dir() is None


def test_read_install_dir_tries_both_registry_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HKCU 抛错时继续试 HKLM（部分安装写在 HKLM，两个视图都要试）。"""
    seen: list[str] = []
    real_dir = Path(__file__).resolve().parents[1]  # 真实存在的目录

    class _Key:
        def __init__(self, root):
            self._root = root

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeWinreg:
        HKEY_CURRENT_USER = "HKCU"
        HKEY_LOCAL_MACHINE = "HKLM"

        @staticmethod
        def OpenKey(root, key_path):  # noqa: N802 - 模拟 winreg API
            seen.append(root)
            return _Key(root)

        @staticmethod
        def QueryValueEx(key, name):  # noqa: N802 - 模拟 winreg API
            if key._root == "HKCU":
                raise FileNotFoundError(name)
            return (str(real_dir), 1)

    monkeypatch.setattr(wxdir, "winreg", _FakeWinreg)
    assert wxdir.read_weixin_install_dir() == real_dir
    assert seen == ["HKCU", "HKLM"], "必须按 HKCU → HKLM 顺序各试一次"


# -- 常量口径（防改坏）--------------------------------------------------------


def test_relative_path_matches_em003_spec() -> None:
    """相对路径常量与 EM-003 验收口径一致（business/emoticon）。"""
    assert wxdir.EMOTICON_RELATIVE_PATH == ("business", "emoticon")


def test_persist_dir_name_is_persist() -> None:
    """单个表情存放目录名固定为 Persist。"""
    assert wxdir.PERSIST_DIR_NAME == "Persist"


def test_not_found_message_contains_manual_hint() -> None:
    """找不到时的中文报错必须含「手动选择」指引（验收要求）。"""
    assert "手动选择" in wxdir.NOT_FOUND_MESSAGE
    assert "没找到微信表情目录" in wxdir.NOT_FOUND_MESSAGE
