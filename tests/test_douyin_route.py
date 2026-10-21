"""抖音登录路线实测记录守护（EM-005-A，RUN-20261019-004）。

守护对象是 `docs/09-抖音登录链路实测.md`（EM-005 路线的耐久真相源）：

- 关键实测标记在位（现役域名/aid/pageId/三关结论）——删了就不会有人发现
  「照着旧口径（TSDK 6383 / passport 域名）做一定失败」；
- `pyproject.toml` 声明 `curl_cffi` 时，docs/09 §4 必须说明其用途与许可证
  （协议 §8 新依赖须说明）——两边自洽，防「悄悄加依赖」；
- 隐私：实测记录不含真实 QQ 号/用户名（本轮红线）。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

#: 仓库根（tests/ -> 仓库根）。刻意不用 ``__file__`` 相对路径：换 cwd 就失效。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 本机隐私红线值（真实 QQ 号 / 用户名）——**不写在本文件**，见 ``_private_values.json``。
_PRIVATE_VALUES_PATH = Path(__file__).with_name("_private_values.json")


def _privacy_needles() -> tuple[str, ...]:
    """读本机隐私红线值；公开仓库不含该文件，返回空。"""
    if not _PRIVATE_VALUES_PATH.is_file():
        return ()
    data = json.loads(_PRIVATE_VALUES_PATH.read_text(encoding="utf-8"))
    return tuple(data.get("privacy_needles", ()))

#: docs/09 必须出现的关键标记（2026-10-19 实测结论，改路由前必读）。
_DOC09_MARKERS = (
    "sso.douyin.com",
    "10006",
    "31827",
    "get_qrcode",
    "a_bogus",
    "curl_cffi",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def test_douyin_route_doc09_has_measured_markers() -> None:
    """docs/09 收录现役域名、aid/pageId 与三关结论标记。"""
    text = _read(REPO_ROOT / "docs" / "09-抖音登录链路实测.md")
    for marker in _DOC09_MARKERS:
        assert marker in text, f"docs/09 缺少关键标记：{marker}"


def test_curl_cffi_declaration_matches_doc09() -> None:
    """pyproject 声明 curl_cffi ⟺ docs/09 §4 说明其用途与许可证（协议 §8 自洽）。"""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    declared = any("curl_cffi" in dep for dep in cfg["project"]["dependencies"])
    text = _read(REPO_ROOT / "docs" / "09-抖音登录链路实测.md")
    documented = "curl_cffi" in text and "MIT" in text
    assert declared == documented, (
        f"pyproject 声明（{declared}）与 docs/09 说明（{documented}）不一致——"
        "新增依赖须在 docs/09 说明用途与许可证，移除依赖须同步删说明"
    )


def test_douyin_route_doc09_has_no_real_credentials() -> None:
    """隐私红线：实测记录不得含真实 QQ 号或本机用户名。

    真实值放在 ``tests/_private_values.json``（不进公开仓库）；
    该文件缺失时本测试跳过——公开克隆者本来也没有这些值。
    """
    needles = _privacy_needles()
    if not needles:
        pytest.skip("隐私红线值未配置（公开仓库不含 tests/_private_values.json）")
    text = _read(REPO_ROOT / "docs" / "09-抖音登录链路实测.md")
    for needle in needles:
        assert needle not in text, f"docs/09 含真实隐私值（隐私红线）：{needle}"
