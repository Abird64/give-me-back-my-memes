"""微信表情目录探测（**只读**，不写注册表、不写文件系统）。

与 :mod:`emoji_migrator.qqdir` 同级的**叶子模块**，只 import 标准库。

⚠️ **与 QQ 的关键差异：数据目录不在注册表 ``InstallPath`` 之下**
（2026-10-13 与 2026-10-20 两次实测均如此）：

- 注册表 ``HKCU\\Software\\Tencent\\Weixin`` 的 ``InstallPath`` 实测是
  ``D:\\application\\lnstallation\\wechat\\Weixin``——注意末尾是 ``Weixin``，
  那是**程序子目录**（内含 ``4.1.15.13`` / ``Weixin.exe`` / ``Uninstall.exe``），
- 而表情数据实际在它的**兄弟目录**：
  ``D:\\application\\lnstallation\\wechat\\聊天记录\\xwechat_files\\``。

即数据目录在 ``InstallPath`` 的**父目录**下。探测链因此为：

1. 注册表（``winreg``，只读）读**程序目录**：``HKCU`` 后 ``HKLM`` 两个视图；
2. 从程序目录**逐级向上**（默认 3 级，见 :data:`ASCEND_LEVELS`），
   每级找 ``聊天记录\\xwechat_files`` 或 ``xwechat_files``；
3. ``wxid_*`` 账号目录下取 ``business\\emoticon``。

⚠️ **不做全盘搜索**：找不到就返回空列表，由调用方给中文报错+ 手动选择入口
（与 qqdir 同一策略，见 :data:`NOT_FOUND_MESSAGE`）。

⚠️ **不遍历 ``Thumb`` 等大目录**：本机实测 ``Persist`` 1138 / ``Thumb`` 1405 /
``PersistStore`` 15 / ``ThumbStore`` 30，探测阶段只确认目录**存在**，不逐个 stat。
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg
else:  # pragma: no cover - 本产品是Windows 专用工具
    winreg = None

__all__ = [
    "WEIXIN_REGKEY",
    "INSTALL_PATH_VALUE",
    "CHAT_DIR_NAME",
    "XWECHAT_FILES_NAME",
    "EMOTICON_RELATIVE_PATH",
    "PERSIST_DIR_NAME",
    "WXID_PREFIX",
    "ACCOUNT_SUFFIX_LEN",
    "WXID_MIN_BODY",
    "ASCEND_LEVELS",
    "NOT_FOUND_MESSAGE",
    "WXDirsNotFoundError",
    "read_weixin_install_dir",
    "find_xwechat_roots",
    "find_emoticon_dirs",
    "detect_weixin_emoticon_dirs",
    "detect_weixin_emoticon_dir",
    "wxid_of",
]

#: 注册表键：微信安装信息（**程序目录**，不是数据目录——见模块 docstring）
WEIXIN_REGKEY = r"Software\Tencent\Weixin"

#: 保存程序目录的值名
INSTALL_PATH_VALUE = "InstallPath"

#: 微信 4.x 的数据目录名——**中文**（本机实测：``聊天记录``）
CHAT_DIR_NAME = "聊天记录"

#: 微信 4.x 数据根目录名
XWECHAT_FILES_NAME = "xwechat_files"

#: 从安装目录向上找数据目录的**最大级数**（含安装目录自身那一级）。
#:
#: ⚠️ **实测（2026-10-20）：注册表 ``InstallPath`` 指到``<安装根>\\Weixin``，
#: 而数据在 ``<安装根>\\聊天记录\\xwechat_files``——两者是**兄弟目录**，
#: 数据目录在 ``InstallPath`` 的**父目录**下。**故必须向上找。**
#: 3 级足够覆盖「安装根\\Weixin」「安装根\\微信」「D:\\Weixin4.0」等常见形态；
#: 刻意不做无界向上（防止一路爬到盘根），也不做全盘搜索（D-07 同款保守策略）。
ASCEND_LEVELS = 3

#: 从 ``wxid_*`` 账号目录到表情目录的相对路径（实测结构）
EMOTICON_RELATIVE_PATH = ("business", "emoticon")

#: 单个表情的存放目录名（其余同级目录见模块 docstring 的实测计数）
PERSIST_DIR_NAME = "Persist"

#: 账号目录前缀（``wxid_example_ab12_cd34``）
WXID_PREFIX = "wxid_"

#: 账号目录名的固定后缀长度（实测 5 字符，形如 ``_cd34``；开源项目里见到 ``_b487``，同为 5）。
#: ⚠️ **这是推断值**：不能保证所有微信版本都是 5 字符。故按「去掉末尾 N 字符」
#: 处理并在 :func:`wxid_of` 里做结构校验——校验不过就**原样返回目录名**，
#: 宁可让派生 key 不匹配（表现为「解不开」而非「静默解错」），也不瞎猜 wxid。
ACCOUNT_SUFFIX_LEN = 5

#: ``wxid_`` 之后主体的**最小**字符数。
#:
#: ⚠️ 阈值取值依据：本机实测主体为 ``3lp462qs71jr22``（12 字符）。
#: 初版取「> 4 字符」，结果把 ``wxid_abc_b487`` 这类短主体误判为不自洽而原样返回——
#: **过严的校验和没有校验一样有害**（前者导致「明明该去尾却不去」）。
#: 取3：覆盖 ``abc`` 这类短主体，同时挡住 ``wxid_x`` / ``wxid_`` 这类明显不是 wxid 的。
WXID_MIN_BODY = 3

#: 找不到时的中文报错（验收要求的关键句必须逐字包含）
NOT_FOUND_MESSAGE = (
    "没找到微信表情目录，请确认微信已启动过并登录过、且打开过任意表情；"
    "也可以点「手动选择目录」直接指定到 xwechat_files/wxid_xxx目录。"
)


class WXDirsNotFoundError(LookupError):
    """:func:`detect_weixin_emoticon_dir` 找不到目录时抛出，消息为中文可读指引。"""


def _query_value(root: int, key_path: str, value_name: str) -> str | None:
    """只读查询注册表字符串值；键/值缺失或不可读返回 ``None``。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, key_path) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
    except OSError:
        # FileNotFoundError：键或值不存在；PermissionError / OSError：不可读。都按「没有」处理。
        return None
    text = str(value).strip().strip('"')
    return text or None


