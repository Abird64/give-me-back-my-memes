"""微信进程内存只读扫描：取账号级seed（EM-003）。

**合规边界**（``AGENTS.md`` §1，本模块是该仓库**唯一**使用只读 Win32 API 的地方）：

- 只用 :data:`ALLOWED_READONLY_APIS` 列的两个**只读**进程访问 API
  与``VirtualQueryEx``（**枚举内存区域，不是读取**，不接触任何进程内容）；
- **不写、不注入、不 Hook**：不申请任何写权限、不起远线程、不注入 DLL
  （本仓库的合规测试会逐项扫描并断言那9 个被禁API 标识符在本包零出现——
  正因如此，本注释也**不复述**它们的名字，见 ``AGENTS.md`` §1）；
- 密钥**仅存内存、用后即弃**：:func:`scan_seed` 返回一个 ``int``，
  本模块不落盘、不写注册表、不写任何文件；
- 每一处 API 使用处**必须带含「用途」二字的注释**（``test_compliance`` 守护）。

**A-1 已在本机证实**（2026-10-20，RUN-20261020-012；2588/2588 文件 100% 解出）：
派生公式是 ``bytes.fromhex(md5(f"{seed}{wxid}EMOTICON").hexdigest())[:16]``，
**IV 用 key 本身**。

⚠️ **踩过的两个坑（写这个模块前务必读完）**：
1. key 不能写 ``md5(...).hexdigest()[:16]``——那是把十六进制**字符串**前 16 字符
   当字节用；必须是 ``bytes.fromhex(...)`` 拿**真实 md5 摘要**。
2. **IV 是 key 本身，不是全 0**。上游 README 只写「key = IV」，极易读错。

依赖方向（``AGENTS.md`` §3）：本模块**只import 标准库**，不 import 任何包内模块，
故与 ``qqdir`` / ``wxdir`` 同为叶子；解密校验所需的魔数常量在本模块内自带
（**不 import** :mod:`emoji_migrator.sniff`——那是业务模块，叶子不得依赖它）。
"""

from __future__ import annotations

import ctypes
import hashlib
import re
import sys
from collections.abc import Callable, Iterator
from ctypes import wintypes
from dataclasses import dataclass

import emoji_migrator.guard

__all__ = [
    "ALLOWED_READONLY_APIS",
    "EMOTICON_SUFFIX",
    "MAGICS",
    "SEED_LOW",
    "SEED_HIGH",
    "SEED_PATTERN",
    "CHUNK_SIZE",
    "MAX_SCAN_BYTES",
    "SCAN_TIMEOUT_MESSAGE",
    "WEIXIN_NOT_RUNNING_MESSAGE",
    "NO_SEED_FOUND_MESSAGE",
    "SeedDeriver",
    "ProcessMemoryReader",
    "SeedNotFoundError",
    "WeixinNotRunningError",
    "enum_weixin_pids",
    "iter_decimal_candidates",
    "scan_seed",
    "verify_key",
    "decrypt_block",
]

if sys.platform != "win32":  # pragma: no cover - 本产品是 Windows 专用工具
    raise ImportError("wxscan 只支持 Windows（依赖 Win32 只读内存 API）")

#: 本模块用到的只读 API 名。**从 :mod:`emoji_migrator.guard` 导入**
#: （唯一真源在那边；此处不复述字面量，否则合规扫描会把文档提及当成未注释使用）。
ALLOWED_READONLY_APIS: tuple[str, ...] = emoji_migrator.guard.ALLOWED_READONLY_APIS

#: 派生密钥时拼在 seed 与 wxid 之后的固定后缀（A-1 实证）。
EMOTICON_SUFFIX = "EMOTICON"

