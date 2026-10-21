"""模块 ``__all__`` 自洽性：列出来的必须真存在，对外的必须被列出来。

（X3，EM-010 独立审核转来，由 EM-002 就近并入。）

原先 ``platforms.__all__`` 没有任何这类断言，于是：

- 漏列一个真被外部使用的名字 ⇒ ``from x import *`` 拿不到，**无人报错**；
- 多列一个不存在的名字 ⇒ 只有真去 import 时才炸，**漂移长期不可见**。

为什么不放在 ``test_platforms.py`` 里单写一份：这条规则对 9 个模块**一律成立**，
逐个模块各写一遍就是 9 份复制品，改了规则就会漏改。参数化成一条更省。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

#: 受 ``__all__`` 自洽性约束的模块清单：EM-001 定死的 9 个 + EM-004 新增的
#: qqdir（QQ 目录探测）与 gui（PySide6 导入向导）+ EM-005-B 新增的 douyin_auth
#: （抖音扫码登录）+ EM-005-C 新增的 webp_gif（WebP→GIF 转换）与
#: douyin_collect（抖音收藏采集）+ EM-005-D 新增的 collect_flow（采集编排层）
#: + EM-003 新增的 wxdir（目录探测）、wxscan（扫 seed）、wxdecrypt（解密+切分）、
#: wxcollect（导出编排层）
#: + D-17 新增的 abogus（f2 a_bogus 签名，vendor）
#: 与 _gmssl_sm3 / _gmssl_func（SM3 国密哈希，gmssl vendor，abogus 的依赖）。
#: 新增模块必须同步登记，否则其 ``__all__``
#: 无守护（由 test_module_names_cover_every_submodule 兜底）。
MODULE_NAMES = (
    "emoji_migrator",
    "emoji_migrator.config",
    "emoji_migrator.platforms",
    "emoji_migrator.sniff",
    "emoji_migrator.normalize",
    "emoji_migrator.dedupe",
    "emoji_migrator.compress",
    "emoji_migrator.guard",
    "emoji_migrator.cli",
    "emoji_migrator.qqdir",
    "emoji_migrator.gui",
    "emoji_migrator.douyin_auth",
    "emoji_migrator.webp_gif",
    "emoji_migrator.douyin_collect",
    "emoji_migrator.collect_flow",
    "emoji_migrator.wxdir",
    "emoji_migrator.wxscan",
    "emoji_migrator.wxdecrypt",
    "emoji_migrator.wxcollect",
    "emoji_migrator.abogus",
    "emoji_migrator._gmssl_sm3",
    "emoji_migrator._gmssl_func",
    "emoji_migrator.webui",
)


def _public_names(module) -> set[str]:
    """模块**自己定义**的公开名（不含下划线开头，也不含 import 进来的依赖）。

    用 AST 而非 ``vars()`` 判定，原因是运行期拿不准「这个名字是本模块定义的，
    还是 import 进来的」：

    - 函数 / 类可以靠 ``__module__`` 判断；
    - **纯数据常量没有** ``__module__`` 属性。而本仓大量使用共享常量
      （如 ``config.SUFFIX_TO_FORMAT`` 被 :mod:`emoji_migrator.sniff` import），
      按「无 ``__module__`` 即本模块定义」判，会把这些**依赖**全算成sniff 的公开名，
      于是要求 sniff 把它们也写进 ``__all__``——那才是错误结论。

    AST 只看顶层「赋值 / 函数 / 类」的定义处，import 语句一概不算，判定精确。
    """
    import ast

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_all_entries_exist_in_the_module(module_name: str) -> None:
    """``__all__`` 里的每个名字都必须真的存在于该模块。"""
    module = importlib.import_module(module_name)
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert not missing, f"{module_name}.__all__ 列了不存在的名字：{missing}"


def test_module_names_cover_every_submodule() -> None:
    """:data:`MODULE_NAMES` 必须覆盖包内全部子模块——少登记一个，它的 ``__all__`` 就悄悄失去守护。

    这条是 EM-004 补的：M7 突变（从清单里删掉 ``qqdir``）在不加这条时**全绿**——
    参数化迭代只跑清单里的模块，删条目不会报错，守护被无声降级。
    """
    import emoji_migrator

    pkg_dir = Path(emoji_migrator.__file__).resolve().parent
    actual = {
        f"emoji_migrator.{path.stem}"
        for path in sorted(pkg_dir.glob("*.py"))
        if path.stem != "__init__"
    }
    assert set(MODULE_NAMES) - {"emoji_migrator"} == actual, (
        f"MODULE_NAMES 与包内实际子模块不一致：清单缺 {sorted(actual - set(MODULE_NAMES))}，"
        f"多列 {sorted(set(MODULE_NAMES) - {'emoji_migrator'} - actual)}"
    )


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_public_names_are_all_exported(module_name: str) -> None:
    """模块定义的非下划线公开名必须都被 ``__all__`` 收录。"""
    module = importlib.import_module(module_name)
    undeclared = sorted(_public_names(module) - set(module.__all__))
    assert not undeclared, f"{module_name} 有公开名未列入 __all__：{undeclared}"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_all_has_no_duplicates(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert len(set(module.__all__)) == len(module.__all__), f"{module_name}.__all__ 有重复项"


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_star_import_exposes_exactly_all(module_name: str) -> None:
    """``from x import *`` 实际拿到的名字必须与 ``__all__`` **完全一致**。

    这是把「自洽」落到**真实行为**上：清单与行为不一致时只有这条会红。

    口径注意：``__all__`` 里的名字**不按「下划线开头就跳过」处理**——
    Python 规定 ``import *`` 严格按 ``__all__`` 取，包括 ``__version__``
    这种 dunder 名。所以这里只滤掉 ``exec`` 自己注入的 ``__builtins__``，
    漏滤会让 ``emoji_migrator``（其 ``__all__`` 就是 ``["__version__"]``）误报。
    """
    namespace: dict[str, object] = {}
    exec(f"from {module_name} import *", namespace)  # noqa: S102
    namespace.pop("__builtins__", None)

    assert set(namespace) == set(importlib.import_module(module_name).__all__)