def read_weixin_install_dir() -> Path | None:
    """读微信**程序目录**（不是数据目录）；读不到或目录不存在返回 ``None``。

    先 ``HKCU`` 后 ``HKLM``：本机实测写在 ``HKCU\\Software\\Tencent\\Weixin``，
    但部分安装/策略会落到 HKLM，两个视图都试一遍成本极低。

    注册表声称的目录必须**真实存在**：指到已卸载残留的路径时返回 ``None``，
    避免把用户引向不存在的目录（与 qqdir 同一保守策略）。
    """
    if winreg is None:
        return None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        raw = _query_value(root, WEIXIN_REGKEY, INSTALL_PATH_VALUE)
        if raw is None:
            continue
        candidate = Path(raw)
        if candidate.is_dir():
            return candidate
    return None


def _ascend_levels(install_dir: Path) -> list[Path]:
    """从安装目录起，向上取 :data:`ASCEND_LEVELS` 级（含自身），按由近到远排序。

    实测依据：注册表给的是 ``<安装根>\\Weixin``，数据在 ``<安装根>\\聊天记录``
    ——**兄弟目录**，故必须向上看。只取有限级（默认 3 级），
    不一路爬到盘根（那是全盘搜索的变体，慢且易命中无关目录）。
    """
    levels = [install_dir]
    current = install_dir
    for _ in range(ASCEND_LEVELS - 1):
        parent = current.parent
        if parent == current:  # 已到盘根
            break
        levels.append(parent)
        current = parent
    return levels


