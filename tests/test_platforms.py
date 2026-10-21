"""平台枚举与目标格式的机器校验。

背景：本产品**不做压缩**，因此平台档位的体积/像素阈值已于 EM-010 下线
（依据：``docs/05-表情档位限制的必要性实测复核.md``——QQ 原样保留了单个达16.6 MB
的表情，说明先前那套体积上限从来不是平台的真实限制；平台自动压缩反而会把动图
压成静态图）。阈值唯一的用途是「压到多大」，不压就没有阈值，所以数字被删除
而非保留为推荐值。

本文件现在守护的是两类**仍然有效**的约定：

- ``Platform`` 枚举的**成员集合**不可增删（尤其 ``DOUYIN``——EM-005 采集要用它
  标识来源端）；
- ``PLATFORM_SUFFIXES`` 是**目标格式**（转成什么），不是压缩阈值；它与枚举成员
  必须**双向一致**：枚举成员不能漏配格式，表里也不能留下悬空键。

设计要点：

- 单向检查（枚举成员 → 必须有格式条目）与反向检查（表的键 → 必须是现存枚举成员）
  分开成两个测试，失败时能直接看出是漏配还是悬空。
- 断言的是**格式字符串本身**，不是「与另一个常量相等」，改错真源会变红。
- 阈值类断言已随常量删除而移除，此处**不再**以任何形式复述已下线的数值。
"""

from __future__ import annotations

import pytest

from emoji_migrator.platforms import PLATFORM_SUFFIXES, Platform

#: 期望的平台成员值集合。写成字面量而非从 ``Platform`` 现算，
#: 这样删掉任一枚举成员都会让断言失败（从枚举现算会跟着一起缩，测试仍全绿）。
EXPECTED_PLATFORM_VALUES = frozenset({"wechat", "qq", "douyin", "source"})

#: 约束表的名字，供 parametrize 复用（避免行长超限）。
#: 阈值表已随 EM-010 下线，现只剩目标格式表一张。
TABLE_NAMES = ("PLATFORM_SUFFIXES",)


def _table(name: str) -> dict[Platform, object]:
    """按名字取约束表。刻意写成查表而非直接引用，便于 parametrize 遍历。"""
    return {
        "PLATFORM_SUFFIXES": PLATFORM_SUFFIXES,
    }[name]


def test_platform_members_are_exactly_the_four_declared_ones() -> None:
    """``Platform`` 的成员集合必须是这四个，不多不少。

    枚举是「平台标识」的唯一真源：EM-005 的抖音采集依赖 ``DOUYIN`` 标识来源端，
    删掉它会让下游 import 失败；而新增成员则必须同步配好目标格式，
    否则 ``PLATFORM_SUFFIXES`` 会漏项（由下一个测试守护）。
    """
    actual = {p.value for p in Platform}
    assert actual == EXPECTED_PLATFORM_VALUES, f"Platform 成员集不符：{sorted(actual)}"


@pytest.mark.parametrize("table_name", TABLE_NAMES)
def test_every_platform_has_an_entry_in_every_table(table_name: str) -> None:
    """每个 ``Platform`` 成员都必须在目标格式表里有条目（单向）。

    防止新增枚举成员时漏配目标格式——漏配会让归一化阶段拿不到该平台该转成什么。
    """
    table = _table(table_name)
    missing = [p.value for p in Platform if p not in table]
    assert not missing, f"{table_name} 缺少这些平台的条目：{missing}"


@pytest.mark.parametrize("table_name", TABLE_NAMES)
def test_no_orphan_entries_referencing_removed_platforms(table_name: str) -> None:
    """约束表的键不得引用已不存在的 ``Platform`` 成员，防止删枚举后留下悬空键。

    报错信息用 ``getattr(k, "value", k)`` 取名字而不是直接 ``k.value``：
    悬空键本身可能根本不是枚举成员（写成裸字符串、或指向已删成员），
    若诊断路径先崩掉，就报不出「哪个键有问题」，这条断言等于半残。
    突变实验 N4（往表里注入裸字符串键）验证过：加这层之前失败信息是
    ``AttributeError``，加了之后才是能指名道姓的 ``AssertionError``。
    """
    orphans = [getattr(k, "value", k) for k in _table(table_name) if k not in set(Platform)]
    assert not orphans, f"{table_name} 存在悬空键：{orphans}"


