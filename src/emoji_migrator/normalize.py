"""格式后缀修正。

职责：把嗅探结果（:mod:`emoji_migrator.sniff`）转成「后缀与真实格式一致」的文件。
本轮（EM-002）**只做后缀修正**：不改像素、不缩放、不重编码。

## 为什么只改名就够了

本项目 2026-10-13 人工裁决**不做压缩**（见工作包「不包含」节），
且 D-13 裁决微信超大表情「只提示、不缩放、不重编码」。于是修正动作退化为
「把文件放到输出目录、并用真实格式的后缀命名」，而这恰好是**最强**的一种保证：

    逐字节复制 ⇒ 输出与输入的像素、帧数、时长逐位相同

所以验收标准「49 个动图输出后仍 ``n_frames > 1``」不依赖任何压缩策略正确，
而是**结构上不可能失败**——没有解码-重编码环节，就没有丢帧的机会。
这也是 D-13「不阻断产出」得以成立的原因。

## 修正目标是什么（K-2 口径）

目标是 **sniff 判出的真实格式**（:data:`~emoji_migrator.config.FORMAT_TO_SUFFIX`），
**不是** :data:`~emoji_migrator.platforms.PLATFORM_SUFFIXES`。
后者是「若将来做转码，该平台偏好什么封装」，本包不做转码，因此不参与判定。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from emoji_migrator.config import FORMAT_TO_SUFFIX
from emoji_migrator.platforms import Platform
from emoji_migrator.sniff import SniffResult

__all__ = ["NormalizePlan", "plan_normalize", "apply_normalize", "NORMALIZE_BLOCKED_HINT"]

#: 归一化遇到「无法判定真实格式」时的中文提示前半句（对应「非图片文件」边界）。
NORMALIZE_BLOCKED_HINT = "无法判定真实格式，已跳过（不是可识别的图片文件）："


@dataclass(frozen=True, slots=True)
class NormalizePlan:
    """单个文件的修正计划。

    ``needs_rename`` 是 EM-002 追加的字段：EM-001 定稿时只有
    ``needs_resize`` / ``needs_transcode`` 两个标志，但本轮的核心动作
    「改后缀」既不是缩放也不是转码（它是**改名**），无处表达——
    若不补这个字段，错乱文件会被算成 ``is_noop`` 而原样漏过。
    追加在末尾并带默认值，不破坏既有位置参数构造。
    """

    source: Path
    target: Path
    platform: Platform
    needs_resize: bool
    needs_transcode: bool
    needs_rename: bool = False

    @property
    def is_noop(self) -> bool:
        """无需任何改写时为 True（含「后缀已正确、只搬个位置」的情形）。"""
        return not self.needs_resize and not self.needs_transcode and not self.needs_rename


def _target_name(source: Path, out_dir: Path, suffix: str, taken: set[str]) -> str:
    """产出目标文件名，并在已占用时追加序号，避免静默覆盖。

    冲突是真实存在的：同一目录里``a.jpg``（实为GIF）与 ``a.gif``（也是GIF）
    修正后都想叫 ``a.gif``。直接覆盖会让先落盘的那一个**消失**——
    对一个「整理」工具来说那是数据丢失，不是去重。
    """
    candidate = f"{source.stem}{suffix}"
    if candidate.casefold() not in taken:
        return candidate
    for index in range(1, 10_000):
        alt = f"{source.stem}-{index}{suffix}"
        if alt.casefold() not in taken:
            return alt
    raise RuntimeError(f"无法为 {source} 分配不冲突的目标名（候选已耗尽）")


def plan_normalize(
    result: SniffResult,
    platform: Platform,
    out_dir: Path,
    taken: set[str] | None = None,
) -> NormalizePlan:
    """为单个文件制定修正计划。

    ``needs_resize`` 与 ``needs_transcode`` 在本轮**恒为 False**：
    前者由 D-13 裁决（超大表情只提示不缩放），后者因本轮不做转码而无处发生。
    它们保留在签名里，是为了让后续接压缩的工作包不必改调用方。

    :raises ValueError: 真实格式不可判定（非图片文件、零帧/损坏文件）。
        批量场景下单个坏文件不应中断整批，故由调用方先用
        :data:`~emoji_migrator.config.FORMAT_TO_SUFFIX` 过滤。
    """
    suffix = FORMAT_TO_SUFFIX.get(result.actual_format)
    if suffix is None:
        raise ValueError(f"{NORMALIZE_BLOCKED_HINT}{result.path}（实测 {result.actual_format}）")

    name = _target_name(result.path, out_dir, suffix, taken if taken is not None else set())
    return NormalizePlan(
        source=result.path,
        target=out_dir / name,
        platform=platform,
        needs_resize=False,
        needs_transcode=False,
        needs_rename=result.path.suffix.lower() != suffix,
    )


def apply_normalize(plan: NormalizePlan) -> Path:
    """执行修正计划并返回输出路径。

    实现是**逐字节复制**，不解码不重编码（理由见模块 docstring）。
    目标已存在且与源不同路径时覆盖写——覆盖是本工具的既定语义：
    重复运行同一目录应得到同一结果，而不是不断堆积副本。
    """
    plan.target.parent.mkdir(parents=True, exist_ok=True)

    if plan.source.resolve() != plan.target.resolve():
        shutil.copyfile(plan.source, plan.target)

    return plan.target