def find_xwechat_roots(install_dir: Path) -> list[Path]:
    """定位 ``xwechat_files``，返回去重排序后的列表。

    **逐级向上**（见 :func:`_ascend_levels`），每级看两个候选：

    1. ``<该级>\\聊天记录\\xwechat_files``——**本机实测命中**（4.x 默认形态）；
    2. ``<该级>\\xwechat_files``——无中文子目录的安装形态。

    先近后远收集，最终**按路径排序**返回（与 qqdir 一致，
    保证多账号时的选择顺序稳定）。
    """
    found: list[Path] = []
    for level in _ascend_levels(install_dir):
        for candidate in (
            level / CHAT_DIR_NAME / XWECHAT_FILES_NAME,
            level / XWECHAT_FILES_NAME,
        ):
            if candidate.is_dir():
                found.append(candidate)
    return sorted(set(found), key=lambda p: str(p))


def wxid_of(account_dir_name: str) -> str:
    """从账号目录名取 ``wxid``（去掉固定尾后缀）。

    ⚠️ 后缀长度是**推断值**（见 :data:`ACCOUNT_SUFFIX_LEN`）。策略：

    - 去尾后必须仍以 ``wxid_`` 开头，且主体至少 :data:`WXID_MIN_BODY` 字符；
    - 校验不过就**原样返回目录名**——宁可让派生 key 不匹配
      （表现为「解不开」这个可见错误），也**不用一个瞎猜的 wxid 去算 key**
      （那会解出错误内容，是静默失败）。

    实测（2026-10-20，本机）：``wxid_example_ab12_cd34`` → ``wxid_example_ab12``，
    与探针实测命中时使用的 wxid **完全一致**。
    """
    if not account_dir_name.startswith(WXID_PREFIX):
        return account_dir_name
    if len(account_dir_name) <= ACCOUNT_SUFFIX_LEN:
        return account_dir_name
    trimmed = account_dir_name[: -ACCOUNT_SUFFIX_LEN]
    if (
        trimmed.startswith(WXID_PREFIX)
        and len(trimmed) - len(WXID_PREFIX) >= WXID_MIN_BODY
    ):
        return trimmed
    return account_dir_name


def find_emoticon_dirs(xwechat_root: Path) -> list[Path]:
    """在一个 ``xwechat_files`` 根下找出各账号的表情目录。

    只看 ``wxid_*`` 目录（不读其他目录，避免误入``all_users`` 等）。
    多账号（如主号 + 小号）全部返回，按路径排序。
    """
    found: list[Path] = []
    try:
        entries = sorted(xwechat_root.iterdir())
    except OSError:
        return []
    for account_dir in entries:
        if not account_dir.is_dir():
            continue
        if not account_dir.name.startswith(WXID_PREFIX):
            continue
        emoticon = account_dir.joinpath(*EMOTICON_RELATIVE_PATH)
        if emoticon.is_dir():
            found.append(emoticon)
    return found


def detect_weixin_emoticon_dirs() -> list[Path]:
    """完整探测链：注册表 → 程序目录 → xwechat_files → emoticon。

    任一步缺失返回**空列表**（不抛异常）——与 :func:`emoji_migrator.qqdir.detect_qq_emoji_dirs`
    同一约定，由调用方决定报不报错。
    """
    if winreg is None:
        return []
    install_dir = read_weixin_install_dir()
    if install_dir is None:
        return []
    found: list[Path] = []
    for root in find_xwechat_roots(install_dir):
        found.extend(find_emoticon_dirs(root))
    return sorted(set(found), key=lambda p: str(p))


def detect_weixin_emoticon_dir() -> Path:
    """探测表情目录；多账号时取排序后的第一个。

    :raises WXDirsNotFoundError: 探测链没找到（消息含中文手动选择指引）。
    """
    dirs = detect_weixin_emoticon_dirs()
    if not dirs:
        raise WXDirsNotFoundError(NOT_FOUND_MESSAGE)
    return dirs[0]