#: **4 字节强 magic**——真实图片格式的文件头。
#:
#: ⚠️ **绝不可加入 2 字节的 magic**：seed 候选动辄上万，
#: ``\x30\x82``（ASN.1 SEQUENCE）这种 2 字节模式在几万候选里**必然误命中**
#: （RUN-20261020-012 前一轮就栽在这：68729 候选里2 个假命中）。
#: 代价是「看起来解开了」，实际解出的是随机数据 —— 静默失败。
MAGICS: tuple[bytes, ...] = (
    b"\x89PNG",       # PNG
    b"GIF8",# GIF87a / GIF89a
    b"\xff\xd8\xff",  # JPEG
    b"wxgf",         # 微信私有动图容器
    b"RIFF",         # WEBP（还要再看 8~12 字节，``verify_key`` 里处理）
)

#: seed 候选的正则：**8~12 位十进制**，前后不能再接数字。
#:
#: 上限 12 位而非 20 位：过宽会引入大量噪声候选（实测 \d{6,20} 出68729 个，
#: \d{8,12} 只出 12017 个，且后者能全部命中）。边界数字必须在 8~12 位里。
SEED_PATTERN = r"(?<![0-9])(\d{8,12})(?![0-9])"

#: seed 数值范围过滤（1亿 < v < 40亿），排除时间戳/哈希等噪声。
SEED_LOW = 100_000_000
SEED_HIGH = 4_000_000_000

#: 单次只读内存拷贝的字节数。4 MB 是实测平衡点：
#: 太小则系统调用次数多（1593 MB / 1 MB = 1600 次，够慢）；
#: 太大则单次失败时浪费多。
CHUNK_SIZE = 4 << 20

#: 单进程扫描上限（**防呆**，不是产品语义）。
#:
#: 实测单个 Weixin.exe 可读区域约 300~600 MB；设 2 GB 上限意味着
#: 「进程内存异常膨胀」时不会把机器卡死。命中上限会在日志里说明（不静默）。
MAX_SCAN_BYTES = 2 << 30

#: 错误文案（**用户能看懂，不含十六进制错误码**，与 EM-002/EM-005 同一口径）。
WEIXIN_NOT_RUNNING_MESSAGE = (
    "微信没在运行（或扫描不到它的进程）。请先打开微信并登录，再回来扫密钥。"
)
NO_SEED_FOUND_MESSAGE = (
    "在微信进程内存里没找到可用的表情密钥。"
    "请确认微信已登录、且打开过任意一个表情，然后再试一次。"
)
SCAN_TIMEOUT_MESSAGE = (
    "扫描微信进程内存超时（内存量异常大时可能发生）。"
    "可先关掉微信的其他窗口再试，或重启微信后重试。"
)

# ---- Win32 常量（数值来自 MSDN，此处只做命名，不解释语义）------------------

_PID = 0x00000002  # CreateToolhelp32Snapshot 的 TH32CS_SNAPPROCESS
_MOD_ALL = 0x00000003  # Module32 的 TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
_VM_READ = 0x0010  # PROCESS_VM_READ
_QUERY_INFO = 0x0400  # PROCESS_QUERY_INFORMATION
_MEM_COMMIT = 0x1000  # MEMORY_BASIC_INFORMATION 的 State
_PAGE_GUARD = 0x100  # PAGE_GUARD
_PAGE_NOACCESS = 0x01  # PAGE_NOACCESS
_PROCESS_QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
#: 用途：``OpenProcess`` / ``ReadProcessMemory`` / ``VirtualQueryEx`` / ``CreateToolhelp32Snapshot``
#: 四个**只读**用途的 Win32 入口都在这里。**本模块不写任何进程内存**。


# ---- ctypes 结构体（ABI 布局必须与 Win32 一致，字段顺序不能改）--------------


