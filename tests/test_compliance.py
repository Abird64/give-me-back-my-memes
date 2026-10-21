"""合规静态扫描：断言被禁 API 标识符在 ``src/`` 中零出现。

这是产品能否公开发布的第一道闸门（需求基线 §3 非目标 1、2）。

设计要点：
- 递归 ``rglob("*.py")`` 扫描**真实文件内容**，不只扫单个文件。
- ``src/`` 缺失、为空、或模块文件数量不符预期时**必须失败**，不得静默 pass。
- :data:`FORBIDDEN_API_COUNT` 与权威清单长度的一致性由
  :func:`test_guard_count_matches_authoritative_list` 守护（此前该断言**根本不存在**，
  是 ``guard.py`` 注释里的一句空承诺）。
- 只读 API（:data:`ALLOWED_READONLY_APIS`）允许出现，但**每一处使用都必须带注释说明用途**，
  由 :func:`test_readonly_api_usage_is_documented` 守护；
  无法注释的声明处走 :data:`_DOC_ALLOWED` 豁免表，且豁免表必须恰好覆盖
  （见 :func:`test_readonly_api_exemption_table_has_no_extra_entries`）。

读取编码策略（EM-007 显式决策，不采纳工作包建议的 ``errors="replace"``）：
下方所有 ``read_text(encoding="utf-8")`` 都是**严格解码**，遇到非法编码即抛错停下，
这是有意为之——合规扫描遇非法字节时「报错」比「继续扫」正确。
若改用 ``errors="replace"``，非法字节会被替换成 ``�``，
一旦被禁标识符恰好跨越非法字节就会被静默漏检，那是**降低保护力**而非容错。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import emoji_migrator.guard

#: ⚠️ **允许出现 U+FFFD（``�``）的位置**——只有**讲这件事本身**的文档才允许。
#:
#: 背景（2026-10-21）：用户真机跑打包版时弹出一个
#: ``UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'`` 窗口。
#: 排查发现同仓里有 3 处 **U+FFFD 替换字符**——那是**编辑工具写文件时
#: 字符被截断**留下的损坏（不是有意写的）。
#: U+FFFD 危险在于：它**能被 UTF-8 正常存储**，所以肉眼扫不出来、测试也不报错，
#: 但 ``gbk`` 编不了它 ⇒ 一旦被 print 到 GBK 控制台就抛异常。
#:
#: 本表只登记「**故意**用 ``�`` 当例子」的位置（讲 ``errors="replace"`` 的后果），
#: 其余任何位置出现 U+FFFD 都视为**文件损坏**，必须变红。
ALLOWED_FFFD_LOCATIONS: tuple[str, ...] = (
    # 本文件 docstring 里讲「errors=replace 会产生 �」
    "tests/test_compliance.py",
    # 架构文档里讲「errors=replace 会产生 �」
    "docs/architecture.md",
)

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

#: 被禁 API 清单（权威副本；src/ 侧刻意不复述这些标识符）
FORBIDDEN_APIS: tuple[str, ...] = (
    "WriteProcessMemory",
    "CreateRemoteThread",
    "VirtualAllocEx",
    "VirtualAlloc",
    "LoadLibraryA",
    "LoadLibraryW",
    "NtQueryInformationProcess",
    "SetWindowsHookEx",
    "SuspendThread",
)

#: 允许出现的只读 API（与 src/emoji_migrator/guard.py 的 ALLOWED_READONLY_APIS 保持一致）
ALLOWED_READONLY_APIS: tuple[str, ...] = emoji_migrator.guard.ALLOWED_READONLY_APIS

#: 只读 API 豁免表：键为 src/ 下的**源文件名**，值为该文件中「无法逐行注释说明用途」
#: 的只读 API **行号**白名单。
#:
#: EM-007 的关键设计：**豁免必须恰好覆盖，不能多也不能少**。
#: - 「少」由 :func:`test_readonly_api_usage_is_documented` 抓住（少一项 → 某处无注释的使用变红）。
#: - 「多」由 :func:`test_readonly_api_exemption_table_has_no_extra_entries` 抓住
#:   （多一项 → 该文件并无对应的未注释使用点，纯属给自己开豁免，测试必须变红）。
#:
#: 这样「往豁免表里加一项来给自己开豁免」这条逃逸路径被堵死：
#: 只有当被豁免的**具体行**确实存在该 API 的未注释出现点时才允许登记。
#:
#: **粒度：P-5 裁决（2026-10-20，EM-003 开工前置强制项）——从「文件 + API」升级为
#: 「文件 + **行号**」**。EM-007 遗留的粒度限制原文：「若有人在 ``guard.py`` 内另行添加
#: 一处未注释的只读 API 调用，只要还是这两个 API 之一就不会被本表发现」。
#: EM-003 即将把**真实的 ``ctypes`` 只读扫描代码**写进 ``src/``，
#: 那时该风险从「纸面漏洞」变成真漏洞——必须先堵住。
#:
#: **行号的脆弱性是已知且接受的**：行号会随文件编辑漂移。缓解方式是
#: :func:`test_readonly_api_exemption_table_has_no_extra_entries` 会在行号失效时
#: **立刻变红**（登记的行号没有对应未注释出现点），不会静默漏检。
#: 换句话说：行号过期 = 立刻发现 ≠ 静默放行。
#:
#: 当前唯一一项是 ``guard.py``——那里是白名单的**声明处**，被禁/允许的标识符
#: 字面量按定义必须出现，不属于「使用」，因此无需逐行注释用途；
#: 其上下文注释（``guard.py`` 中 ``ALLOWED_READONLY_APIS`` 上方）已说明整份清单的用途。
#: 新增被允许的 API 时必须同步登记，否则本表会因「存在未登记的未注释出现点」而变红。
#:
#: 命名沿用 ``AGENTS.md`` §1 已引用的 ``_DOC_ALLOWED``，保持仓库内表述一致。
#:
#: ⚠️ **改这张表时必须同步更新行号**——``guard.py`` 的行号会随其内容变化。
#: 跑 ``pytest tests/test_compliance.py -q`` 若报「豁免的行号没有对应出现点」，
#: 按报错里给出的实际行号更正即可。
_DOC_ALLOWED: dict[str, tuple[int, ...]] = {
    # 白名单声明处：标识符字面量在此必然出现，属定义而非使用
    # 行号 49 / 50 = ALLOWED_READONLY_APIS 元组的两个元素（EM-003 开工前实测）
    "guard.py": (49, 50),
}

#: 期望的模块文件数：EM-001 定死的 9 个（含 __init__.py）+ 各工作包新增模块累加。
#: 新增模块必须**同步改这里并注明来源**——本数字是防「静默加模块」的闸门。
#: 当前构成：9（EM-001）+ 1（EM-004 qqdir.py：QQ 目录探测）+ 1（EM-004 gui.py：导入向导）
#: + 1（EM-005-B douyin_auth.py：抖音扫码登录 + DPAPI 加密缓存）
#: + 1（EM-005-C webp_gif.py：WebP→GIF 转换）+ 1（EM-005-C douyin_collect.py：收藏采集）
#: + 1（EM-005-D collect_flow.py：采集编排层，翻页 + 下载 + 转换串联，cli/gui 共用）
#: + 1（EM-003 wxdir.py：微信表情目录只读探测，注册表 +逐级向上找 xwechat_files）
#: + 1（EM-003 wxscan.py：微信进程内存**只读**扫 seed，ctypes + AES 派生校验）
#: + 1（EM-003 wxdecrypt.py：表情解密 + PersistStore 容器结构化切分）
#: + 1（EM-003 wxcollect.py：微信导出编排层，扫种子→解密→wxgf 转码→落盘）
#: + 3（D-17，2026-10-20 人工授权：abogus.py = f2 a_bogus 签名 vendor（Apache-2.0）；
#:   _gmssl_sm3.py / _gmssl_func.py = SM3 国密哈希 gmssl vendor（BSD），abogus 的依赖）。
#: + 1（EM-011，2026-10-21 人工授权：webui.py = WebEngine 前端壳，承载
#:   《把我的表情还回来！》的 HTML 界面 + QWebChannel 桥；不实现业务算法）。
EXPECTED_MODULE_COUNT = 9 + 14

#: 注释用途标记：出现只读 API 的那一行（或其上一行）必须含此前缀
DOC_COMMENT_MARKER = "用途"

#: ⚠️ **不得出现在 src/ 代码里的「本机专有值」**（EM-003 交付后新增）。
#:
#: 背景：用户问「微信表情导出会不会依赖我本机那个 TraceMemo（我独有的）？
#: 别人没有，肯定不能依赖这个项目」。核查确认代码零依赖，但**没有闸门**——
#: 以后谁不小心把本机的 seed / wxid / 绝对路径写死进代码，就是一个
#: 「在我机器上能跑、发给用户就废」的隐形炸弹。
#:
#: 允许的形式：
#: - **注释与 docstring 里的举例**（如 ``wxid_example_ab12``）
#:   —— 那是给人看的说明，不是执行路径；
#: - **可由公式派生的结构**（如 ``"EMOTICON"`` 后缀、目录名前缀 ``wxid_``）。
#:
#: 禁止的形式：把某台机器的具体值**当执行路径的一部分**写死。
#: 故本检查只看**代码行**（剔除纯注释行与 docstring 后仍出现即判违规）。
#:
#: ⚠️ 真实值（本机 wxid / seed / 派生 key / 安装路径）**不写在本文件**——
#: 它们是密钥材料，写进源码等于公开。清单放在同目录的
#: ``_private_values.json``，该文件不进公开仓库；缺失时本检查退化为空清单。
#: 自己机器上要开启这道闸门，照下面格式建一份即可：
#:
#: ```json
#: ["wxid_xxxx", "_后缀", "seed", "derived_key", "D:\\application"]
#: ```
_PRIVATE_VALUES_PATH = Path(__file__).with_name("_private_values.json")


def _load_machine_specific_literals() -> tuple[str, ...]:
    if not _PRIVATE_VALUES_PATH.is_file():
        return ()
    data = json.loads(_PRIVATE_VALUES_PATH.read_text(encoding="utf-8"))
    return tuple(data.get("machine_specific_literals", ()))


MACHINE_SPECIFIC_LITERALS: tuple[str, ...] = _load_machine_specific_literals()


def _code_only_lines(text: str) -> list[tuple[int, str]]:
    """剔除注释行与 docstring，返回 ``(行号, 代码文本)`` 列表。

    判定「是不是注释/文档」用 ``ast`` 精确取注释 token 位置，
    不用 ``#`` 前缀猜——字符串里含 ``#`` 的情况会被误判。

    ⚠️ docstring 判定：模块/类/函数体第一个字符串字面量视为 docstring。
    这与 ``ast.get_docstring`` 的语义一致。
    """
    import ast

    tree = ast.parse(text)
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_lines.update(range(first.value.lineno, first.value.end_lineno + 1))

    result: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if lineno in docstring_lines:
            continue
        result.append((lineno, line))
    return result


def _collect_source_files() -> list[Path]:
    if not SRC_DIR.is_dir():
        pytest.fail(f"src/ 目录缺失：{SRC_DIR}")
    files = sorted(SRC_DIR.rglob("*.py"))
    if not files:
        pytest.fail(f"src/ 下没有任何 Python 源文件：{SRC_DIR}")
    if len(files) != EXPECTED_MODULE_COUNT:
        pytest.fail(
            f"src/ 下应有 {EXPECTED_MODULE_COUNT} 个模块文件，实际 {len(files)} 个："
            f"{[f.name for f in files]}"
        )
    return files


def test_source_tree_shape_is_intact() -> None:
    """src/ 必须存在且模块文件数符合 EM-001 定死的 9 个。"""
    files = _collect_source_files()
    assert len(files) == EXPECTED_MODULE_COUNT


@pytest.mark.parametrize("api", FORBIDDEN_APIS)
def test_forbidden_api_absent_from_src(api: str) -> None:
    """逐个被禁 API 断言其在全部 src 源文件内容中零出现。"""
    for path in _collect_source_files():
        text = path.read_text(encoding="utf-8")
        assert api not in text, f"被禁 API {api!r} 出现在 {path}"


def test_scanner_detects_forbidden_api_in_a_file(tmp_path: Path) -> None:
    """阳性对照：扫描逻辑必须能在真实文件内容中命中被禁 API。

    这是「跑绿不等于有保护」的兜底——防止扫描器被改成永远返回空结果。
    """
    probe = tmp_path / "probe.py"
    probe.write_text("# " + "Write" + "ProcessMemory(1, 2, 3)\n", encoding="utf-8")
    text = probe.read_text(encoding="utf-8")
    assert any(api in text for api in FORBIDDEN_APIS)


# -- 本机专有值闸门（EM-003 交付后新增）------------------------------------------


def test_machine_specific_literals_absent_from_code() -> None:
    """**src/ 的代码行里不得出现本机专有值**（注释与 docstring 允许举例）。

    为什么需要这条闸门：微信表情导出的所有输入（微信目录、seed、wxid）
    都必须**在目标机器上现场取得**。一旦有人把某台机器的具体值写死进
    代码，症状是「在我机器上能跑、发给用户就废」——而且往往要到
    用户手上才暴露。

    这条闸门把「可移植性」从口头约定变成机器检查。
    """
    problems: list[str] = []
    for path in _collect_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in _code_only_lines(text):
            for literal in MACHINE_SPECIFIC_LITERALS:
                if literal in line:
                    problems.append(f"{path.name}:{lineno} 含本机专有值 {literal!r}")
    assert not problems, "src/ 代码行出现本机专有值（会导致换机器即失效）：\n" + "\n".join(
        problems
    )


def test_machine_specific_check_ignores_comments_and_docstrings() -> None:
    """阳性对照：注释与 docstring 里的举例**必须放行**。

    ``wxdir.py`` 的 docstring 里写了 ``wxid_example_ab12`` 作为推导示例
    ——那是给人看的说明，拦下来会让文档无法书写。

    同时验证检测器**确实能抓到代码行**里的真违规。
    本例用自带的假值，不依赖 ``_private_values.json``。
    """
    fake_literals = ("FAKE_WXID_EXAMPLE", "FAKE_SECRET_SEED")
    source = (
        '"""模块说明：例如 FAKE_WXID_EXAMPLE 这样的目录名。"""\n'
        "# 注释里也允许提 FAKE_WXID_EXAMPLE\n"
        'VALUE = "FAKE_SECRET_SEED"\n'
    )
    code_lines = _code_only_lines(source)
    offenders = [
        (lineno, line)
        for lineno, line in code_lines
        if any(literal in line for literal in fake_literals)
    ]
    assert len(offenders) == 1, f"应只抓到代码行那一条，实际 {offenders}"
    assert offenders[0][0] == 3, f"应命中第 3 行的代码，实际第 {offenders[0][0]} 行"


def test_machine_specific_literals_are_not_empty() -> None:
    """闸门本身不能是空的（否则恒绿，等于没检查）。"""
    assert MACHINE_SPECIFIC_LITERALS, "本机专有值清单不能为空"
    assert len(MACHINE_SPECIFIC_LITERALS) >= 4, "清单条目过少，闸门形同虚设"


def test_guard_count_matches_authoritative_list() -> None:
    """``guard.FORBIDDEN_API_COUNT`` 必须等于权威清单的真实长度。

    ``guard.py`` 的注释声称「与权威清单长度做一致性断言，防止清单被悄悄删项」，
    但 EM-001 **从未落地这条断言**（全仓 tests/ 零引用该常量），是空承诺：
    实测把 ``FORBIDDEN_APIS`` 从 9 项删到 8 项，14 个测试全绿、零报警。

    EM-007 补上它。长度从 :data:`FORBIDDEN_APIS` 现场计算，不复写数字 9，
    否则把两边一起改成 8 仍然会全绿。
    """
    assert emoji_migrator.guard.FORBIDDEN_API_COUNT == len(FORBIDDEN_APIS)


# -- 文本损坏闸门（2026-10-21，用户真机报障追出来的）-------------------------


def _scan_fffd_in_repo() -> list[str]:
    """扫全仓（src/ + tests/ + docs/）的 U+FFFD 替换字符。

    排除构建产物与临时目录——那些是复制出来的，不算源。
    """
    root = Path(__file__).resolve().parents[1]
    skip_dirs = {".venv", ".git", "build", "dist", "dist-v2", "_out", ".workbuddy"}
    problems: list[str] = []
    for pattern in ("*.py", "*.md"):
        for path in sorted(root.rglob(pattern)):
            if any(part in skip_dirs for part in path.relative_to(root).parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in ALLOWED_FFFD_LOCATIONS:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "\ufffd" in line:
                    problems.append(f"{rel}:{lineno} 含 U+FFFD 替换字符（文件损坏）")
    return problems


def test_no_replacement_character_in_repo() -> None:
    """⚠️ **源码里不得有 U+FFFD 替换字符**——那是文件损坏，不是内容。

    U+FFFD（``�``）是「解码失败时的替换符」。它出现在仓库里只有一个原因：
    **编辑工具写文件时字符被截断**。危险之处在于它**能被 UTF-8 正常存储**，
    所以：

    - 肉眼扫不出来（看起来就是个方块或问号）；
    - pytest 不会报错（文件是合法 UTF-8）；
    - 但 ``gbk`` 编不了它 ⇒ **一旦被 print 到 GBK 控制台就抛
      ``UnicodeEncodeError``**，且异常发生在 print 那一刻，栈里看不出是哪句话。

    实测本仓曾有 3 处（``collect_flow.py`` / ``gui.py`` / ``wxscan.py``），
    2026-10-21 全部修掉。
    """
    problems = _scan_fffd_in_repo()
    assert not problems, "仓库里出现 U+FFFD 替换字符（编辑时字符被截断）：\n" + "\n".join(
        problems
    )


def test_user_facing_strings_are_gbk_encodable() -> None:
    """⚠️ **用户可见文案必须能被 GBK 编码**——否则 CLI 打印时炸。

    打包后的 exe 在 Windows 上 stdout 是 cp936/GBK（见 AGENTS.md §9）。
    若某个提示语里混进了 GBK 编不出的字符（emoji / 生僻符号 / U+FFFD），
    ``print`` 那一刻就抛 ``UnicodeEncodeError``，用户看到的是**一屏 traceback**。

    本测试用 ``ast`` 精确取出**会被打印的字符串常量**（模块级与函数内
    ``str`` 常量、以及 ``print(...)`` 的实参），逐个验证可 GBK 编码。

    ⚠️ **不查注释与 docstring**——那些不参与输出（docstring 只在
    ``help()`` 时才被读，而本产品不提供 ``--help`` 走这条路径）。
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    src_dir = root / "src"
    problems: list[str] = []

    for path in sorted(src_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        # 收集所有 docstring 的行号（它们不是「会被打印的文案」）
        docstring_lines: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                continue
            body = getattr(node, "body", [])
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_lines.update(
                    range(first.value.lineno, first.value.end_lineno + 1)
                )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if any(ln in docstring_lines for ln in range(node.lineno, node.end_lineno + 1)):
                continue  # docstring，跳过
            try:
                node.value.encode("gbk")
            except UnicodeEncodeError:
                rel = path.relative_to(root).as_posix()
                problems.append(
                    f"{rel}:{node.lineno} 文案含 GBK 编不出的字符：{node.value[:40]!r}"
                )
    assert not problems, (
        "用户可见文案必须能被 GBK 编码（打包后 stdout 是 cp936/GBK）：\n"
        + "\n".join(problems)
    )


def _is_documented(line: str, prev_line: str) -> bool:
    """该行是否已有说明用途的注释。

    口径：同行或上一行存在注释，且注释正文含 :data:`DOC_COMMENT_MARKER`。
    同行注释指 ``#`` 出现在代码之后（行内注释），上一行则整行须以 ``#`` 开头。
    """
    if prev_line.lstrip().startswith("#") and DOC_COMMENT_MARKER in prev_line:
        return True
    _, sep, tail = line.partition("#")
    return bool(sep) and DOC_COMMENT_MARKER in tail


def _readonly_api_hits(text: str) -> list[tuple[int, str]]:
    """找出所有「出现只读 API 且相邻注释未说明用途」的行。

    返回 ``(行号, API 名)`` 列表，行号为 1-based。
    """
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        present = [api for api in ALLOWED_READONLY_APIS if api in line]
        if not present:
            continue
        prev = lines[idx - 1] if idx > 0 else ""
        if not _is_documented(line, prev):
            hits.append((idx + 1, present[0]))
    return hits


def _src_file_index() -> dict[str, Path]:
    """按文件名索引 ``src/`` 下的源文件；同名文件出现多个时立即失败。"""
    index: dict[str, Path] = {}
    for path in _collect_source_files():
        assert path.name not in index, f"src/ 下存在同名源文件，豁免表无法定位：{path.name}"
        index[path.name] = path
    return index


def test_readonly_api_usage_is_documented() -> None:
    """只读 API 的每一处使用都必须有相邻注释说明用途，除非被豁免表登记。

    规则来源：``guard.py`` 的 ``ALLOWED_READONLY_APIS`` 注释与 ``docs/architecture.md`` §4
    都声称「只读 API 允许出现，但使用处必须注释说明用途」——EM-007 之前**没有任何测试守护**，
    属于「声称有但没有」。本测试把它变成可断言的规则。
    """
    undoc: list[str] = []
    for path in _collect_source_files():
        exempt = set(_DOC_ALLOWED.get(path.name, ()))
        for lineno, api in _readonly_api_hits(path.read_text(encoding="utf-8")):
            if lineno in exempt:
                continue
            undoc.append(
                f"{path.name}:{lineno} 使用只读 API {api!r} "
                "但相邻注释未说明用途（若确属声明处，须在 _DOC_ALLOWED 登记**该行号**）"
            )
    assert not undoc, "只读 API 使用处缺少用途注释：\n" + "\n".join(undoc)


def test_readonly_api_exemption_table_has_no_extra_entries() -> None:
    """豁免表必须**恰好覆盖**，不得有多余项。

    这是 EM-007 用来替代失真的 ``_DOC_ALLOWED`` 突变对象的设计：
    对每个被豁免的文件，该文件「出现只读 API 但无相邻用途注释」的 API 集合
    必须与豁免表登记的集合**完全相等**——不多不少。

    - 不等（豁免项多于实际需要）→ 空头豁免，测试变红。
    - 不等（豁免项少于实际需要）→ 同文件里多了一处未登记的未注释使用，测试也变红。

    效果：无法靠「往豁免表里加一项」来给自己免检，也没法在被豁免的文件里
    偷偷加一处未注释的只读 API 调用。

    **P-5 后粒度为「文件 + 行号」**（EM-003 开工前置，2026-10-20）：
    比对的是「该文件未注释使用点的**行号集合**」与「豁免表登记的**行号集合**」，
    必须**完全相等**。行号过期（文件编辑导致漂移）会让本测试立刻变红——
    **行号过期 = 立刻发现 ≠ 静默放行**，这是接受行号脆弱性的前提。
    """
    index = _src_file_index()
    problems: list[str] = []
    for filename, linenos in _DOC_ALLOWED.items():
        assert filename in index, f"豁免表登记了 src/ 下不存在的文件：{filename}"
        hits = {
            lineno
            for lineno, api in _readonly_api_hits(index[filename].read_text(encoding="utf-8"))
            if api in ALLOWED_READONLY_APIS
        }
        declared = set(linenos)
        redundant = sorted(declared - hits)
        missing = sorted(hits - declared)
        if redundant:
            problems.append(
                f"{filename} 豁免了这些**行**，但该行并无未注释的只读 API 使用：{redundant}"
                f"（行号可能已随文件编辑漂移——请核对实际出现位置）"
            )
        if missing:
            problems.append(f"{filename} 这些行有未登记的未注释只读 API 使用：{missing}")
    assert not problems, "豁免表未恰好覆盖：\n" + "\n".join(problems)


def test_readonly_api_exemption_table_keys_are_real_files() -> None:
    """豁免表的键必须是 ``src/`` 下真实存在的源文件名。"""
    real = set(_src_file_index())
    phantom = [name for name in _DOC_ALLOWED if name not in real]
    assert not phantom, f"豁免表登记了 src/ 下不存在的文件：{phantom}"


def test_doc_comment_rule_detects_undocumented_usage(tmp_path: Path) -> None:
    """阳性对照：用途注释判定逻辑本身必须真的能抓到「无注释的使用」。

    没有这条，:func:`test_readonly_api_usage_is_documented` 可能因为判定写错
    而永远返回空列表——那就退化成和 EM-001 一样的空承诺。
    """
    api = ALLOWED_READONLY_APIS[0]

    undocumented = f"handle = {api}(pid)\n"
    assert _readonly_api_hits(undocumented) == [(1, api)], "无注释的使用应被判定为缺注释"

    documented = f"# 用途：EM-003 只读扫描\nhandle = {api}(pid)\n"
    assert _readonly_api_hits(documented) == [], "有相邻用途注释的使用不应被判定为缺注释"

    inline_documented = f"handle = {api}(pid)  # 用途：EM-003 只读扫描\n"
    assert _readonly_api_hits(inline_documented) == [], "行内用途注释也应被接受"

    unrelated = f"# 这只是普通说明\nhandle = {api}(pid)\n"
    assert _readonly_api_hits(unrelated) == [(2, api)], "不含用途标记的注释不算数"
