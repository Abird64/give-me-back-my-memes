"""QQ 表情目录探测（**只读**，不写注册表、不写文件系统）。

D-07 裁决 B：探测链**只有两级**，不做文档 / 各盘根兜底扫描：

1. 读注册表取 QQ 安装目录（标准库 ``winreg``，只读）：

   - 主键：``HKLM\\SOFTWARE\\WOW6432Node\\Tencent\\QQNT`` 的 ``Install`` 值。
     2026-10-14 实测这是安装路径的**唯一真源**——
     ``HKCU\\Software\\Tencent\\QQNT`` 只有 ``version``，没有安装路径。
   - 兜底键：``HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\
     Uninstall\\QQ`` 的 ``DisplayIcon`` / ``UninstallString``
     （形如 ``D:\\...\\QQ.exe,0``，取可执行文件所在目录）。

2. 安装目录下逐级找：``聊天消息`` → ``Tencent Files\\*`` →
   ``nt_qq\\nt_data\\Emoji\\personal_emoji\\Ori``。
   ``*`` 是 QQ 号目录，同机登录过多个 QQ 时全部返回（按路径排序）。

找不到时**不猜、不遍历全盘**：:func:`detect_qq_emoji_dirs` 返回空列表，
由调用方给出中文可读报错（:data:`NOT_FOUND_MESSAGE`）并提供「手动选择目录」入口。

本模块是叶子依赖：只 import 标准库，不 import 包内其他模块
（``AGENTS.md`` §3 依赖方向：新增模块与 ``sniff``/``normalize`` 同级）。
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - 本产品是 Windows 专用工具；非 Windows 上探测恒不可用
    winreg = None

__all__ = [
    "QQ_INSTALL_REGKEY",
    "QQ_INSTALL_VALUE",
    "QQ_UNINSTALL_REGKEY",
    "CHAT_DIR_NAME",
    "TENCENT_FILES_NAME",
    "EMOJI_RELATIVE_PATH",
    "NOT_FOUND_MESSAGE",
    "QQDirsNotFoundError",
    "read_qq_install_dir",
    "find_emoji_dirs",
    "detect_qq_emoji_dirs",
    "detect_qq_emoji_dir",
]

#: 主注册表键：QQNT 安装信息（32 位视图，实测路径）
QQ_INSTALL_REGKEY = r"SOFTWARE\WOW6432Node\Tencent\QQNT"

#: 主键中保存安装目录的值名
QQ_INSTALL_VALUE = "Install"

#: 兜底注册表键：Windows 卸载信息里的 QQ 条目
QQ_UNINSTALL_REGKEY = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"

#: 安装目录下保存聊天数据（含表情）的目录名——**中文**，实测如此
CHAT_DIR_NAME = "聊天消息"

#: QQ 号目录的父目录名
TENCENT_FILES_NAME = "Tencent Files"

#: 从 QQ 号目录到表情目录的相对路径（实测结构，一字不能差）
EMOJI_RELATIVE_PATH = ("nt_qq", "nt_data", "Emoji", "personal_emoji", "Ori")

#: 两级探测都失败时的中文报错。验收标准要求的关键句
#: 「没找到 QQ 表情目录，请确认 QQ 已启动过并打开过收藏表情」必须逐字包含。
NOT_FOUND_MESSAGE = (
    "没找到 QQ 表情目录，请确认 QQ 已启动过并打开过收藏表情；"
    "也可以点「手动选择目录」自己指定。"
)


class QQDirsNotFoundError(LookupError):
    """:func:`detect_qq_emoji_dir` 找不到目录时抛出，消息为中文可读指引。"""


def _query_value(key_path: str, value_name: str) -> str | None:
    """只读查询 ``HKLM\\<key_path>`` 的字符串值；键/值缺失或不可读返回 None。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
    except OSError:
        # FileNotFoundError：键或值不存在；PermissionError/OSError：不可读。都按「没有」处理。
        return None
    text = str(value).strip().strip('"')
    return text or None


def _install_dir_from_uninstall() -> Path | None:
    """从卸载信息的 ``DisplayIcon`` / ``UninstallString`` 反推安装目录。

    两个值的形态分别是 ``D:\\...\\QQ.exe,0``（逗号后是图标序号）与
    ``"D:\\...\\Uninstall.exe"``（可能带引号），统一取可执行文件的父目录。
    """
    for value_name in ("DisplayIcon", "UninstallString"):
        raw = _query_value(QQ_UNINSTALL_REGKEY, value_name)
        if raw is None:
            continue
        exe = Path(raw.split(",")[0].strip().strip('"'))
        if exe.parent.is_dir():
            return exe.parent
    return None


def read_qq_install_dir() -> Path | None:
    """按「主键 → 兜底键」顺序读 QQ 安装目录；都读不到返回 None。

    注册表声称的目录必须**真实存在**：指到已卸载残留的路径时继续走兜底，
    避免把用户引向一个不存在的目录。
    """
    raw = _query_value(QQ_INSTALL_REGKEY, QQ_INSTALL_VALUE)
    if raw is not None:
        candidate = Path(raw)
        if candidate.is_dir():
            return candidate
    return _install_dir_from_uninstall()


def find_emoji_dirs(install_dir: Path) -> list[Path]:
    """在安装目录下按两级链找表情目录，返回去重、按路径排序的列表。

    只探测 ``install_dir`` 之内的两级（``聊天消息`` 与安装目录本身各看一眼
    ``Tencent Files``）；安装目录之外的任何位置一律不看——D-07 明令不遍历全盘。
    """
    found: list[Path] = []
    for root in (install_dir / CHAT_DIR_NAME, install_dir):
        tencent_files = root / TENCENT_FILES_NAME
        if not tencent_files.is_dir():
            continue
        for account_dir in sorted(tencent_files.iterdir()):
            if not account_dir.is_dir():
                continue
            emoji_dir = account_dir.joinpath(*EMOJI_RELATIVE_PATH)
            if emoji_dir.is_dir():
                found.append(emoji_dir)
    # 安装目录下同时存在「聊天消息/Tencent Files」与「Tencent Files」时同一目录
    # 可能被命中两次，按路径去重（Path 可哈希）。
    return sorted(set(found), key=lambda path: str(path))


def detect_qq_emoji_dirs() -> list[Path]:
    """完整探测链：注册表 → 安装目录 → 表情目录。任一步缺失返回空列表。"""
    if winreg is None:
        return []
    install_dir = read_qq_install_dir()
    if install_dir is None:
        return []
    return find_emoji_dirs(install_dir)


def detect_qq_emoji_dir() -> Path:
    """探测表情目录；多 QQ 号时取排序后的第一个。

    :raises QQDirsNotFoundError: 两级链都没找到（消息含中文手动选择指引）。
    """
    dirs = detect_qq_emoji_dirs()
    if not dirs:
        raise QQDirsNotFoundError(NOT_FOUND_MESSAGE)
    return dirs[0]