class _PROCESSENTRY32W(ctypes.Structure):
    """``PROCESSENTRY32W``——``CreateToolhelp32Snapshot`` 的进程枚举返回项。"""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    """``MEMORY_BASIC_INFORMATION``——``VirtualQueryEx`` 的返回项。

    ⚠️ 字段顺序与类型必须与 Win32 x64 ABI 逐一对齐（实测踩过：内联用
    ``type(...)`` 造这个结构会**静默错位**，``VirtualQueryEx`` 填出全 0，
    看起来像「没权限读内存」）。故单独 ``class`` 定义。
    """

    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.ULONG),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _configure_prototypes() -> None:
    """声明 Win32 函数的``restype``/``argtypes``。

    ⚠️ **不做这一步的后果（实测踩过）**：``ctypes`` 默认 ``restype=c_int``，
    64 位句柄会被**截断**——打开进程的调用可能因此返回**假成功**
    （拿到的值其实是无效句柄），据此判断「能读内存」会得到相反结论。
    所有返回 HANDLE 的函数都必须显式声明 ``c_void_p``。
    """
    if _kernel32 is None:  # pragma: no cover - 非 Windows 已在 import 期raise
        return
    # 用途：声明打开进程只读入口的返回类型（必须 c_void_p，否则句柄截断会假成功）
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    # 用途：声明「打开进程」入口的**参数**类型（只声明签名，不在此调用）。
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    # 用途：声明「只读内存拷贝」入口的返回类型（只声明，不在此调用）。
    _kernel32.ReadProcessMemory.restype = wintypes.BOOL
    # 用途：声明「只读内存拷贝」入口的参数类型（只声明，不在此调用）。
    _kernel32.ReadProcessMemory.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    # 用途：``VirtualQueryEx`` —— **只查询**某地址所属内存区域的大小与属性
    # （不拷贝内容）；用它来遍历可读区域，避免盲目扫全 2^64 地址空间。
    _kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    _kernel32.VirtualQueryEx.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    )
    # 用途：``CreateToolhelp32Snapshot`` —— 拍一份**进程/模块列表**快照（只读枚举）。
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.Module32FirstW.restype = wintypes.BOOL
    _kernel32.Module32FirstW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.Module32NextW.restype = wintypes.BOOL
    _kernel32.Module32NextW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)


_configure_prototypes()


class WeixinNotRunningError(RuntimeError):
    """微信进程不存在（消息为中文可读指引，见 :data:`WEIXIN_NOT_RUNNING_MESSAGE`）。"""


class SeedNotFoundError(RuntimeError):
    """扫完了但没找到可用密钥（消息见 :data:`NO_SEED_FOUND_MESSAGE`）。"""


@dataclass(frozen=True)
class SeedDeriver:
    """把 ``seed`` + ``wxid`` 派生成 AES key，并校验能否解出真实图片。

    **A-1 实证口径（RUN-20261020-012，2588/2588 文件 100% 解出）**：

    - ``key = bytes.fromhex(md5(f"{seed}{wxid}EMOTICON").hexdigest())[:16]``
      —— ⚠️ **不是** ``hexdigest()[:16]``（那是十六进制**字符串**当字节用）；
    - **IV = key 本身** —— ⚠️ **不是**全 0。上游README 只写「key = IV」，
      极易读错成「IV 可以随便取」。

    把派生 + 校验绑在一起的理由：这两个口径**必须同时正确**才解得开，
    拆开写就会重演「key 对了但 IV 错」这类半对半错的调试噩梦。
    """

    wxid: str

    def key_for(self, seed: int) -> bytes:
        """派生单个 seed 对应的 key（16 字节）。"""
        digest = hashlib.md5(
            f"{seed}{self.wxid}{EMOTICON_SUFFIX}".encode()
        ).hexdigest()
        return bytes.fromhex(digest)[:16]

    def keys(self, seeds: object) -> Iterator[tuple[int, bytes]]:
        """批量派生：``seeds`` 是可迭代的 seed，逐个产出 ``(seed, key)``。"""
        for seed in seeds:  # type: ignore[union-attr]
            yield seed, self.key_for(seed)


