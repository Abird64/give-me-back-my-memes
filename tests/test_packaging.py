"""打包与发布文档一致性守护（EM-006）。

守护对象是**干净签出也能验**的不变量——不构建、不跑 exe，只验「耐久真相源」
（`docs/`）关键就位且互相自洽：

- `docs/07-打包发布与验证.md`：spec 全文 + 构建命令 + 产物名（因为 `*.spec` 与
  `build/`、`dist/` 都在 `.gitignore`，**spec 本体不入库**，docs/07 是唯一耐久副本）；
- `docs/08-使用说明（小白版）.md`：面向用户说明含卸载口径（单文件 exe 删除即卸载）；
- `docs/licenses/NOTICE.md` + `LGPL-3.0-only.txt`：PySide6 LGPL 分发义务的文本就位。

为什么值得单独立文件：这些文档是**分发的合规载体**（LGPL 义务、卸载说明、
复现命令），删一行关键词不会有任何测试报警——直到有人照着做不了为止。
"""

from __future__ import annotations

from pathlib import Path

#: 仓库根（tests/ -> 仓库根）。刻意不用 ``__file__`` 相对路径：换 cwd 就失效。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 发布 exe 的产物名（spec 的 ``name``；与 docs/07/08、RUN 记录一致）。
EXE_NAME = "把我的表情还回来.exe"

#: docs/07 必须出现的关键标记（构建可复现性）。
_DOC07_MARKERS = (
    "emoji-migrator.spec",
    EXE_NAME,
    ".venv/Scripts/python.exe -m PyInstaller",
    "selftest",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def test_packaging_doc07_is_durable_source_of_truth() -> None:
    """docs/07 收录 spec 名、产物名与构建命令（spec 不入库时的耐久副本）。"""
    text = _read(REPO_ROOT / "docs" / "07-打包发布与验证.md")
    for marker in _DOC07_MARKERS:
        assert marker in text, f"docs/07 缺少关键标记：{marker}"


def test_user_guide_covers_uninstall() -> None:
    """小白版说明含卸载口径：单文件 exe，删除即卸载。"""
    text = _read(REPO_ROOT / "docs" / "08-使用说明（小白版）.md")
    assert "卸载" in text
    assert "删除" in text


def test_third_party_notice_covers_pyside6_lgpl() -> None:
    """LGPL 分发义务文本就位：NOTICE 点名 PySide6/LGPL，且许可证全文在目录内。"""
    notice = _read(REPO_ROOT / "docs" / "licenses" / "NOTICE.md")
    assert "PySide6" in notice
    assert "LGPL" in notice
    lgpl = _read(REPO_ROOT / "docs" / "licenses" / "LGPL-3.0-only.txt")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in lgpl
