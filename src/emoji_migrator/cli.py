"""命令行入口。

本模块是**唯一允许编排多个业务模块**的地方（见 ``AGENTS.md`` §3 依赖方向）。
它只做编排与呈现，不含算法：格式判定在 :mod:`~emoji_migrator.sniff`，
后缀修正在 :mod:`~emoji_migrator.normalize`，感知哈希去重在 :mod:`~emoji_migrator.dedupe`，
守门规则常量在 :mod:`~emoji_migrator.guard`。

子命令：

- ``compliance``：打印合规基线元信息；
- ``scan``：嗅探目录，只报告，不写任何文件；
- ``normalize``：嗅探 → 后缀修正 → 感知哈希去重 → 正确性守门，供 EM-004 复用；
- ``collect-douyin``：抖音收藏表情 → 下载 → WebP 转 GIF（EM-005-D 新增）。
- ``collect-weixin``：微信表情 → 扫内存取密钥 → 解密 → wxgf 转 GIF（EM-003 新增）。

``normalize`` 额外提供 ``--dedupe/--no-dedupe`` 与 ``--platform``，
后者只影响「超大表情提示」用哪套话术，**不影响后缀修正的目标**
（修正目标恒为 sniff 的真实格式，见 ``AGENTS.md`` 的 K-2 口径）。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from emoji_migrator import __version__, collect_flow
from emoji_migrator.collect_flow import CollectOutcome, collect_douyin_stickers
from emoji_migrator.config import UNKNOWN_FORMAT
from emoji_migrator.dedupe import dedupe
from emoji_migrator.douyin_collect import SessionExpired
from emoji_migrator.guard import (
    ALLOWED_READONLY_APIS,
    ANIMATION_LOSS_WARNING,
    COMPLIANCE_POLICY_ID,
    COMPLIANCE_SOURCE_OF_TRUTH,
    CORRECTNESS_RULES,
    FORBIDDEN_API_COUNT,
    MIN_ANIMATION_FRAMES,
    RULE_ANIMATION_SURVIVED,
    RULE_SUFFIX_MATCHES_FORMAT,
)
from emoji_migrator.normalize import NORMALIZE_BLOCKED_HINT, apply_normalize, plan_normalize
from emoji_migrator.platforms import Platform
from emoji_migrator.sniff import SniffResult, is_image_suffix, sniff

__all__ = [
    "build_parser",
    "main",
    "collect_images",
    "verify_output",
    "Violation",
    "WECHAT_OVERSIZE_HINT",
    "OVERSIZE_EDGE",
    "DOUYIN_NO_COOKIE_HINT",
    "DOUYIN_CORRUPT_HINT",
    "WECHAT_NEEDS_RUNNING_HINT",
]

#: 微信表情的展示边长参考值。**仅用于提示**，不触发任何缩放或重编码（D-13 裁决）。
OVERSIZE_EDGE: int = 240

#: D-13 裁决的固定中文提示文案。
#:
#: ⚠️ 逐字固定，不可改写：验收标准要求 CLI 输出**包含**这段话，
#: 且「注意微信的压缩可能把动图变成静态图」这句是本工具存在的核心理由
#: （本机实测46 个 GIF 在 QQ 自己的缩略图目录里全被压成静态单帧）。
WECHAT_OVERSIZE_HINT: str = (
    "该表情超出 {edge}×{edge}，微信导入时会自行压缩；"
    "注意微信的压缩可能把动图变成静态图"
)

#: ``collect-douyin`` 无登录缓存时的中文提示（EM-005-D）。
#:
#: 分两种情形给不同文案：缓存文件**根本不存在** = 从没登录过；
#: 文件在但解不开（DPAPI 解密失败 / JSON 损坏 / 结构不对）= 缓存坏了。
#: ``douyin_auth.load_cached_cookie`` 对全失败面一律返回 ``None``、不外抛，
#: 所以此处用 ``has_cached_session`` 之外再做一次「文件是否存在」判断来区分。
DOUYIN_NO_COOKIE_HINT: str = (
    "本机没有抖音登录缓存。请先在 GUI 里点「登录抖音」完成扫码，"
    "再回到命令行执行本命令。"
)

#: 缓存文件存在但解不开时的提示。**不含任何路径与十六进制错误码**。
DOUYIN_CORRUPT_HINT: str = (
    "抖音登录缓存存在但无法读取（可能换了 Windows 账户、或缓存已损坏）。"
    "请在 GUI 里点「登录抖音」重新扫码登录。"
)


@dataclass(frozen=True, slots=True)
class Violation:
    """一条正确性违规。"""

    path: Path
    rule: str
    detail: str


def collect_images(root: Path) -> list[Path]:
    """递归收集目录下后缀受支持的图片文件，按路径排序。

    排序是刻意的：输出顺序必须与文件系统遍历顺序无关，
    否则同一份输入在不同机器上会得到不同的报告，测试也会变得不稳定。
    """
    if not root.is_dir():
        raise NotADirectoryError(f"不是目录：{root}")
    paths: list[Path] = []
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file() and is_image_suffix(path):
                    paths.append(path)
            except OSError:
                # 客户端可能正在写入/替换某个文件；跳过它，不让整批扫描失败。
                continue
    except OSError:
        # 目录树在扫描过程中被客户端更新时，保留已经收集到的部分结果。
        pass
    return sorted(paths, key=lambda path: str(path))


def _frame_count(path: Path) -> int:
    """读多帧 GIF/动图的帧数；静态图返回 1；无法解码返回 0。

    兜住 :class:`~PIL.Image.DecompressionBombError`：损坏文件头可能宣称
    荒谬尺寸而触发 Pillow 的解压炸弹防护，答案应是「读不出帧数」。
    """
    try:
        with Image.open(path) as img:
            total = getattr(img, "n_frames", 1)
            # seek 到最后一帧，确认帧数不是元数据里写死的谎报值。
            img.seek(max(total, 1) - 1)
            return max(total, 1)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        Image.DecompressionBombError,
    ):
        return 0


def verify_output(path: Path, was_animated: bool) -> list[Violation]:
    """对**输出**文件执行两条正确性判定。

    规则二（动图存活）只在 ``was_animated`` 为真时要求 ``n_frames > 1``：
    静态图本就该是单帧，若对它也要求多帧，就是把「本该静态」误判成违规。

    :param was_animated: 该文件在**输入**阶段是否就是动图（帧数 > 1）。
    """
    problems: list[Violation] = []

    result = sniff(path)
    if result.mismatch:
        problems.append(
            Violation(
                path=path,
                rule=RULE_SUFFIX_MATCHES_FORMAT,
                detail=f"后缀声称 {result.claimed_format}，真实格式 {result.actual_format}",
            )
        )

    frames = _frame_count(path)
    if was_animated and frames < MIN_ANIMATION_FRAMES:
        problems.append(
            Violation(
                path=path,
                rule=RULE_ANIMATION_SURVIVED,
                detail=f"{ANIMATION_LOSS_WARNING}（实测帧数 {frames}）",
            )
        )

    return problems


def _max_edge(path: Path) -> int:
    """取图片最长边像素；读不出返回 0。"""
    try:
        with Image.open(path) as img:
            return max(img.size)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        return 0


def _scan_dir(root: Path, verbose: bool) -> int:
    """``scan`` 实现：只报告，不写文件。返回退出码。"""
    paths = collect_images(root)
    mismatches = [sniff(p) for p in paths]
    bad = [r for r in mismatches if r.mismatch]
    unreadable = [r for r in mismatches if r.actual_format == UNKNOWN_FORMAT]

    print(f"扫描目录：{root}")
    print(f"受支持图片：{len(mismatches)} 个")
    print(f"后缀错乱：{len(bad)} 个")
    print(f"无法判定格式：{len(unreadable)} 个")

    if verbose:
        for result in mismatches:
            print(f"  {result}")
    else:
        for result in bad:
            print(f"  {result}")

    return 0


def _normalize_dir(source: Path, out_dir: Path, platform: Platform, do_dedupe: bool) -> int:
    """``normalize`` 实现：嗅探 → 后缀修正 →（可选）去重 → 守门。"""
    paths = collect_images(source)
    plans = []
    results: dict[Path, SniffResult] = {}
    taken: set[str] = set()
    blocked: list[Path] = []

    for path in paths:
        result = sniff(path)
        results[path] = result
        if result.actual_format == UNKNOWN_FORMAT:
            blocked.append(path)
            continue
        plan = plan_normalize(result, platform, out_dir, taken=taken)
        taken.add(plan.target.name.casefold())
        plans.append(plan)

    for plan in plans:
        apply_normalize(plan)

    renamed = [p for p in plans if p.needs_rename]
    print(f"输入目录：{source}")
    print(f"输出目录：{out_dir}")
    print(f"受支持图片：{len(paths)} 个")
    print(f"后缀修正：{len(renamed)} 个")
    print(f"跳过：{len(blocked)} 个")
    for path in blocked:
        print(f"  {NORMALIZE_BLOCKED_HINT}{path}")

    # D-13：只给中文提示，不缩放、不重编码、不阻断产出。
    oversize = [p for p in plans if _max_edge(p.target) > OVERSIZE_EDGE]
    if oversize:
        print(f"超出 {OVERSIZE_EDGE}×{OVERSIZE_EDGE} 的表情 {len(oversize)} 个：")
        print("  " + WECHAT_OVERSIZE_HINT.format(edge=OVERSIZE_EDGE))
        for plan in oversize:
            print(f"    {plan.target.name}（{_max_edge(plan.target)} 像素）")

    if do_dedupe:
        report = dedupe([p.target for p in plans])
        print(f"去重前：{len(plans)} 个")
        print(f"去重后：{report.unique_count} 个（移除重复 {report.removed_count} 个）")
        for group in report.groups:
            dupes = "、".join(d.name for d in group.duplicates)
            print(f"  保留 {group.keeper.name}；合并重复：{dupes}")
        if report.skipped:
            print(f"  跳过（无法计算感知哈希）：{len(report.skipped)} 个")

    violations: list[Violation] = []
    for plan in plans:
        was_animated = _frame_count(plan.source) > 1
        violations.extend(verify_output(plan.target, was_animated))

    print(f"正确性守门（{len(CORRECTNESS_RULES)} 条）：{'通过' if not violations else '不通过'}")
    for problem in violations:
        print(f"  [{problem.rule}] {problem.path.name}：{problem.detail}")

    return 1 if violations else 0


WECHAT_NEEDS_RUNNING_HINT: str = (
    "微信表情导出需要**微信在运行**（要从它的进程内存里读密钥）。"
    "请先打开微信并登录，再执行本命令。"
)


def _collect_weixin(out_dir: Path, skip_wxgf: bool) -> int:
    """``collect-weixin`` 实现：扫内存取密钥 → 解密 → wxgf 转 GIF → 落盘。

    退出码口径与 :func:`_collect_douyin` 对齐：**0** = 干净完成，
    **1** = 有失败，**2** = 前置条件不满足（微信没运行 / 找不到表情目录）。

    与抖音侧完全对称：编排都在 :mod:`~emoji_migrator.wxcollect` 里，
    本函数只做「调编排 → 打印计数」。
    """
    # 延迟 import：本模块 import 期不碰 ctypes 与进程内存，
    # 让「只跑 compliance/scan」的命令不受这些依赖影响。
    from emoji_migrator import wxcollect

    def show(message: str) -> None:
        print(message, flush=True)

    try:
        outcome = wxcollect.collect_weixin_emoticons(
            out_dir, convert_wxgf=not skip_wxgf, progress=show
        )
    except wxcollect.WxError as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        return 2

    print(f"输出目录：{outcome.out_dir}")
    print(f"扫内存耗时：{outcome.scan_seconds:.1f} 秒")
    print(f"单文件表情：{outcome.single_files} 个")
    print(f"整包切出：{outcome.container_slices} 个")
    if outcome.wxgf_converted:
        print(f"微信动图转 GIF：{outcome.wxgf_converted} 个")
    if outcome.wxgf_failed:
        print(f"动图转码失败：{outcome.wxgf_failed} 个（原始文件仍在微信里，可重试本命令）")
    print(f"写出合计：{outcome.written} 个")
    if outcome.rejected:
        print(f"无法识别格式：{outcome.rejected} 个")
    for name, reason in outcome.failures[:10]:
        print(f"  [失败] {name}：{reason}")
    if len(outcome.failures) > 10:
        print(f"  ……另有 {len(outcome.failures) - 10} 条失败未列出")
    return 0 if not outcome.failures else 1


def _collect_douyin(out_dir: Path, max_pages: int) -> int:
    """``collect-douyin`` 实现：读缓存 cookie → 采集 → 下载 → WebP 转 GIF。

    退出码口径与 ``normalize`` 对齐：**0** = 干净完成，**1** = 有失败或什么都没拿到，
    **2** = 前置条件不满足（无 cookie / 缓存损坏）。

    为什么编排放在 :mod:`~emoji_migrator.collect_flow` 而不在这里：GUI 也要用同一套，
    写在这里会让 gui 反向依赖 cli。本函数只做「取cookie → 调编排 → 打印计数」。
    """
    # 延迟 import：本模块 import 期不碰 cookie 路径与 DPAPI，
    # 让「只跑 compliance/scan」的命令不受这些依赖影响。
    from emoji_migrator import douyin_auth

    cookies = douyin_auth.load_cached_cookie()
    if cookies is None:
        try:
            cached_file_exists = douyin_auth.cookie_cache_path().exists()
        except OSError:
            cached_file_exists = False
        hint = DOUYIN_CORRUPT_HINT if cached_file_exists else DOUYIN_NO_COOKIE_HINT
        print(hint, file=sys.stderr)
        return 2

    try:
        outcome = collect_douyin_stickers(cookies, out_dir, max_pages=max_pages)
    except SessionExpired:
        print("登录态已过期（服务端 401），请重新扫码登录抖音", file=sys.stderr)
        return 2
    except collect_flow.CollectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    _report_collect(outcome, out_dir)
    return 0 if outcome.ok else 1


def _report_collect(outcome: CollectOutcome, out_dir: Path) -> None:
    """打印采集计数。口径写在 ``AGENTS.md`` 的验收标准里，逐项对应。"""
    print(f"输出目录：{out_dir}")
    print(f"翻页数：{outcome.pages} 页")
    print(f"列表条目：{outcome.listed} 个（跨页重复 {outcome.duplicates} 个）")
    print(f"下载成功：{outcome.downloaded} 个")
    print(f"转换成功：{outcome.converted} 个（WebP → GIF）")
    print(f"产出合计：{outcome.total_collected} 个")
    if outcome.convert_failed:
        print(f"转换失败：{outcome.convert_failed} 个（原始 WebP 保留在 {outcome.raw_dir}）")
    if outcome.failed:
        print(f"下载失败：{outcome.failed} 个")
    if outcome.hit_page_limit:
        print(collect_flow.PAGE_LIMIT_HINT)
    for sticker_id, stage, reason in outcome.failures:
        print(f"  [{stage}失败] {sticker_id}：{reason}")
    if not outcome.listed:
        print("抖音收藏里没有表情（可能是空收藏页）。")


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="emoji-migrator",
        description="跨平台表情格式嗅探、后缀修正与感知哈希去重",
    )
    parser.add_argument("--version", action="version", version=f"emoji-migrator {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("compliance", help="打印合规基线元信息")

    scan_parser = sub.add_parser("scan", help="嗅探并列出后缀与真实格式不一致的文件（只读）")
    scan_parser.add_argument("src", type=Path, help="待扫描目录")
    scan_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="列出全部文件，而非只列出不一致的",
    )

    norm_parser = sub.add_parser("normalize", help="嗅探 → 后缀修正 → 去重 → 正确性守门")
    norm_parser.add_argument("src", type=Path, help="输入目录")
    norm_parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("_out/normalized"),
        help="输出目录（默认 _out/normalized）",
    )
    norm_parser.add_argument(
        "-p",
        "--platform",
        type=Platform,
        choices=list(Platform),
        default=Platform.WECHAT,
        help="目标平台（只影响提示话术，不影响后缀修正目标；默认 wechat）",
    )
    dedupe_group = norm_parser.add_mutually_exclusive_group()
    dedupe_group.add_argument(
        "--dedupe",
        dest="dedupe",
        action="store_true",
        default=True,
        help="执行感知哈希去重（默认开启）",
    )
    dedupe_group.add_argument(
        "--no-dedupe",
        dest="dedupe",
        action="store_false",
        help="跳过去重",
    )

    collect_parser = sub.add_parser(
        "collect-douyin",
        help="采集抖音收藏表情并转成 GIF（需先在 GUI 登录抖音）",
    )
    collect_parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("_out/douyin"),
        help="输出目录（默认 _out/douyin）",
    )
    collect_parser.add_argument(
        "--max-pages",
        type=int,
        default=collect_flow.MAX_PAGES,
        help=f"翻页硬上限（默认 {collect_flow.MAX_PAGES}，仅作死循环防呆）",
    )

    wx_parser = sub.add_parser(
        "collect-weixin",
        help="解密微信表情并转成 GIF（需微信在运行）",
    )
    wx_parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("_out/weixin"),
        help="输出目录（默认 _out/weixin）",
    )
    wx_parser.add_argument(
        "--skip-wxgf",
        action="store_true",
        help="跳过微信私有动图（wxgf）——它们需要转码，且耗时占大头",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "compliance":
        print(f"policy: {COMPLIANCE_POLICY_ID}")
        print(f"forbidden api count: {FORBIDDEN_API_COUNT}")
        print(f"authoritative list: {COMPLIANCE_SOURCE_OF_TRUTH}")
        print("allowed (read-only, 需注释说明用途):")
        for name in ALLOWED_READONLY_APIS:
            print(f"  - {name}")
        return 0

    if args.command == "scan":
        try:
            return _scan_dir(args.src, args.verbose)
        except NotADirectoryError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.command == "normalize":
        try:
            return _normalize_dir(args.src, args.out, args.platform, args.dedupe)
        except NotADirectoryError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.command == "collect-douyin":
        return _collect_douyin(args.out, args.max_pages)

    if args.command == "collect-weixin":
        return _collect_weixin(args.out, args.skip_wxgf)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