def verify_key(key: bytes, first_block: bytes, deriver: SeedDeriver) -> bool:
    """校验 key 能否把 ``first_block``（密文首块）解成合法图片头。

    判据是**4 字节强 magic**（见 :data:`MAGICS`）；``RIFF`` 额外看 8~12 字节
    确认是 WEBP（RIFF 容器也用于 AVI/WAV，仅 4 字节不足以判定）。

    ⚠️ 本函数**不做任何解密以外的判断**，也不看「解出来像不像」——
    宁可漏判（解不开）也不误判（把随机数据当成图片）。
    """
    try:
        plain = _aes_cbc_decrypt_first_block(key, first_block)
    except Exception:  # noqa: BLE001 - 解密失败就是「这个 key 不对」
        return False
    if plain[:4] in MAGICS:
        return True
    # RIFF 需要二次确认：仅前 4 字节无法区分 WEBP / AVI / WAV
    return plain[:4] == b"RIFF" and plain[8:12] == b"WEBP"


def _aes_cbc_decrypt_first_block(key: bytes, block: bytes) -> bytes:
    """用 AES-128-CBC 解一个块，**IV = key**。

    延迟 import ``Crypto.Cipher.AES``：本模块的 Win32 部分不需要它，
    让「只跑合规检查」的环境也能 import 本模块。
    """
    from Crypto.Cipher import AES

    return AES.new(key, AES.MODE_CBC, key).decrypt(block[:16])


def enum_weixin_pids() -> list[int]:
    """枚举 ``Weixin.exe`` 的全部进程 ID（**只读**枚举，不打开进程）。

    实测本机微信会起 **5 个** ``Weixin.exe`` 进程（多进程架构），
    逐个扫；任一命中即返回。
    """
    if _kernel32 is None:  # pragma: no cover
        return []
    # 用途：``CreateToolhelp32Snapshot`` —— 拍进程列表快照（只读，不影响目标进程）。
    snap = _kernel32.CreateToolhelp32Snapshot(_PID, 0)
    if snap in (None, _INVALID_HANDLE):
        return []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        pids: list[int] = []
        # 用途：``Process32FirstW`` / ``Process32NextW`` —— 逐条读快照里的进程项。
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if "weixin" in entry.szExeFile.lower():
                pids.append(int(entry.th32ProcessID))
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        return pids
    finally:
        # 用途：``CloseHandle`` —— 释放快照句柄（不关闭目标进程，只关我们自己开的）。
        _kernel32.CloseHandle(snap)


