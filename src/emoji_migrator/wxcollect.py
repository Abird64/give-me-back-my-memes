"""微信表情导出的编排层（EM-003）：扫密钥 → 解密 → wxgf 转码 → 落盘。

**编排层**（``AGENTS.md`` §3）：import 四个叶子模块（``wxdir`` / ``wxscan`` /
``wxdecrypt`` / ``webp_gif``），自己不实现算法。与 :mod:`emoji_migrator.collect_flow`
（抖音侧的编排层）**对称**，故可作对照。

**只读边界**：本模块读微信的数据目录与进程内存，**只写自己的输出目录**——
绝不写回微信的任何文件（EM-003 的「密钥不落盘」与「不碰用户原文件」两条硬约束）。

**wxgf 转码**：微信私有容器（4 字节 magic + 裸 H.265 流），微信与QQ 都不认，
必须转成 GIF 才能用。转码交给 :mod:`emoji_migrator.webp_gif` 之外的独立路径
（ffmpeg / ``imageio-ffmpeg``），因为 wxgf 是 **H.265** 而 webp_gif 是WebP 入口。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from emoji_migrator import wxdecrypt, wxdir, wxscan

__all__ = [
    "WxCollectOutcome",
    "WxError",
    "collect_weixin_emoticons",
    "convert_wxgf_to_gif",
]


#: Windows 的 ``CREATE_NO_WINDOW`` 进程创建标志（``0x08000000``）。
#:
#: **用途**：让子进程（ffmpeg）**不分配新控制台**。
#: 打包后的 exe 是 windowed（``console=False``，本进程无控制台），
#: 不传这个标志时 Windows 会给每个子进程分配一个控制台 ⇒
#: 每转一个动图弹一次黑框（974 个 wxgf = 弹 974 次）。
#: 实测见 :func:`convert_wxgf_to_gif` 的 docstring。
#:
#: 非 Windows 平台**没有这个标志**，用 0（等价于「不指定」）。
_CREATE_NO_WINDOW: int = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
)


class WxError(RuntimeError):
    """微信导出流程的可读失败（消息为中文，**不含路径与十六进制错误码**）。"""


@dataclass
class WxCollectOutcome:
    """一次微信导出的结果统计（供 CLI 打印 / GUI 显示）。"""

    #: 解密成功的单文件表情数（不含容器切片）
    single_files: int = 0
    #: 从容器整包里切出的表情数
    container_slices: int = 0
    #: wxgf 转 GIF 成功数
    wxgf_converted: int = 0
    #: wxgf 转码失败数
    wxgf_failed: int = 0
    #: 被EM-002 守门拦下的数量（格式不认识等）
    rejected: int = 0
    #: 逐条失败明细 ``(来源文件名, 原因)``——原因不含路径与密钥
    failures: list[tuple[str, str]] = field(default_factory=list)
    #: 实际写出的文件总数
    written: int = 0
    #: 输出目录
    out_dir: Path | None = None
    #: 扫内存耗时（秒）——供 GUI 显示「为什么要等」
    scan_seconds: float = 0.0

    @property
    def total(self) -> int:
        """本轮产出的表情总数（单文件 + 容器切片）。"""
        return self.single_files + self.container_slices


def convert_wxgf_to_gif(payload: bytes, target: Path, retries: int = 1) -> None:
    """把 wxgf（4 字节 magic + 裸 H.265）转成 GIF 写入 ``target``。

    实测（RUN-20261020-012）：5 个样本 → 5 个成功，**4 个是真动图**
    （35 / 31 / 93 / 4 帧）——即**保留了完整动图而非静态首帧**。

    用 ``imageio-ffmpeg`` 自带的 ffmpeg（EM-006 已随包），不要求系统安装。

    ⚠️ **带重试**（``retries`` 默认 1，即最多试 2 次）：
    2026-10-20 批量实测 1009 个 wxgf 里**35 个首次转码失败**（退出码是
    ``3199971767`` 这种非正常值），但**同一个文件单独重跑就成功**（实测：
    退出码 0、35 帧）⇒ 是批量高负载下的偶发失败，不是文件本身有问题。
    不重试的话这 35 个表情就白丢了，而它们占 wxgf 总数的 3.5%。

    转码失败抛 :class:`WxError`，**不产出半个文件**（先写临时文件、成功才改名）。

    ⚠️ **必须传 ``creationflags=CREATE_NO_WINDOW``**（2026-10-21 实测修）：
    打包后的 exe 是 ``console=False``（windowed，**本进程没有控制台**，
    实测 ``GetConsoleWindow() == 0``）。Windows 上这类程序启动子进程时，
    若不指定该 flag，子进程会被**分配一个新的控制台** ⇒ **每调一次 ffmpeg
    就弹一个黑框，进程退出即消失**。用户实测症状是「反复弹命令行黑窗口
    然后秒关，持续很久」——974 个 wxgf 各调一次 ffmpeg = **弹 974 次**。
    实测对照（同一份 windowed 探针 exe，各跑 5 次）：

    | 模式 | 观察到的 ffmpeg 窗口数 |
    | --- | --- |
    | 不传 flag | **5 / 5**（每次都弹） |
    | 传 ``CREATE_NO_WINDOW`` | **0 / 5** |

    该 flag 在**非 Windows 平台不存在**，故按平台取常量（Windows 用
    :data:`_CREATE_NO_WINDOW`，其他平台用 0）。
    """
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise WxError("缺少 ffmpeg 组件，无法转换微信动图") from exc

    body = payload[4:]  # 去掉 4 字节 magic
    last_error = ""
    with tempfile.TemporaryDirectory(prefix="wxgf-") as tmp:
        raw = Path(tmp) / "in.h265"
        out = Path(tmp) / "out.gif"
        raw.write_bytes(body)
        for _attempt in range(retries + 1):
            try:
                proc = subprocess.run(  # noqa: S603 - 固定可执行文件，无 shell
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-y", "-loglevel", "error",
                        "-i", str(raw), "-f", "gif", str(out),
                    ],
                    capture_output=True,
                    timeout=90,
                    check=False,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except subprocess.TimeoutExpired:
                # ⚠️ **必须转成 WxError**：调用方只 catch ``WxError``，
                # ``TimeoutExpired``（``SubprocessError`` 子类）会穿透它
                # ⇒ **整轮导出崩掉**，而不是「这个表情失败、其余继续」。
                # 实测踩过：机器负载高时单个 wxgf 转码超 90 秒。
                last_error = "动图转码超时（文件过大或系统繁忙，可重试本命令）"
                continue
            except OSError as exc:
                raise WxError("动图转码失败（ffmpeg 无法运行）") from exc
            if proc.returncode == 0 and out.is_file():
                target.write_bytes(out.read_bytes())
                return
            # 只保留退出码，不转发 stderr（可能含临时路径）
            last_error = f"动图转码失败（ffmpeg 退出码 {proc.returncode}）"
    raise WxError(last_error)


def _write_one(data: bytes, fmt: str, target_dir: Path, stem: str) -> Path:
    """把一个已解密的表情写盘（wxgf 走转码，其余直写）。

    写入策略：**先写临时文件、成功后改名**——避免转码/写入中途失败留下半个文件
    （半个 GIF 在微信里表现为「表情损坏」，用户看不出是导出出了问题）。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = wxdecrypt.target_suffix_for(fmt)
    target = target_dir / f"{stem}{suffix}"
    if fmt == wxdecrypt.FORMAT_WXGF:
        with tempfile.NamedTemporaryFile(
            delete=False, dir=str(target_dir), suffix=".part"
        ) as handle:
            tmp = Path(handle.name)
        try:
            convert_wxgf_to_gif(data, tmp)
            tmp.replace(target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return target
    # 非wxgf：直接写（内容已在内存里，无需转码）
    with tempfile.NamedTemporaryFile(
        delete=False, dir=str(target_dir), suffix=".part"
    ) as handle:
        handle.write(data)
        tmp = Path(handle.name)
    tmp.replace(target)
    return target


def collect_weixin_emoticons(
    out_dir: Path,
    emoticon_dir: Path | None = None,
    convert_wxgf: bool = True,
    scan: Callable[[str, bytes], int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> WxCollectOutcome:
    """扫密钥 → 解密全部表情 → wxgf 转 GIF → 落盘到 ``out_dir``。

    :param out_dir: 输出目录（**只会写这里**，绝不写回微信目录）。
    :param emoticon_dir: 表情目录；``None`` 时用 :func:`wxdir` 自动探测。
    :param convert_wxgf: 是否把 wxgf 转成 GIF（``False`` 时只导非 wxgf 部分——
        wxgf 微信/QQ 都不认，不转就没法用，故默认 ``True``）。
    :param scan: 扫 seed 的函数 ``(wxid, sample) -> int``；``None`` 用
        :func:`emoji_migrator.wxscan.scan_seed`（**会读进程内存**，40~90秒）。
    :param progress: 阶段回调（GUI 显示「正在扫内存/正在解密…」）。

    :raises WxError: 探测不到目录 / 微信未运行 / 找不到密钥。
    """
    outcome = WxCollectOutcome(out_dir=Path(out_dir))

    # 1) 定位表情目录
    if emoticon_dir is None:
        try:
            emoticon_dir = wxdir.detect_weixin_emoticon_dir()
        except wxdir.WXDirsNotFoundError as exc:
            raise WxError(wxdir.NOT_FOUND_MESSAGE) from exc
    emoticon_dir = Path(emoticon_dir)
    if not emoticon_dir.is_dir():
        raise WxError("指定的微信表情目录不存在，请重新选择")

    # 2) 算 wxid + 取校验样本
    account_dir = emoticon_dir.parent.parent
    wxid = wxdir.wxid_of(account_dir.name)
    persist = emoticon_dir / wxdir.PERSIST_DIR_NAME
    sample_file = _first_file(persist)
    if sample_file is None:
        raise WxError("微信表情目录里没有可解密的样本文件（是否已登录微信？）")

    # 3) 扫 seed（**唯一读内存的一步**，耗时以十秒计）
    if progress is not None:
        progress("正在扫描微信进程内存寻找密钥（约需 1 分钟，请勿关闭微信）…")
    import time

    t0 = time.perf_counter()
    scan_fn = scan if scan is not None else wxscan.scan_seed
    try:
        sample = sample_file.read_bytes()[:16]
    except OSError as exc:
        raise WxError("微信表情样本暂时无法读取，请保持微信运行后重试") from exc
    try:
        seed = scan_fn(wxid, sample)
    except wxscan.WeixinNotRunningError as exc:
        raise WxError(str(exc)) from exc
    except wxscan.SeedNotFoundError as exc:
        raise WxError(str(exc)) from exc
    outcome.scan_seconds = time.perf_counter() - t0
    key = wxdecrypt.derive_key(seed, wxid)

    # 4) 解密 Persist 单文件
    if progress is not None:
        progress("正在解密微信表情…")
    for path in _iter_files(persist):
        try:
            data = wxdecrypt.decrypt_bytes(key, path.read_bytes())
            fmt = wxdecrypt.detect_format(data)
        except wxdecrypt.DetectError as exc:
            outcome.rejected += 1
            outcome.failures.append((path.name, str(exc)))
            continue
        except OSError as exc:
            outcome.failures.append((path.name, f"读取失败：{exc.strerror}"))
            continue
        if fmt == wxdecrypt.FORMAT_WXGF and not convert_wxgf:
            continue
        try:
            _write_one(data, fmt, Path(out_dir), path.stem)
        except (WxError, OSError) as exc:
            if fmt == wxdecrypt.FORMAT_WXGF:
                outcome.wxgf_failed += 1
            outcome.failures.append((path.name, str(exc) or "写出失败"))
            continue
        if fmt == wxdecrypt.FORMAT_WXGF:
            outcome.wxgf_converted += 1
        outcome.single_files += 1
        outcome.written += 1

    # 5) 切分 PersistStore 整包
    store = emoticon_dir / "PersistStore"
    if store.is_dir():
        if progress is not None:
            progress("正在切分表情整包…")
        for path in _iter_files(store):
            try:
                plain = wxdecrypt.decrypt_bytes(key, path.read_bytes())
            except (wxdecrypt.DetectError, OSError) as exc:
                outcome.failures.append((path.name, f"整包解密失败：{exc}"))
                continue
            for index, piece in enumerate(wxdecrypt.split_container(plain), start=1):
                stem = f"{path.stem}_{index:03d}"
                try:
                    _write_one(piece.data, piece.fmt, Path(out_dir), stem)
                except (WxError, OSError) as exc:
                    outcome.failures.append((path.name, str(exc) or "写出失败"))
                    continue
                outcome.container_slices += 1
                outcome.written += 1

    return outcome


def _first_file(root: Path) -> Path | None:
    """返回 ``root`` 下第一个真实文件（**只认文件**，目录会返回 None）。"""
    if not root.is_dir():
        return None
    for path in sorted(root.rglob("*")):
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _iter_files(root: Path) -> list[Path]:
    """递归列出 ``root`` 下的真实文件（**跳过目录**——微信的表情目录是两层嵌套）。"""
    if not root.is_dir():
        return []
    return [p for p in sorted(root.rglob("*")) if p.is_file()]

