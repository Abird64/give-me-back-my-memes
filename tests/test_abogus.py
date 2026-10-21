"""vendor 签名器测试（EM-005 D-17）：SM3 标准向量 + ABogus 结构 + provenance 守护。

vendor 三文件（``abogus.py`` / ``_gmssl_sm3.py`` / ``_gmssl_func.py``）已整体豁免
ruff（pyproject per-file-ignores，理由见 pyproject 注释），其正确性**不靠 lint 靠这里**：

- SM3 用 GB/T 32905 标准测试向量钉死——vendor 的 SM3 代码被改动必然红；
- ABogus 对 GET query 生成非空 a_bogus（**结构断言，不做输出值快照**：
  算法内部含随机成分，同一输入两次输出不同属预期，快照必然脆断）；
- provenance 头守护：vendor 文件失去来源留痕块即红（vendor 必须可追溯）；
- 行尾守护：vendor 文件一律 LF（仓库标准，经验原子：行尾坑）。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import pytest

from emoji_migrator import _gmssl_func as func
from emoji_migrator import _gmssl_sm3 as sm3
from emoji_migrator import douyin_collect as dc
from emoji_migrator.abogus import ABogus

#: 仓库根（AGENTS.md §6：以 __file__ 锚定，不用相对 cwd）
REPO_ROOT = Path(__file__).resolve().parents[1]

VENDORED_FILES = ("abogus.py", "_gmssl_sm3.py", "_gmssl_func.py")

#: GB/T 32905-2016 标准测试向量（SM3 权威向量，公开常量，非本仓私有数据）
SM3_VECTORS = [
    (b"abc", "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"),
    (b"", "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b"),
    (
        b"abcd" * 16,
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732",
    ),
]


@pytest.mark.parametrize(("message", "want_hex"), SM3_VECTORS)
def test_vendored_sm3_matches_gb_t_32905_vectors(message: bytes, want_hex: str) -> None:
    """vendor 的 SM3 实现必须复现国密标准向量——改坏 SM3 任何一处必红。"""
    got = sm3.sm3_hash(func.bytes_to_list(message))
    assert got == want_hex


def test_abogus_generates_nonempty_param_for_get_query() -> None:
    """ABogus 对 GET query 产出非空 a_bogus；返回 4 元组。

    实测口径（2026-10-20）：``[0]`` 是**拼好 a_bogus 的完整签名串**（原参数串 +
    ``&a_bogus=``），``[1]`` 是裸 a_bogus 值，``[2]`` 回传构造时的 UA，``[3]`` 是
    body（GET 为空）。
    """
    query = "device_platform=webapp&count=50&cursor=0"
    result = ABogus(user_agent=dc.USER_AGENT).generate_abogus(query)
    assert isinstance(result, tuple) and len(result) == 4
    a_bogus = result[1]
    assert isinstance(a_bogus, str) and len(a_bogus) > 10
    # [0] 必须是「原参数串 + a_bogus」的完整签名串，且与 [1] 一致
    assert result[0] == f"{query}&a_bogus={a_bogus}"
    assert result[2] == dc.USER_AGENT  # 回传的 UA 与构造时一致
    assert result[3] == ""  # GET 请求体为空


def test_abogus_signer_keeps_original_params_in_front() -> None:
    """签名器输出保序：原参数在前、a_bogus 收尾——与签名输入串口径一致。"""
    query = {"device_platform": "webapp", "count": "50", "cursor": "0"}
    out = dc._abogus_signer(dict(query), dc.USER_AGENT)
    assert list(out)[:3] == list(query)
    assert list(out)[-1] == "a_bogus"
    assert out["device_platform"] == "webapp" and out["count"] == "50"
    assert urlencode(out).startswith("device_platform=webapp&count=50&cursor=0&a_bogus=")


def test_abogus_param_chars_fit_custom_alphabet() -> None:
    """a_bogus 取值应落在上游自定义 Base64 字符表 + URL 编码安全字符内。

    宽松断言（仅可见 ASCII、无空白/引号）：输出被 urlencode 后必须能
    无损进入 query 串。精确字符集属上游实现细节，不做字节级快照。
    """
    result = ABogus(user_agent=dc.USER_AGENT).generate_abogus("a=1&b=2")
    a_bogus = result[1]
    assert all(0x21 <= ord(ch) <= 0x7E for ch in a_bogus)


@pytest.mark.parametrize("name", VENDORED_FILES)
def test_vendored_files_carry_provenance_header(name: str) -> None:
    """vendor 文件必须带 provenance 留痕块——被无声替换/剥头即红。"""
    text = (REPO_ROOT / "src" / "emoji_migrator" / name).read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("# ===="), f"{name} 首行不是 provenance 分隔线"
    assert "【vendor 文件】" in lines[1], f"{name} 第 2 行缺 vendor 标记"
    for marker in ("来源项目", "原文锚点", "许可证", "本仓改动", "RUN-20261020-004"):
        assert marker in "\n".join(lines[:20]), f"{name} provenance 头缺「{marker}」字段"


@pytest.mark.parametrize("name", VENDORED_FILES)
def test_vendored_files_use_consistent_line_endings(name: str) -> None:
    """vendor 文件行尾必须**单一风格**（全 LF 或全 CRLF，不得混用）。

    为什么不断言「一律 LF」：本机 git ``core.autocrlf=true``，干净签出会把
    文本文件转成 CRLF——按 LF 断言在新 clone 上必假红（RUN-20261020-004
    提交时实测坐实）。行尾**混用**才是真实危害（sed/diff/字节级突变脚本
    会错位），故钉一致性而非具体风格。
    """
    raw = (REPO_ROOT / "src" / "emoji_migrator" / name).read_bytes()
    crlf = raw.count(b"\r\n")
    lf_only = raw.count(b"\n") - crlf
    assert crlf == 0 or lf_only == 0, f"{name} 行尾混用：CRLF {crlf} 行 / LF {lf_only} 行"
