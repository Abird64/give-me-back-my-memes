"""冒烟测试：断言包可导入，且导入到的确实是 ``src/`` 下那一份。

除了「能import」，本文件还守护一个 EM-001 的真实缺口：
``pyproject.toml`` 设了 ``pythonpath = ["src"]``，若 ``sys.path`` 中已存在
可被优先命中的同名模块（例如 ``tests/`` 或仓库根下多出一个 ``emoji_migrator.py``），
``importlib.import_module`` 依然会成功——「importable」不等于「import 到的是正确的包」。
因此这里额外断言被导入模块的 ``__file__`` 落在 ``src/emoji_migrator/`` 内。
"""

from __future__ import annotations

from pathlib import Path

#: 本包在仓库中的真实位置（``tests/`` -> 仓库根 -> src/emoji_migrator）
EXPECTED_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "emoji_migrator"


def test_package_importable() -> None:
    """emoji_migrator 必须可导入。"""
    import emoji_migrator

    assert emoji_migrator is not None


def test_package_has_version() -> None:
    """包必须暴露版本号。"""
    import emoji_migrator

    assert isinstance(emoji_migrator.__version__, str)
    assert emoji_migrator.__version__


def test_every_skeleton_module_importable() -> None:
    """EM-001 定死的 9 个模块 + EM-004 新增的 qqdir/gui 必须全部存在且可导入。"""
    import importlib

    module_names = (
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
    )
    for name in module_names:
        assert importlib.import_module(name) is not None, name


def test_package_resolves_to_src_layout() -> None:
    """被导入的 ``emoji_migrator`` 必须来自 ``src/emoji_migrator/``，不能是同名劫持物。

    ``pythonpath = ["src"]`` 只保证 src 在路径里，**不保证它被优先命中**。
    若别处存在同名 ``emoji_migrator.py`` 或 ``emoji_migrator/`` 先被找到，
    上面那条 importable 断言照样绿——这条守卫就是补这个洞。
    """
    import emoji_migrator

    assert emoji_migrator.__file__ is not None, "emoji_migrator.__file__ 为 None，无法判定来源"
    resolved = Path(emoji_migrator.__file__).resolve()
    assert resolved.parent == EXPECTED_PACKAGE_DIR.resolve(), (
        f"emoji_migrator 被导入自 {resolved}，"
        f"期望来自 {EXPECTED_PACKAGE_DIR.resolve()}（可能是同名模块劫持 sys.path）"
    )


def test_every_skeleton_module_resolves_under_the_same_package_dir() -> None:
    """EM-001 的 9 个模块 + EM-004 新增的 qqdir/gui 必须全部落在同一个
    ``src/emoji_migrator/`` 目录内。

    防止出现「包主体来自 src/、个别子模块来自别处」这种半真半假的通过。
    """
    import importlib

    module_names = (
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
    )
    expected = EXPECTED_PACKAGE_DIR.resolve()
    strays: list[str] = []
    for name in module_names:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None)
        if origin is None:
            strays.append(f"{name} 是命名空间包（无 __file__），无法判定来源")
            continue
        if Path(origin).resolve().parent != expected:
            strays.append(f"{name} 实际来自 {Path(origin).resolve()}，期望位于 {expected}")
    assert not strays, "模块来源被劫持或不在 src 布局内：\n" + "\n".join(strays)
