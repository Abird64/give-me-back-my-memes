"""共享测试夹具：生成合成样本集，并在整个测试会话内复用。

**关键决策：合成集只生成一次。**

146 个文件里含两个 2.4 MB 级的超大体积样本，每个用例都重新生成一遍会让
测试从4 秒涨到几分钟。改用会话级 ``tmp_path_factory`` 目录 + 只生成一次，
各测试文件用 ``--fixtures-only`` 的薄封装读取同一份数据。

样本**不提交进仓库**（版权 +隐私，见 ``fixtures/README.md``），每次运行现生成。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

#: PySide6 无头运行（EM-004 GUI 测试与验收截图都靠它）：Qt 在 **QApplication 构造时**
#: 读取 QT_QPA_PLATFORM，因此必须在任何 PySide6 import 之前设置。
#: conftest 先于测试模块导入，放这里可保证整个会话一致。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "fixtures"

#: 合成集生成脚本的位置。用路径注入而非 ``importlib``，是因为 ``fixtures/``
#: 不是包（没有 ``__init__.py``），且这样写能让「换 cwd 就失效」这条老坑不复发。
_GENERATOR_PATH = FIXTURES_DIR / "generate_fixtures.py"


def _load_generator():
    """加载 ``fixtures/generate_fixtures.py`` 模块。"""
    import importlib.util

    if not _GENERATOR_PATH.is_file():
        pytest.fail(f"缺少合成样本生成脚本：{_GENERATOR_PATH}")

    spec = importlib.util.spec_from_file_location("em002_generate_fixtures", _GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def generator():
    """合成样本生成脚本模块。"""
    return _load_generator()


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory: pytest.TempPathFactory, generator) -> Path:
    """整个会话共用的合成样本目录（生成一次）。"""
    out_dir = tmp_path_factory.mktemp("synthetic")
    generator.generate(out_dir)
    return out_dir


def sample_paths(directory: Path) -> list[Path]:
    """目录下全部文件，按名排序（与CLI 的遍历口径一致）。"""
    return sorted((p for p in directory.iterdir() if p.is_file()), key=lambda p: p.name)


def pytest_report_header(config) -> list[str]:  # noqa: ARG001
    """在测试报告开头声明运行环境，避免「用错解释器」这类经典误判。"""
    return [f"python: {sys.executable}"]