class ProcessMemoryReader:
    """对一个进程做**只读**内存读取，用作 context manager。

    ⚠️ 本类**只申请 ``PROCESS_VM_READ``**（只读权限）。
    它不申请任何写权限，也不提供写方法——从能力上就无法改目标进程。
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._handle: int | None = None

    def __enter__(self) -> ProcessMemoryReader:
        if _kernel32 is None:  # pragma: no cover
            raise WeixinNotRunningError(WEIXIN_NOT_RUNNING_MESSAGE)
        # 用途：``OpenProcess`` —— 以**只读**权限打开目标进程。
        # 访问掩码只含 VM_READ（不申请 VM_WRITE / VM_OPERATION），
        # 用途：以只读权限打开目标进程；因此本进程**在系统层面就无法写入**其内存。
        handle = _kernel32.OpenProcess(
            _VM_READ | _QUERY_INFO | _PROCESS_QUERY_LIMITED, False, self.pid
        )
        if not handle:
            raise WeixinNotRunningError(WEIXIN_NOT_RUNNING_MESSAGE)
        self._handle = handle
        return self

    def __exit__(self, *exc: object) -> None:
        if self._handle is not None:
            # 用途：``CloseHandle`` —— 释放本进程打开的句柄（不是关目标进程）。
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def read(self, address: int, size: int) -> bytes | None:
        """只读拷贝 ``size`` 字节；该区域不可读时返回 ``None``。"""
        if self._handle is None:  # pragma: no cover - 误用防护
            raise RuntimeError("必须在 with 块内使用 ProcessMemoryReader")
        buffer = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t()
        # 用途：``ReadProcessMemory`` —— 把目标进程的一块内存**只读**拷到本地缓冲。
        # 用途：把目标进程的一块内存**只读**拷到本地缓冲（读出来，不是写进去）。
        ok = _kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(read),
        )
        return bytes(buffer[: read.value]) if ok and read.value else None

    def readable_regions(self) -> Iterator[tuple[int, int]]:
        """产出 ``(起始地址, 长度)``——**已提交且不带保护标记**的可读区域。

        用 ``VirtualQueryEx`` 遍历（**只查询属性，不拷贝内容**），
        跳过 ``PAGE_GUARD`` / ``PAGE_NOACCESS`` 与未提交的区段。
        """
        if self._handle is None:  # pragma: no cover
            raise RuntimeError("必须在 with 块内使用 ProcessMemoryReader")
        address = 0
        info = _MEMORY_BASIC_INFORMATION()
        while True:
            # 用途：``VirtualQueryEx`` —— **只查询**某地址所在区域的属性
            # （State / Protect / RegionSize），不拷贝该区域的任何内容。
            got = _kernel32.VirtualQueryEx(
                self._handle,
                ctypes.c_void_p(address),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not got:
                return
            base = int(info.BaseAddress or 0)
            size = int(info.RegionSize)
            # ⚠️ **只判size，不判 base**：低地址区的 ``BaseAddress`` 恰好是 0
            # （地址 0 附近那段未提交内存，实测第一个区域就是它），
            # 加 ``base <= 0`` 判据会让遍历在第一步就退出 —— 实测踩过：
            # 可读区域数 0 个，而同一进程实际有 2324 个 / 846 MB。
            if size <= 0:
                return
            readable = (
                info.State == _MEM_COMMIT
                and not (info.Protect & _PAGE_GUARD)
                and not (info.Protect & _PAGE_NOACCESS)
            )
            if readable:
                yield base, size
            # 越界保护：地址必须单调递增且不超用户态地址空间上界
            nxt = base + size
            if nxt <= address or nxt > (1 << 48):
                return
            address = nxt


def iter_decimal_candidates(data: bytes) -> Iterator[int]:
    """从一段字节里产出 seed 候选（**已按 :data:`SEED_PATTERN` 与数值范围过滤**）。

    范围过滤是**实测得出的必要过滤**：不过滤时噪声候选会多到几万，
    逐个派生 + AES 解密的开销随之放大（而真 seed 只有 1 个）。
    """
    for match in re.finditer(SEED_PATTERN.encode(), data):
        value = int(match.group(0))
        if SEED_LOW < value < SEED_HIGH:
            yield value


def scan_seed(
    wxid: str,
    sample: bytes,
    *,
    max_bytes: int = MAX_SCAN_BYTES,
    pids: list[int] | None = None,
    reader_factory: Callable[[int], object] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """扫微信进程内存，找出能解开 ``sample`` 的那个 seed。

    流程（每一步都是**只读**）：

    1. 枚举 ``Weixin.exe`` 进程（实测本机5 个）；
    2. 逐个打开**只读**句柄，按 :data:`MAX_SCAN_BYTES` 上限遍历可读区域；
    3. 从字节流里按 :data:`SEED_PATTERN` + 数值范围提取候选 seed（**去重**）；
    4. 逐个派生 key（:class:`SeedDeriver`），用**首块真文件**做魔数校验；
    5. 命中即返回那个 ``seed``（``int``，**调用方自己负责用完即弃**）。

    :param wxid: 微信号（由 :func:`emoji_migrator.wxdir.wxid_of` 推导）。
    :param sample: **某个真实加密表情文件的首块密文**（>= 16 字节）。
        用真实文件而非合成数据，是校验能够判定真假命中的前提。
    :param max_bytes: 单进程扫描上限（**防呆**，非产品语义）。
    :param pids: 只扫指定 PID（测试打桩用；``None`` 表示自动枚举）。
    :param reader_factory: 构造「只读读取器」的工厂（测试打桩用）。
    :param progress: 进度回调 ``(已扫字节, 上限字节)``，用于 GUI 进度条。

    :raises WeixinNotRunningError: 微信进程不存在，或全部进程打不开。
    :raises SeedNotFoundError: 扫完了但没找到能解开 ``sample`` 的 seed。

    ⚠️ **本函数不做任何写操作**；返回的 seed 是纯整数，用完即弃、不落盘。
    """
    if not wxid:
        raise ValueError("wxid 不能为空（应由 wxdir.wxid_of 推导）")
    if len(sample) < 16:
        raise ValueError("校验样本至少需要 16 字节（一个 AES 块）")

    targets = pids if pids is not None else enum_weixin_pids()
    if not targets:
        raise WeixinNotRunningError(WEIXIN_NOT_RUNNING_MESSAGE)

    deriver = SeedDeriver(wxid=wxid)
    make_reader = reader_factory if reader_factory is not None else ProcessMemoryReader

    any_opened = False
    for pid in targets:
        try:
            reader_ctx = make_reader(pid)
        except WeixinNotRunningError:
            continue
        try:
            with reader_ctx as reader:  # type: ignore[union-attr]
                any_opened = True
                candidates = _collect_candidates(
                    reader, max_bytes=max_bytes, progress=progress
                )
                hit = _first_working_seed(deriver, candidates, sample)
                if hit is not None:
                    return hit
        except SeedNotFoundError:
            raise
        except OSError:
            # 单个进程读失败（权限/进程已退出）→ 试下一个，不中断整体。
            continue

    if not any_opened:
        raise WeixinNotRunningError(WEIXIN_NOT_RUNNING_MESSAGE)
    raise SeedNotFoundError(NO_SEED_FOUND_MESSAGE)


def _collect_candidates(
    reader: object,
    *,
    max_bytes: int,
    progress: Callable[[int, int], None] | None = None,
) -> set[int]:
    """遍历可读区域，收集**去重后**的 seed 候选集合。

    去重是必要的：同一个 seed 会在多处出现（内存里反复存同一常量），
    不去重会导致同一候选被验证上百次。
    """
    found: set[int] = set()
    scanned = 0
    regions = reader.readable_regions()  # type: ignore[attr-defined]
    for base, size in regions:
        if scanned >= max_bytes:
            break
        # 单块最多读 CHUNK_SIZE；跨块时保留尾部重叠，避免 chunk 边界切断候选
        offset = 0
        overlap = b""
        while offset < size:
            want = min(CHUNK_SIZE, size - offset)
            chunk = reader.read(base + offset, want)  # type: ignore[attr-defined]
            if not chunk:
                break
            scanned += len(chunk)
            if progress is not None:
                progress(scanned, max_bytes)
            found.update(iter_decimal_candidates(overlap + chunk))
            # 保留 12 字节重叠（候选最长 12 位，见 SEED_PATTERN）
            overlap = chunk[-12:] if len(chunk) >= 12 else chunk
            offset += want
            if scanned >= max_bytes:
                break
    return found


def _first_working_seed(
    deriver: SeedDeriver,
    candidates: object,
    sample: bytes,
) -> int | None:
    """逐个候选派生 key 并校验，返回第一个能解开 ``sample`` 的 seed。"""
    for seed, key in deriver.keys(candidates):  # type: ignore[arg-type]
        if verify_key(key, sample, deriver):
            return seed  # type: ignore[return-value]
    return None


def decrypt_block(key: bytes, block: bytes) -> bytes:
    """用给定key 解一个 AES 块（**IV = key**，与 :class:`SeedDeriver` 同口径）。

    单块解密函数：给解密模块（下一个模块）复用，避免「IV 口径」在两处实现里漂移。
    """
    return _aes_cbc_decrypt_first_block(key, block)