def test_wechat_suffix_is_the_em001_carried_over_target_format() -> None:
    """钉死微信档的目标格式 ``.gif``。

    ⚠️ 该值**不是**压缩相关的取值，需求基线 C-7 也未限定微信的导出格式——
    它是 EM-001 既定、本仓沿用的**目标格式**，即「微信档转成什么封装」。
    之所以仍要钉死：它是 ``platforms.py`` 的实际取值、EM-002 归一化会直接消费，
    改动必须同步改注释，不允许无声漂移。
    """
    assert PLATFORM_SUFFIXES[Platform.WECHAT] == ".gif"


def test_douyin_prefers_webp() -> None:
    """C-7：抖音档 WebP 优先（这是格式选择，不是阈值）。"""
    assert Platform.DOUYIN.value == "douyin"
    assert PLATFORM_SUFFIXES[Platform.DOUYIN] == ".webp"


# --------------------------------------------------------------------------------------
# EM-010 独立审核转来的两项（由 EM-002 就近并入）
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", [Platform.QQ, Platform.SOURCE])
def test_qq_and_source_target_png(platform: Platform) -> None:
    """**X1：``.png`` 此前零断言**——改掉 ``PLATFORM_SUFFIXES`` 里任一 ``.png`` 全绿。

    EM-010 把本表的语义改成「目标格式」后，``.png``（QQ / SOURCE）就成了
    裸值：既没被 ``test_wechat_suffix_is_...`` 覆盖，也没有任何测试提到它。
    改动实测确认过——把这两个 ``.png`` 改成 ``.bmp``，原有断言**全部通过**。
    这里补上，让「值本身」而不只是「键集合」被钉住。
    """
    assert PLATFORM_SUFFIXES[platform] == ".png"


def test_every_platform_suffix_is_a_dotted_lowercase_suffix() -> None:
    """所有目标格式都必须是「小写、含点」的后缀。

    这是对**全部四个平台**的统一断言，不针对某个具体值，
    因此即使将来改掉某个 ``.png``，这条也会一并变红。
    """
    for platform, suffix in PLATFORM_SUFFIXES.items():
        assert suffix.startswith("."), f"{platform.value} 的目标格式缺前导点：{suffix!r}"
        assert suffix == suffix.lower(), f"{platform.value} 的目标格式含大写：{suffix!r}"
        assert len(suffix) > 1, f"{platform.value} 的目标格式只有点：{suffix!r}"


def test_module_all_matches_actual_public_names() -> None:
    """**X3：``__all__`` 此前无「与模块公开名一致」的自洽断言**。

    早期版本用「非下划线开头且 ``__module__`` 是本模块」来判定公开名，
    结果把 ``PLATFORM_SUFFIXES`` 判成了「不存在」——因为纯数据常量**没有**
    ``__module__`` 属性。判定口径改用 AST 顶层赋值（见
    :mod:`tests.test_module_all` 里的同一实现），并补上 ``hasattr`` 正向断言。
    """
    import ast
    from pathlib import Path

    import emoji_migrator.platforms as module

    declared = set(module.__all__)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    defined: set[str] = set()
    for node in tree.body:
        # 必须同时处理 Assign 与 AnnAssign：本模块的 ``PLATFORM_SUFFIXES`` 写的是
        # 带类型注解的 ``X: dict[...] = {...}``，那是 AnnAssign 而非 Assign。
        # 早期版本只认Assign，于是把它判成「不在__all__ 里」——假失败。
        if isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef):
            defined.add(node.name)
    defined = {name for name in defined if not name.startswith("_")}

    assert not (declared - defined), f"__all__ 列了不存在的名字：{sorted(declared - defined)}"
    assert not (defined - declared), f"公开名未列入 __all__：{sorted(defined - declared)}"
    for name in declared:
        assert hasattr(module, name), f"{name} 在运行时不可访问"


def test_all_entries_are_unique_strings() -> None:
    """``__all__`` 的元素必须都是字符串且无重复。

    不断言它是 ``tuple`` 还是 ``list``：EM-001 定稿时本仓用的是 ``list``，
    「改成 tuple」不是本包该管的风格变更。
    """
    import emoji_migrator.platforms as module

    assert all(isinstance(name, str) for name in module.__all__)
    assert len(set(module.__all__)) == len(module.__all__), "__all__ 有重复项"
