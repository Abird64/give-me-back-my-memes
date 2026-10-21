"""QQ 目录探测测试（EM-004，D-07 两级链）。

守护对象：

- 注册表**主键 / 兜底键**的读取与降级顺序（含「注册表目录不存在时不信它」）；
- 两级路径链一字不差：``聊天消息`` → ``Tencent Files\\*`` →
  ``nt_qq\\nt_data\\Emoji\\personal_emoji\\Ori``；
- **不猜不遍历**：安装目录之外的诱饵目录不得被命中；
- 多 QQ 号全部返回且顺序确定；
- 找不到时的中文报错含验收标准要求的关键句。

注册表用假 ``winreg`` 注入（monkeypatch ``qqdir.winreg``），不碰真注册表。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emoji_migrator import qqdir

#: 仓库根（tests/ -> 仓库根），与 RUN-20261012-001 原子② 的锚定口径一致
REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeKey:
    """假注册表键句柄：持有一份值字典。"""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def _make_fake_winreg(values: dict[str, dict[str, str]]) -> type:
    """按 ``{键路径: {值名: 值}}`` 构造假 winreg 模块。

    语义与真 ``winreg`` 对齐：键不存在 → ``OpenKey`` 抛 ``FileNotFoundError``；
    值不存在 → ``QueryValueEx`` 抛 ``FileNotFoundError``。
    """

    class _FakeWinReg:
        HKEY_LOCAL_MACHINE = "HKLM"

        @staticmethod
        def OpenKey(root: object, path: str) -> _FakeKey:  # noqa: N802
            assert root == "HKLM", "本产品只读 HKLM"
            key_values = values.get(path)
            if key_values is None:
                raise FileNotFoundError(path)
            return _FakeKey(key_values)

        @staticmethod
        def QueryValueEx(key: _FakeKey, name: str) -> tuple[str, int]:  # noqa: N802
            if name not in key._values:
                raise FileNotFoundError(name)
            return key._values[name], 1

    return _FakeWinReg


@pytest.fixture
def patch_registry(monkeypatch: pytest.MonkeyPatch):
    """把假 winreg 装进 qqdir；返回设置器。"""

    def _install(values: dict[str, dict[str, str]]) -> None:
        monkeypatch.setattr(qqdir, "winreg", _make_fake_winreg(values))

    return _install


def _build_install_dir(root: Path, *, with_chat_dir: bool = True) -> Path:
    """按实测结构搭一套安装目录；返回安装目录路径。"""
    install = root / "qq"
    base = install / qqdir.CHAT_DIR_NAME if with_chat_dir else install
    emoji_dir = base / qqdir.TENCENT_FILES_NAME / "1234567890"
    emoji_dir = emoji_dir.joinpath(*qqdir.EMOJI_RELATIVE_PATH)
    emoji_dir.mkdir(parents=True)
    (emoji_dir / "a.jpg").write_bytes(b"x")
    return install


# --------------------------------------------------------------------------------------
# 注册表读取：主键、兜底、降级顺序
# --------------------------------------------------------------------------------------


def test_read_install_dir_from_primary_key(patch_registry, tmp_path: Path) -> None:
    """主键 Install 值存在且目录真实存在时，直接采用。

    注册表路径用**字面量**而非 ``qqdir.QQ_INSTALL_REGKEY``：自指常量会让
    「改坏键路径」这类突变只有结构守卫一条能抓（见
    :func:`test_real_machine_chain_matches_the_measured_layout`）。
    """
    install = _build_install_dir(tmp_path)
    patch_registry({r"SOFTWARE\WOW6432Node\Tencent\QQNT": {"Install": str(install)}})
    assert qqdir.read_qq_install_dir() == install


#: 兜底卸载键的字面量（测试刻意不复用 ``qqdir.QQ_UNINSTALL_REGKEY``，
#: 否则改坏键路径只有结构守卫一条能抓——见 M1 突变记录）
_UNINSTALL_KEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"


def test_primary_key_missing_falls_back_to_display_icon(patch_registry, tmp_path: Path) -> None:
    """主键缺失时走卸载信息的 DisplayIcon（形态 ``D:\\...\\QQ.exe,0``）。"""
    install = _build_install_dir(tmp_path)
    patch_registry(
        {
            _UNINSTALL_KEY: {
                "DisplayIcon": f"{install}\\QQ.exe,0",
                "UninstallString": f'"{install}\\Uninstall.exe"',
            }
        }
    )
    assert qqdir.read_qq_install_dir() == install


def test_falls_back_to_uninstall_string_when_display_icon_absent(
    patch_registry, tmp_path: Path
) -> None:
    """DisplayIcon 缺失时走 UninstallString（带引号的 exe 路径）。"""
    install = _build_install_dir(tmp_path)
    patch_registry({_UNINSTALL_KEY: {"UninstallString": f'"{install}\\Uninstall.exe"'}})
    assert qqdir.read_qq_install_dir() == install


def test_no_registry_entries_returns_none(patch_registry) -> None:
    """两个键都读不到时返回 None（由调用方给中文报错）。"""
    patch_registry({})
    assert qqdir.read_qq_install_dir() is None


def test_registry_dir_that_does_not_exist_is_rejected(patch_registry, tmp_path: Path) -> None:
    """注册表声称的目录不存在时不信它，继续走兜底；兜底也没有则 None。"""
    ghost = tmp_path / "gone"
    patch_registry(
        {
            r"SOFTWARE\WOW6432Node\Tencent\QQNT": {"Install": str(ghost)},
            _UNINSTALL_KEY: {"DisplayIcon": f"{ghost}\\QQ.exe,0"},
        }
    )
    assert qqdir.read_qq_install_dir() is None


def test_hkcu_key_is_not_consulted(patch_registry, tmp_path: Path) -> None:
    """实测 HKCU\\Software\\Tencent\\QQNT 只有 version——不得被当作安装路径来源。

    假注册表若被误改成读 HKCU，本测试的键路径根本不存在，结果只能是 None。
    """
    install = _build_install_dir(tmp_path)
    patch_registry({"Software\\Tencent\\QQNT": {"version": "9.9.32.51246"}})
    assert qqdir.read_qq_install_dir() is None
    assert install.is_dir()


# --------------------------------------------------------------------------------------
# 两级路径链
# --------------------------------------------------------------------------------------


def test_find_emoji_dirs_walks_the_two_level_chain(tmp_path: Path) -> None:
    """安装目录 → 聊天消息 → Tencent Files\\<QQ号> → ... → Ori，逐级命中。"""
    install = _build_install_dir(tmp_path)
    expected = (
        install
        / qqdir.CHAT_DIR_NAME
        / qqdir.TENCENT_FILES_NAME
        / "1234567890"
        / "nt_qq"
        / "nt_data"
        / "Emoji"
        / "personal_emoji"
        / "Ori"
    )
    assert qqdir.find_emoji_dirs(install) == [expected]


def test_find_emoji_dirs_ignores_decoys_outside_the_chain(tmp_path: Path) -> None:
    """D-07：不猜不遍历。安装目录之外的 Ori 与链外的 personal_emoji 都不得命中。"""
    install = _build_install_dir(tmp_path)

    # 诱饵 1：安装目录**父级**下的同构 Tencent Files（模拟「往上找/各盘根兜底」）
    # ——若探测链被改成扫 install_dir.parent，这条必须让它变红。
    outside = tmp_path / "Tencent Files" / "7777"
    (outside / "nt_qq" / "nt_data" / "Emoji" / "personal_emoji" / "Ori").mkdir(parents=True)

    # 诱饵 2：Tencent Files 下的非 QQ 号目录（无 nt_qq 子树）
    decoy_account = install / qqdir.CHAT_DIR_NAME / qqdir.TENCENT_FILES_NAME / "All Users"
    decoy_account.mkdir(parents=True)

    # 诱饵 3：链上级别错误的 personal_emoji（缺 nt_qq/nt_data 前缀）
    wrong_level = install / qqdir.CHAT_DIR_NAME / qqdir.TENCENT_FILES_NAME / "9999"
    (wrong_level / "Emoji" / "personal_emoji" / "Ori").mkdir(parents=True)

    found = qqdir.find_emoji_dirs(install)
    assert len(found) == 1
    assert "1234567890" in str(found[0])


def test_find_emoji_dirs_returns_all_accounts_sorted(tmp_path: Path) -> None:
    """同机多个 QQ 号全部返回，且顺序确定（按路径排序）。"""
    install = tmp_path / "qq"
    accounts = []
    for qq_number in ("1234567890", "10000", "999999999"):
        emoji_dir = (
            install
            / qqdir.CHAT_DIR_NAME
            / qqdir.TENCENT_FILES_NAME
            / qq_number
        ).joinpath(*qqdir.EMOJI_RELATIVE_PATH)
        emoji_dir.mkdir(parents=True)
        accounts.append(emoji_dir)

    assert qqdir.find_emoji_dirs(install) == sorted(accounts, key=lambda path: str(path))


def test_find_emoji_dirs_deduplicates_when_both_roots_hit(tmp_path: Path) -> None:
    """安装目录下同时有「聊天消息/Tencent Files」与「Tencent Files」且指向同一结构时去重。"""
    install = _build_install_dir(tmp_path)
    # 在安装目录根部再放一份指向不同 QQ 号的 Tencent Files
    emoji_dir = (install / qqdir.TENCENT_FILES_NAME / "55555").joinpath(*qqdir.EMOJI_RELATIVE_PATH)
    emoji_dir.mkdir(parents=True)

    found = qqdir.find_emoji_dirs(install)
    assert len(found) == 2
    assert len(set(found)) == 2


def test_find_emoji_dirs_without_tencent_files_returns_empty(tmp_path: Path) -> None:
    """安装目录下没有 Tencent Files 时返回空列表（不报错、不扫描别处）。"""
    install = tmp_path / "qq"
    (install / qqdir.CHAT_DIR_NAME).mkdir(parents=True)
    assert qqdir.find_emoji_dirs(install) == []


# --------------------------------------------------------------------------------------
# 完整探测链与中文报错
# --------------------------------------------------------------------------------------


def test_detect_full_chain_end_to_end(patch_registry, tmp_path: Path) -> None:
    """注册表 → 安装目录 → 表情目录 全链跑通（用假注册表，不碰真系统）。

    键路径同样用字面量，理由同
    :func:`test_read_install_dir_from_primary_key`。
    """
    install = _build_install_dir(tmp_path)
    patch_registry({r"SOFTWARE\WOW6432Node\Tencent\QQNT": {"Install": str(install)}})

    expected = (
        install / qqdir.CHAT_DIR_NAME / qqdir.TENCENT_FILES_NAME / "1234567890"
    ).joinpath(*qqdir.EMOJI_RELATIVE_PATH)
    assert qqdir.detect_qq_emoji_dirs() == [expected]
    assert qqdir.detect_qq_emoji_dir() == expected


def test_detect_returns_empty_when_registry_silent(patch_registry) -> None:
    """注册表什么都不说时返回空列表——GUI 据此展示中文报错。"""
    patch_registry({})
    assert qqdir.detect_qq_emoji_dirs() == []


def test_detect_qq_emoji_dir_raises_with_chinese_message(patch_registry) -> None:
    """找不到时抛 QQDirsNotFoundError，消息含验收标准要求的关键句。"""
    patch_registry({})
    with pytest.raises(qqdir.QQDirsNotFoundError) as excinfo:
        qqdir.detect_qq_emoji_dir()
    message = str(excinfo.value)
    assert "没找到 QQ 表情目录" in message
    assert "请确认 QQ 已启动过并打开过收藏表情" in message
    assert "手动选择目录" in message


def test_not_found_message_is_the_acceptance_wording() -> None:
    """NOT_FOUND_MESSAGE 常量本身逐字包含验收关键句（防文案被改坏）。"""
    assert "没找到 QQ 表情目录，请确认 QQ 已启动过并打开过收藏表情" in qqdir.NOT_FOUND_MESSAGE


def test_detection_on_non_windows_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 Windows 平台（winreg 不可用）时探测恒为空，不得抛 import 错。"""
    monkeypatch.setattr(qqdir, "winreg", None)
    assert qqdir.detect_qq_emoji_dirs() == []


def test_real_machine_chain_matches_the_measured_layout() -> None:
    """结构守卫：实测链的各级名字必须与模块常量一致（防常量被改坏后测试全绿）。

    这里不断言真实文件系统（CI/别的机器上没有 QQ），只断言**链的定义**：
    EM-004 开工前实测的结构是 ``聊天消息/Tencent Files/<QQ号>/nt_qq/nt_data/
    Emoji/personal_emoji/Ori``，任何一级改名都必须让本测试变红。
    """
    assert qqdir.CHAT_DIR_NAME == "聊天消息"
    assert qqdir.TENCENT_FILES_NAME == "Tencent Files"
    assert qqdir.EMOJI_RELATIVE_PATH == ("nt_qq", "nt_data", "Emoji", "personal_emoji", "Ori")
    assert qqdir.QQ_INSTALL_REGKEY == r"SOFTWARE\WOW6432Node\Tencent\QQNT"
    assert qqdir.QQ_INSTALL_VALUE == "Install"
    assert qqdir.QQ_UNINSTALL_REGKEY.endswith(r"Uninstall\QQ")
