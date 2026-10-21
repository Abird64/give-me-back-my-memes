# AGENTS.md — 把我的表情还回来（emoji-migrator）

> 本文件是 `D:/code/mymeme` 仓库的**长期工程约定**。任何 AI 会话或人类开发者在本仓动手前**必读**。
> 与工作包正文冲突时，以工作包的 `allowed_paths` 与验收标准为准（人工明确指令优先级最高）。

---

## 0. 一句话定位

把已有表情素材变成 **微信 / QQ / 抖音** 三端都能直接用的**合规**文件。
产品定位是「表情整理与迁移工具」，**不是**「表情解密工具」。

真相源（不在本仓）：`D:\生命之河\01-Projects\AI任务系统\`
—— 项目说明、工作包、运行记录都在那里，改代码前先读对应工作包。

---

## 1. 合规红线（最高优先级，任何情况不得越过）

这是本产品能否公开发布的**第一道闸门**。9 个被禁 API 在 `src/` 中必须**零出现**：

```
WriteProcessMemory   CreateRemoteThread   VirtualAllocEx
VirtualAlloc         LoadLibraryA         LoadLibraryW
NtQueryInformationProcess                 SetWindowsHookEx
SuspendThread
```

- **权威清单唯一副本**在 `tests/test_compliance.py` 的 `FORBIDDEN_APIS`（9 项）。
  `src/` 里**不得**复述这些标识符——否则守门常量会触发它自己守的闸门。
  ✅ 长度一致性由 `test_guard_count_matches_authoritative_list` 守护
  （**从清单现场算长度**，不复读数字，所以两边一起改仍会红）。
- **允许出现**（EM-003 只读扫描需要）：`ReadProcessMemory`、`OpenProcess`。
  但**必须带注释说明用途**——由 `test_readonly_api_usage_is_documented` 守护，
  豁免走 `_DOC_ALLOWED`（**必须恰好覆盖，多一项即红**）。
  ✅ **P-5 已于 2026-10-20 完成**：豁免粒度从「文件 + API」升级为
  「**文件 + 行号**」（`guard.py` 登记 `(49, 50)`，现场量准）。
  EM-007 记录的「同文件另加一处未注释调用不会被发现」这个后门**已堵**
  （突变验证：注入未注释的 `OpenProcess` 调用 → **2 个测试变红**）。
  ⚠️ 写 `ctypes` 只读代码时留意：注释里必须含「**用途**」二字；
  行号过期会让测试**立刻变红**（不是静默漏检），报错里会给出实际行号。
- 其他硬性非目标：不做网络拦截 / 抓包、不解密聊天数据库（只做 emoticon）、
  不做键鼠模拟、不用注入框架、不内置任何表情素材、不分发。

> ⚠️ **合规扫描是文本扫描，不是编译期检查。** 它拦得住「人工写出禁用 API 字面量」，
> 拦不住运行时动态拼接。**别把测试全绿当成合规证明**——新增任何 `ctypes` /
> `win32` / `subprocess` 相关代码前，先想清楚有没有绕开红线。

---

## 2. 环境（已实测，别重新踩）

```bash
# 唯一正确的跑法：项目内 .venv
cd D:/code/mymeme
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -c "import emoji_migrator"
```

- **managed Python 本体没装 pytest / ruff**（实测 `ModuleNotFoundError`）。
  工具链在**项目内 `.venv`**，已在 `.gitignore`，**不要**装到全局解释器。
- 已装：`pytest 9.1.1` / `ruff 0.17.0` / `Pillow` / `imageio` / `imageio-ffmpeg` / `pycryptodome`
- Python `>=3.11`（实测 3.13.14）
- **仓库在 Windows 本地**（`D:/code/mymeme`），**不要**走 WSL UNC 路径写入（`//wsl.localhost/...`
  的 Edit/Write 覆盖已有文件会失败）。

---

## 3. 模块命名 —— 已定死

`src/emoji_migrator/` 下 **22 个模块**（= EM-001 定死的 9 + 后续工作包新增 13）：
9 个在EM-001 定稿，`gui.py` / `qqdir.py` 为 EM-004 新增，
`douyin_auth.py`（EM-005-B）、`webp_gif.py` / `douyin_collect.py`（EM-005-C）、
`collect_flow.py`（EM-005-D 采集编排层）、`wxdir.py` / `wxscan.py` / `wxdecrypt.py` /
`wxcollect.py`（EM-003 微信：目录探测 / 内存扫 seed / 解密切分 / 导出编排），
`abogus.py` / `_gmssl_sm3.py` / `_gmssl_func.py` 为 D-17 vendor（2026-10-20 人工授权）。
**后续工作包不得改名**：

| 模块 | 职责 |
| --- | --- |
| `config.py` | 跨模块共享纯常量（档位枚举、路径、后缀集合）。**无 IO、无算法** |
| `platforms.py` | 平台枚举与各平台目标格式后缀表。**无阈值**（EM-010 已下线全部档位常量） |
| `sniff.py` | 读文件头判定真实格式（**不信后缀**） |
| `normalize.py` | 后缀修正与尺寸修正 |
| `dedupe.py` | 感知哈希去重（动图按像素内容，非文件字节） |
| `compress.py` | 按平台档位压缩 |
| `guard.py` | 合规守门阈值与策略元数据 |
| `cli.py` | 命令行入口，命令行侧**唯一允许编排多个业务模块的地方** |
| `gui.py` | PySide6 三步向导（EM-004 新增）：GUI 侧编排，经 `cli` 走导出管线，另调 `qqdir` 探测与 `sniff` 判后缀；EM-005-B 接入「登录抖音」，EM-005-D 接入「采集抖音收藏」（走 QThread） |
| `qqdir.py` | QQ 表情目录只读探测（EM-004 新增）：注册表两级链（`winreg`，只读），**叶子模块**，无包内依赖 |
| `douyin_auth.py` | 抖音扫码登录（EM-005-B 新增）：QWebEngineView 登录窗 + `cookieAdded` 捕获 + DPAPI 加密落 `%APPDATA%`。**叶子模块**，不 import 任何包内模块 |
| `webp_gif.py` | WebP→GIF 转换（EM-005-C 新增）：alpha 掩模合成白底防黑边 + 自适应量化 + 动图帧/loop 保留。**叶子模块** |
| `douyin_collect.py` | 抖音收藏采集（EM-005-C 新增）：curl_cffi chrome145 指纹 + D-15（401 → `SessionExpired`）+ 下载域名白名单 + 签名器注入位。仅依赖 `abogus`。EM-005-D 加 `fetch_page` 翻页能力 |
| `collect_flow.py` | **采集编排层**（EM-005-D 新增）：翻页推进 + 下载 + WebP 转换 + 计数。**cli 与 gui 共用**，故独立成模块。**零 Qt 依赖**（Qt 是 GUI 概念，编排层要能被命令行复用） |
| `wxdir.py` | 微信表情目录只读探测（EM-003）：注册表 `HKCU`→`HKLM` 读**程序目录**，再**逐级向上**（`ASCEND_LEVELS=3`）找 `xwechat_files`；附 `wxid_of`。**叶子**，只 import 标准库 |
| `wxscan.py` | 微信进程内存**只读**扫 seed（EM-003）：`ctypes` 调 `OpenProcess(PROCESS_VM_READ)` + `ReadProcessMemory`，正则提候选 + AES 魔数校验。**全仓唯一用只读 Win32 API 的模块**，P-5 行号粒度在此实战守护。**叶子**（只 import 标准库 + `guard`） |
| `wxdecrypt.py` | 微信表情解密 + `PersistStore` 容器**结构化**切分（EM-003）：AES-128-CBC（**IV=key**）+ PKCS7；GIF 按**块结构**切（不能搜字节）。**叶子**，不 import 任何包内模块 |
| `wxcollect.py` | 微信导出**编排层**（EM-003）：扫种子 → 解密 → wxgf 转 GIF → 原子落盘。与 `collect_flow` 对称。**只写自己的输出目录，绝不写回微信目录** |
| `abogus.py` | **vendor**（D-17）：f2 `a_bogus` 签名，Apache-2.0，逐字保留 + provenance 头。依赖两个 `_gmssl_*` |
| `_gmssl_sm3.py` | **vendor**（D-17）：SM3 国密哈希，gmssl 3.2.2，BSD。依赖 `_gmssl_func` |
| `_gmssl_func.py` | **vendor**（D-17）：gmssl 通用函数，BSD。**叶子模块**，无包内依赖 |
| `__init__.py` | 只放 `__version__` 等元信息，**不导入子模块** |

> ⚠️ **模块数有机器闸门**：`tests/test_compliance.py` 的 `EXPECTED_MODULE_COUNT = 9 + 13`。
> 新增模块**必须同步改这里并注明来源**，否则 `test_source_tree_shape_is_intact` 直接红。
> 该数字是防「静默加模块」的闸门—— EM-005-B 独立审核时故意把它改小，**13 个用例变红**，
> 坐实此闸门确有必要。EM-005-D 加 `collect_flow`、EM-003 加 `wxdir` 时它都真的报红了。

改名 = 破坏性变更，必须回规划轮重评。详见 `docs/architecture.md`。

### 依赖方向（单向，不得反向或成环）

```
cli → {sniff, normalize, dedupe, compress} → {config, platforms, guard} → __init__
cli → collect_flow → {douyin_collect, webp_gif}
douyin_collect → abogus → {_gmssl_sm3 → _gmssl_func}
gui → {cli, collect_flow, qqdir, sniff, config, platforms, douyin_auth}
     gui 内部另有 _CollectThread(QThread) 调 collect_flow —— Qt 概念留在 GUI 侧
cli/gui → wxcollect → {wxdir, wxscan, wxdecrypt}
qqdir / webp_gif / douyin_auth / _gmssl_func / wxdir / wxscan / wxdecrypt → （叶子）
```

1. 箭头只能从上往下。
2. **业务模块之间不互相 import**（`normalize` 不 import `dedupe`），串联由 `cli` 完成；
   `gui` 是 GUI 侧编排点（复用 `cli`，不重写串联逻辑）。
3. `config` / `platforms` / `guard` / `qqdir` / `webp_gif` / `douyin_auth` / `_gmssl_func`
   / `wxdir` / `wxscan` / `wxdecrypt` / `collect_flow` 是叶子（编排层只 import 业务模块，不被业务模块 import）。
4. `__init__.py` 不导入子模块，保证 `import emoji_migrator` 不触发整包加载。
5. **vendor 家族例外说明**：`abogus` → `_gmssl_*` 的依赖链是 vendor 保真的**硬约束**
   （上游就是这么写的），不算「业务模块互相 import」。三个 vendor 文件在
   `pyproject.toml` 的 `per-file-ignores` 中豁免 ruff（实测撞89 处 lint），
   但**测试守护仍然管它们**（provenance 头、行尾、SM3 标准向量）。
6. **编排层与 Qt 的分界**：`collect_flow` **零 PySide6 依赖**
   （有守护断言）；线程这类 Qt 概念一律留在 `gui._CollectThread`。
   反向也不通——`collect_flow` 不得 import `gui`。

---

## 4. 写入范围

**每个工作包的 `allowed_paths` 是唯一写入白名单**，不得自行扩大。需要新路径 → 阻塞，等人工授权。

| 工作包 | 允许写入 |
| --- | --- |
| EM-001 | 根级仅 `pyproject.toml` / `README.md` / `.gitignore`（**只允许追加**）+ `src` + `tests` + `docs` + `fixtures` |
| EM-002 | `src` + `tests` + `docs` + `fixtures` + `_out`（**无仓库根**） |
| EM-003 | `src` + `tests` + `docs` + `_out` |
| EM-004 | `src` + `tests` + `docs` + `fixtures` |
| EM-005 | `src` + `tests` + `docs` |
| EM-006 | `src` + `tests` + `docs` + `build` + `dist` |
| EM-007 | `src` + `tests` + `docs` |
| EM-008 | 仓库根（仅 `AGENTS.md` 本身） |
| EM-009 | `src` + `tests` + `docs` |
| EM-010 | `src` + `tests` + `docs` |

> **BL-17 已解除（2026-10-19，人工授权）**：`pyproject.toml` 对 EM-006 追加开放，范围限于
> `[tool.setuptools.package-data]`（guides PNG 入 wheel）+ PyInstaller build 依赖声明两项。
> 后续包**不继承**该授权——仍需新写入路径时按协议 §5.2 阻塞等授权。

### EM-005 的四处额外授权（2026-10-19/20，人工逐次授权）

EM-005 正文 `allowed_paths` 是 `src` + `tests` + `docs`，但实际落地时人工另外开了四个口子。
**后续 EM-005 子包（如 EM-005-D）不自动继承**，需要时仍要重新问：

| 授权内容 | 范围 | 授权时间 |
| --- | --- | --- |
| `pyproject.toml` | **仅** `dependencies` 加 `curl_cffi` 一项（MIT） | 2026-10-19（RUN-004） |
| `src/` 内 vendor | `abogus.py` + `_gmssl_sm3.py` + `_gmssl_func.py`，含 `per-file-ignores` 豁免 ruff | 2026-10-20（D-17，RUN-004） |
| `build/` | **仅** `build/emoji-migrator.spec` 补收 QtWebEngine 资源 | 2026-10-20（RUN-004） |
| 仓库根 `AGENTS.md` | EM-005 域**无权**改；改动归 **EM-008**（人工 2026-10-20 授权 EM-008 续作） | 2026-10-20 |

**绝不能碰**：`personal_emoji` 等用户真实表情目录（只读抽样可以，**绝不复制进仓库**——版权 + 隐私）。

### 打包与发布（EM-006 落地，勿重复发现）

- `build/`（含 `*.spec`）与 `dist/` **均不入库**（`.gitignore`）——**打包 spec 的耐久真相源是
  `docs/07-打包发布与验证.md` §2**（spec 全文逐行收录），改打包先改那里再同步磁盘 spec。
- PyInstaller 是**构建期工具**：venv 安装（`.venv/` 已忽略），`pyproject.toml` 以
  `[project.optional-dependencies].build` 声明（BL-17），不进 exe 运行时依赖。
- 许可证归集：`docs/licenses/`（NOTICE + 各组件全文，LGPL 全文 md5 与 gnu.org 官方对账）；
  构建后须 `cp -r docs/licenses dist/THIRD-PARTY-LICENSES`（与 exe 同级，LGPL 分发义务）。
- 面向用户的小白版说明是 `docs/08-使用说明（小白版）.md`，**不要**把开发者文档当用户文档。
- ⚠️ **QtWebEngine 打包**：EM-005-B实测坐实 PyInstaller 的 Qt6 hook **只收 15 个 `.qm` 翻译**，
  `QtWebEngineProcess.exe` 的 binaries 逻辑写死在 macOS 分支→ **必须手写 spec 补收**
  `QtWebEngineProcess.exe` + `resources/*.pak`(4) + `icudtl.dat`。
  补收后 exe 231.7 MB（补收前271.9 MB）；构建 4m0s。
  ⚠️ **hooks-contrib 2026.8起QtWebEngineCore hook 已整目录收 `resources/`**——
  B包「只收 .qm」的结论**已过时**，但子进程 exe 与 DLL 仍不收，spec 仍需保留。
- ⚠️ **`dist/` 里的 exe 可能落后于源码**（2026-10-20 实测）：当时 exe 构建于 16:33，
  而端点校准提交在 17:14 ⇒ **exe 是旧版**。**发布前先比对 exe mtime 与 HEAD 时间**。

---

## 5. Git

- 工作区**必须干净**才能开始；不干净先查来源，不自行 `clean` / `stash` / `reset`。
- 提交信息**必须含任务 ID**，例如 `feat(EM-002): 实现格式嗅探与后缀修正`。
- **不自动 push、不建 PR、不合并远程分支。**
- 单个任务一个主要可验收结果；预计 30–90 分钟。超过 2 小时必须拆包。

---

## 6. 测试与质量门禁

```bash
.venv/Scripts/python.exe -m pytest tests -q          # 必须全绿
.venv/Scripts/python.exe -m ruff check src tests     # 必须 All checks passed!，零告警
```

- **ruff 基线是零告警。** 不要引用任何文档里写的基线数字——**现场实测**。
- 报数字时**写明口径**：`448 passed` 是 pytest 收集的**测试实例数**（parametrize 按实例展开），
  不是 assert 语句数。用 `grep -c` 计数会把定义行算进去。
  ⚠️ 参数量变化会让这个数**上下浮动**——历史轨迹：EM-007 的 32 → EM-010 删阈值 26 →
  EM-002 补测 228 → EM-004 273 → EM-006 278 → EM-005-A 281 → EM-005-B 306 → 308 →
  EM-005-C 336 → D-17 vendor 签名器 362 → EM-005-D 编排层 408 → 补 16 条守护 424 → GUI 挂载 **448**，
  **基线永远是运行时实测值，不是本文档里的数**。
- 涉及文件系统的测试用 `Path(__file__).resolve().parents[N]` 锚定仓库根，
  **不要**用 `__file__` 相对路径——那样换 cwd 就失效。
  ✅ 已全部合规：`test_smoke.py:15` / `test_compliance.py:31` 均为 `parents[1]`。
- **「跑绿」不等于「有保护」。** 新增任何规格类断言（守门规则、架构约束、合规扫描），
  必须做**突变实验**：故意改坏，对应测试**必须变红**。改测试时尤其要做——
  包括**篡改豁免清单本身**（`_DOC_ALLOWED` 加一项多豁免 → 必须红）。
- **核查断言是否零覆盖，要挑「最可能没被覆盖」的点去打**。
  EM-007 的独立审核用这招查出 2 处真零覆盖：`PLATFORM_SUFFIXES[WECHAT]` 与
  `PLATFORM_MAX_EDGE[QQ]` 改掉后测试**仍全绿**。
- 测试集用 `fixtures/generate_fixtures.py` **合成**，不提交真实用户表情。

---

## 7. 真实表情数据（重要）

- 本机 QQ 表情目录在**安装盘且目录名为中文**：
  `D:\application\lnstallation\qq\聊天消息\Tencent Files\*\nt_qq\nt_data\Emoji\personal_emoji\Ori`
  实测 **152** 个文件（EM-004 开工前 2026-10-14 复测；10-12 规划时为 146，之后用户新增 6 个），
  其中 **52 个后缀错乱**（声明 `.jpg` 实为 GIF 49 个 / PNG 3 个），
  其余为 JPEG 且声明后缀正确（不计入错乱）。EM-002 验收按**不变量**断言，不硬编码文件数。
- 微信表情（**2026-10-20 递归实测，BL-7 口径已厘清**）：
  数据目录实际在 `D:\application\lnstallation\wechat\聊天记录\xwechat_files\wxid_*_cd34\`，
  **不在**注册表 `InstallPath` 下。
  ⚠️⚠️ **`emoticon` 下的条目是目录，不是文件**——顶层 `Persist` 只有 **256 个子目录**，
  递归统计真实文件才是总数：

  | 目录 | 顶层条目 | 递归真实文件 |
  | --- | --- | --- |
  | `Persist` | 256（**全是目录**） | **1130** |
  | `PersistStore` | 13 | 15 |
  | `Thumb` | 255 | 1396 |
  | `ThumbStore` | 13 | 30 |

  - 旧文档写的「1103」=把顶层目录数当文件数导致的**错误口径**（详见 EM-003 任务文档）。
    1130 vs 1103 差27 个，多半是记录之后用户新增的表情（QQ 侧同类漂移：146 → 152）。
    **验收一律按不变量断言**（`md5(切片) == 文件名` 逐个对），不硬编码文件数。
  - ⚠️ **探测时不要对目录调 `Path.open()`** —— Windows 上目录 open **恒抛 `PermissionError`**，
    与 ACL 无关。EM-003 曾因此误判「524/524 全部 PermissionError、非管理员读不到」，
    2026-10-20 复核**证明是假阳性**：ACL 逐级 `icacls` 全继承、**零 DENY ACE**，
    非管理员会话 1130/1130 **全部可读**。
  - 同理**不需要管理员权限**：`OpenProcess(PROCESS_VM_READ)` 对 `Weixin.exe` 5/5 成功，
    `ReadProcessMemory` 实读主模块 2977792/2977792 字节（Windows 上同用户进程默认可读）。
    ⇒ **EM-003 曾在「需要管理员会话」上阻塞 7 天，是误判。**
  - ✅ **A-1 公开公式已在本机证实**（2026-10-20，RUN-20261020-012）：
    `key = bytes.fromhex(md5(f"{seed}{wxid}EMOTICON").hexdigest())[:16]`，**IV=key**。
    **2588/2588 文件 100% 解出、零失败**；wxgf→GIF 5/5（4 个真动图，35/31/93/4 帧）。
    ⚠️ 本机实测的 seed 值与派生 key **不入库**——它们是密钥材料，只登记在
    `tests/_private_values.json`（本地文件，不进公开仓库）。
    ⚠️ **踩过的两个坑**（写扫描代码时别再错）：
    ① key 用 `hexdigest()[:16]` 是拿**十六进制字符串**当字节，必须用 `bytes.fromhex`；
    ② **IV 是 key 本身，不是全 0**（README 只写「key = IV」，极易读错）。
    另：seed 正则 `\d{8,12}` + 范围 `1e8<v<4e9`；magic 只认 **4 字节强 magic**——
    `\x30\x82` 这种 **2 字节**的在几万候选里必然误命中，禁止纳入判据。
  - ⚠️ **数据目录是 `InstallPath` 的兄弟目录**（实测，比旧结论精确）：
    注册表 `InstallPath` → `<安装根>\Weixin`（程序子目录），
    数据在 `<安装根>\聊天记录\xwechat_files` ⇒ **必须逐级向上找**
    （`wxdir.ASCEND_LEVELS=3`，有界不做全盘搜索）。
- 执行轮**可只读抽样对照统计**（读取不改），**不得复制进仓库**。
- **平台产物目录是验证平台行为的最佳证据**（优于搜索、优于官方文档解读）：
  - QQ：`personal_emoji/Ori`（**原件**，146 文件 51 MB）vs `Thumb`（**QQ 自动生成**，3.0 MB）
  - 微信：`business/emoticon/Persist`（84 MB）vs `Thumb`（25 MB）
  - 两端**同构** ⇒ 「保留原件 + 自动生成展示副本」是行业通行做法。
  - ⚠️ 由此发现：平台自动压缩会**把动图压成静态图**（本机 46 个 GIF → 0 个动图）。
    详见 `docs/05-表情档位限制的必要性实测复核.md` 与 §8 的「语义提醒」。
- **网络上的平台规格不可作判据**：实测检索到的 5 个来源给出 500KB / 1MB / 2MB **三套矛盾数字**，
  且**全部来自表情压缩工具的营销页**，非平台文档。

---

## 8. 已知待修（别重复发现）

> ⚠️ **本节已于 2026-10-13 全面重写，2026-10-19 BL-5 还魂再更新。** 上一版本列的 5 项里，
> **3 项经源码级核实「从未存在」**（它们来自上一轮审核 Agent 的观察，在转述进本文件时丢失了时效性），
> **2 项已由 EM-007 修复**。**别再照着旧版去「修」不存在的问题。**

### ✅ 已修复（EM-007，commit `406260ac` + `39a7ac6b`）

| 旧版记的待修项 | 真实情况 |
| --- | --- |
| `guard.py:32` 注释声称「与权威清单长度做一致性断言」但断言不存在 | **属实**，已修：新增 `test_guard_count_matches_authoritative_list`，从权威清单**现场算长度**（不复写数字 9） |
| C-7 档位数字无任何机器校验 | **属实**，已修：新增 `tests/test_platforms.py`，6 类断言；`platforms.py` 成为唯一真源（这些断言后随 EM-010 阈值下线而重写，见下） |
| （新）只读 API「须注释说明用途」规则无测试守护 | 已修：`_DOC_ALLOWED` 豁免表 + `test_readonly_api_usage_is_documented` + 阳性对照 |
| **PLATFORM_MAX_BYTES / PLATFORM_MAX_EDGE 阈值常量** | **EM-010 已整体下线**（D-09 裁决「删」）：`platforms.py` 现仅剩 `Platform` 枚举 + `PLATFORM_SUFFIXES`；两标识符在 `.py` 零命中（`docs/05` 历史报告 3 处提及**明确不处置**——保 D-10 证据链） |
| `platforms.py` 注释「均用全角标点」却混半角 `,` | **已随 EM-010 注释重写消失**；现注释块明确「这是目标格式，不是压缩阈值」 |

### ❌ 从未存在（当时描述失实，已作废）

| 旧版记的「待修」 | 源码实测 |
| --- | --- |
| `test_smoke.py` 用 `__file__` 相对路径定位 | `test_smoke.py` **从未用 `__file__` 定位包目录**，走 `importlib.import_module`。唯一用它的是 `test_compliance.py:31`，且**本来就是** `resolve().parents[1]` 正确写法 |
| `test_compliance.py` 的 `_read_source` 无编码兜底 | **该函数从未存在**。读文件用的是 `read_text(encoding="utf-8")`，已显式声明编码 |
| 模块顶部重复 `import ast` | `grep -rn "import ast" src tests` **零命中** |

### ❌ 已作废的「阻塞」记录（2026-10-20 实测推翻）

| 记为阻塞的事 | 推翻证据 |
| --- | --- |
| **EM-003 需要管理员会话才能跑**（阻塞 7 天，2026-10-13 → 2026-10-20） | `OpenProcess(PROCESS_VM_READ)` 对 5 个 `Weixin.exe` **5/5 成功**；`ReadProcessMemory` 实读主模块 **100%**。Windows 上同用户进程默认可读，**根本不需要管理员** |
| **524/524 文件全部 `PermissionError`** | 假阳性：`Persist` 顶层 256 项**全是目录**，探测脚本对目录调 `Path.open()`——Windows 上目录 open 恒抛 `PermissionError`。递归真实文件 **1130 个，可读 1130 / 报错 0** |
| **微信文件数 1103「来源不明」（BL-7）** | 口径错配：1103 ≈ 递归真实文件数 1130，而 256 是**顶层目录数**。差27 个是记录后用户新增（QQ 侧同类：146 → 152）。**按不变量断言，不硬编码** |

> **教训原子（2026-10-20）**：
> ① **`PermissionError` 不等于「权限不足」**——先查被 open 的是不是目录/特殊文件，再查 ACL（`icacls` 找 DENY ACE）。
> ② **「实测阻塞」也要复核**。2026-10-13 的实测结论带着完整证据表和「524/524」这种精确数字，
>    看起来非常可信，但**只用一个未验证的探测脚本**。**精确的错误数字不等于正确的错误结论。**
> ③ **`\x30\x82` 这类 2 字节魔数在 68729 个候选里必然误命中**——判据魔数至少 3 字节，
>    且**必须多样本交叉复核**（同一 seed 解5 个文件，头部结构应一致）。
> ④ **同用户的其他进程内存默认可读**——别凭直觉假定「扫内存 = 要管理员」。

### 🟠 突变实验自身的三个坑（2026-10-20 EM-005-D，代价：3 次残留突变 + 1 次丢工作）

| 坑 | 症状 | 正解 |
| --- | --- | --- |
| **进程被杀会停在「突变已写入、未还原」** | 后台跑突变实验被 `TaskStop` 杀掉后，源码里留着 `page_budget = 10**9`、`hit_page_limit = False`、`return 0`——**下轮测试直接挂死或谎报成功** | 每组改动后**立刻**还原，或用 `git checkout --` 兜底；**跑突变前先 `git status` 确认干净**（脚本内置该检查） |
| **`git checkout -- .`会吞掉未提交的新测试** | 用它做还原后，新写的守护测试被一起回滚，突变重跑仍显示「零覆盖」，**误以为守护没生效** | 还原只针对**本轮要改的那一个文件**：`git checkout -- "$F"` |
| **没给 pytest 设超时的突变实验会自我挂死** | 「把上限改成 10⁹」这类突变让被测代码进无限循环，突变脚本跟着卡死 | `run_tests` 必须带 `timeout`，**超时按「有杀伤力」计** |

> ✅ **推荐形态**：把突变拆成**逐组独立的脚本调用**（`bash mutate.sh <组号>`），
> 每组自带 clean → mutate → test → restore。被杀最坏只留一组，且下次调用开头自动清场。

### 🟡 仍待处理

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | ~~**`_DOC_ALLOWED` 豁免粒度为「文件 + API」**~~ | ✅ **已由 P-5 解除（2026-10-20，commit `106602c`）**：升级为「文件 + **行号**」，`guard.py` 登记 `(49, 50)`。突变验证注入未注释的 `OpenProcess` → 2 失败，后门已堵。**EM-003 写 `ctypes` 只读代码前必读 §1 的注意事项** |
| 2 | **`guard.py:44` 注释措辞仍略强** | 注释写「与权威清单长度做一致性断言」，断言确实已存在，但表述未说明断言在 `tests/` 而非同文件——可读性小瑕疵，非缺陷 |

> 原 #3（`platforms.py` 半角逗号）已随 EM-010 注释重写消失，2026-10-19 划掉。
> BL-4（原 #1）已由 P-5 解除，2026-10-20 划掉。

### 🔴 语义提醒：两类约束，改动时不要一起动

**偏好**（「压多大」）与**正确性**（「压完还是不是原来的东西」）是两类约束。
EM-009 曾试图合并二者，已被 EM-010 取代（superseded）。现状：

- **微信侧（EM-003，2026-10-20 已交付）**：`cli collect-weixin` + GUI「导出微信表情」按钮；
  真机实测 **单文件 1103 / 整包切片 272 / 写出 1375 个**，扫内存 41 秒、总耗时 289 秒。
  ⭐ **「1103」的真相**：= 1138 个文件里成功导出的数量（1138 − 35 个 wxgf 转码失败）。
  它一直是某个中间统计口径，不是文件总数（文件总数 1138）。BL-7 就此闭环。

- **⚠️ 微信链路的可移植性（2026-10-21 实测核查）**：**不依赖本机任何专有值**。
  四步链路的输入**全部来自目标机器自己的微信**：
  ① `wxdir` 注册表读程序目录 → 逐级向上找 `xwechat_files`（无硬编码路径）；
  ② `wxscan` 运行时只读扫 `Weixin.exe` 内存取 seed（用完即弃、**绝不落盘**）；
  ③ `wxdecrypt` 派生 key = `md5(seed ‖ wxid ‖ "EMOTICON")` 前 16 字节、**IV = key**；
  ④ `wxcollect` 按 GIF/PNG **块结构**切分整包 + wxgf 转 GIF。
  **决定性实验**：固定 seed 换 wxid → key 变、解密结果变垃圾
  ⇒ key 绑定 (seed, wxid) 二元组，两者**随机器变**，换台机器各自成立。
  ⚠️ **TraceMemo 无关**：v3 方案书（内部归档，不随公开仓库分发）曾计划「M2 改为调用
  TraceMemo」，但**实际落地是自研**（扫内存 + AES），`wxcollect`/`wxscan`/`wxdecrypt`/`wxdir`
  对 TraceMemo 与 GUI **零引用**。GUI 第 1 页的「一键读取 TraceMemo 表情」按钮
  是**可选增强来源**（缺失即禁用、点击零写入），与解密链路无关。
  🚦 **有机器闸门守护**：`tests/test_compliance.py` 的 `MACHINE_SPECIFIC_LITERALS`
  断言 `src/` 的**代码行**里不得出现本机 wxid / 账号后缀 / 实测 seed / key /
  安装路径片段（注释与 docstring 里的举例**允许**）。
  突变实验 3 组：写死 seed → 红、写死路径 → 红、docstring 里举例 → 绿。
  **前提**：目标机器自己装了微信 PC 版**并在运行**（密钥在它自己的进程内存里），
  且微信仍是 4.x 的目录结构与加密方案——微信改算法/改结构则需重新校准。

- **档位阈值常量已全部下线**（EM-010，D-09 裁决「删」）：`platforms.py` 无
  `PLATFORM_MAX_BYTES` / `PLATFORM_MAX_EDGE`。唯一残留的偏好告警线在 `cli.py` 的
  `OVERSIZE_EDGE = 240`（超出时打印计数 + `WECHAT_OVERSIZE_HINT` 建议，**不拦截**）。
- **正确性判定**在 `guard.py` 的 `CORRECTNESS_RULES`（后缀与真实格式一致、动图帧数 > 1、
  透明度无黑边），**硬门槛，不降级**。

> 失效后果是判据：偏好失效只是文件偏大/多一句告警；正确性失效是**静默失败**
> （动图变静态、不报错）。平台自动压缩会把动图压成静态图（本机 46 个 GIF → 0 个动图），
> 实测依据详见 `docs/05-表情档位限制的必要性实测复核.md`（QQ 接受 16.6 MB GIF 原样保留
> ⇒「QQ ≤1MB」不成立；100 个静态图超 1MB 的 0 个 ⇒ 超标全部集中在动图）。


---

## 9. 环境坑（本机已复现多次）

| 坑 | 症状 | 正解 |
| --- | --- | --- |
| managed Python 缺工具 | `ModuleNotFoundError: No module named 'pytest'` | 用 `.venv/Scripts/python.exe`，别装全局 |
| Bash 调 Python 路径 | `can't open file 'd:\\c\\Users\\...'` | `cd /d/code/mymeme` 后用相对路径；**不要**给 Python 传 `/c/Users/...` |
| 双层 shell 吞变量 | `wsl.exe ... bash -lc "…$var…"` 里 `$var` 变空 | 脚本 Write 落盘 → `cp` 到 `/tmp` → `sed -i 's/\r$//'` → `bash` |
| Bash 改写正则反斜杠 | heredoc 里的 `\d` 变怪 | 脚本用 Write 工具落盘，**不用** heredoc |
| `pip install -e .` 假失败 | 返回非零/挂住但**实际已装上** | 别信退出码，查 `ls .venv/Lib/site-packages/` |
| Git 换行警告 | `LF will be replaced by CRLF` | Windows 上的正常提示，非错误 |
| 全局 grep 计数失真 | 把定义行当调用点 | 计数写明口径，或用 AST |
| **PyInstaller onefile 不传 `PYTHON*` 变量给子进程** | 引导进程丢弃父 shell 的 `PYTHONUTF8` / `PYTHONIOENCODING` / `PYTHONVERBOSE` / `PYTHONHOME`（`PYTHONHOME=/nonexistent` 运行不崩可证），exe 的 stdout 固定 cp936/GBK | 采集 exe 输出后**转码为 UTF-8** 再存档；`PYTHONUTF8=1` 仅对「venv python 直跑源脚本」有效 |
| **沙箱内 pytest 慢约 100 倍**（I/O 拦截） | 全量 278 个：沙箱外 9-14 秒，沙箱内单文件可到 20 分钟 | 超过 1 分钟的测试/构建命令直接申请沙箱外执行；沙箱内测得的时间类数字**先沙箱外校准再记录** |
| **Git Bash `/tmp` 传给 Windows 进程会错位** | `pip -w /tmp/x`（Windows 进程）把 `/tmp/x` 解析成 `<cwd 所在盘>:\tmp\x`，与 shell 内 `mkdir` 的 MSYS `/tmp` 不是同一目录，产物「消失」且不报错 | 传给 Windows 原生进程的路径参数一律显式 `C:/Users/<用户>/AppData/Local/Temp/...` |
| **`ctypes` 默认 `restype` 是 `c_int`，64 位句柄被截断** | `CreateFileW` 返回**假成功**（拿到的值其实是无效句柄），据此判断「能读」得到相反结论 | 调 64 位 API 前显式 `fn.restype = ctypes.c_void_p`；**返回值一律当指针看**，别用 `!= -1` 比 |
| **Windows 控制台 API 输出是 GBK** | `subprocess.run(..., text=True)` 抛 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd7` | 加 `errors="replace"`（并显式 `encoding=`）；`whoami /groups`、`cipher`、`attrib` 全是 GBK |
| **`ctypes.Structure` 内联定义 `type(...)` 会静默错位** | `VirtualQueryEx` 填出 `BaseAddress=0x0` / `RegionSize=0`，看起来像「没权限读」 | 结构体**单独 `class` 定义**并核对字段顺序与 ctypes 官方 ABI；先用 `EnumProcessModules` 等高阶 API 少踩结构体 |
| **目录 `open()` 恒抛 `PermissionError`** | 遍历微信目录时全部文件「读不了」，误判为权限不足 | `p.is_file()` / `p.is_dir()` 先分流；`Path.open()` 只喂真文件。详见 §8 教训原子① |
| **2 字节魔数在大量候选里必然误命中** | 68729 个 seed 候选里 2 个「命中」ASN.1 头，解开全是随机 | 判据魔数 ≥3 字节，且**多样本交叉复核**（同 seed 解多个文件，结构须一致） |
| **Edit 工具会把被编辑文件整体转成 CRLF** | 在纯 LF 文件上Edit 后 `git diff --stat` 显示整文件变更 | 本机 `core.autocrlf=true`；守护断言**行尾一致性**而非「一律 LF」 |
| **windowed exe 调子进程会弹控制台黑框** | 打包后（`console=False`）每次调 ffmpeg 都弹一个黑框又秒关；974 个 wxgf = **弹 974 次** | `subprocess.run(..., creationflags=subprocess.CREATE_NO_WINDOW)`。**本进程无控制台时，Windows 会给子进程分配新控制台**。实测对照：不传 flag → 5/5 次弹；传 flag → 0/5。由 `test_source_contains_no_console_window_bug_pattern`（AST 级）守护 |
| **抛出点与捕获点的异常类型可能对不上** | ffmpeg 超时抛 `TimeoutExpired`（`SubprocessError` 子类），而调用方只 catch `WxError` ⇒ **整轮导出崩掉**而不是「这个失败、其余继续」 | 转码层把 `TimeoutExpired` / `OSError` **统一转成 `WxError`**；守护 `test_timeout_becomes_wxerror_not_a_crash` |
| **本机环境变量有 `HTTPS_PROXY` 与 `https_proxy` 大小写重复** | `Start-Process -RedirectStandardOutput` 报「已添加项。字典中的关键字」错误；Git Bash 里 `exe > file` 输出全空 | 跑 Windows exe 改用 **PowerShell 的调用运算符 `& $exe $args`**（不经 Start-Process）；windowed exe 的 stdout 本来就不在标准流 |

---

## 10. 改代码前的自检

```bash
cd D:/code/mymeme
git status --porcelain -uall          # 必须空
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m ruff check src tests
```

## 11. 当前项目状态（2026-10-20 实测，勿凭记忆）

| 项 | 状态 |
| --- | --- |
| 工作包 | EM-001/002/004/006/007/008/010 **done**；EM-009 superseded；**EM-005-D 已交付**（GUI/CLI挂载 + 翻页）；EM-005 仍 `backlog`（待真机端到端复验）；**EM-003 阻塞理由已消解，待人工转 `todo`** |
| 测试基线 | **577 passed**（pytest 收集的测试实例数）/ ruff 零告警 |
| 微信（EM-003，**已交付**） | ✅ A-1 证实｜✅ P-5 豁免升级｜✅ `wxdir`/`wxscan`/`wxdecrypt`/`wxcollect` 四模块｜✅ CLI + GUI 挂载｜真机 **1375 个文件**（扫内存 41 秒 / 总 289 秒） |
| 孤岛模块 | **已消**（EM-005-D）：`douyin_collect` / `webp_gif` / `collect_flow` 现在都有调用方——`cli` 的 `collect-douyin` 子命令 + `gui` 的「采集抖音收藏表情」按钮 |
| 抖音端点 | `GET /aweme/v1/web/im/resource/list/aggregation`（`scenes=CUSTOM_STICKER_PAGE`，`aid=1128`），实测 41 条/页。**发现方法：grep 主站 IM chunk JS 比 WebSearch 猜端点可靠** |
| 翻页 | `douyin_collect.fetch_page(cursor)` → `StickerPage`；终止条件保守（无游标 / 空页 / `has_more` 假）；`MAX_PAGES=200` 防呆。**翻页结构未真机验证**（实测 41 条一页拿完，超页场景从未发生） |
| 采集命令 | `python -m emoji_migrator.cli collect-douyin [-o DIR] [--max-pages N]`；退出码 0 干净 / 1 有失败或空手 / 2 前置不满足 |
| `dist/` exe | **247,764,864 字节**，2026-10-21 11:16 构建（RUN-002，**含「反复弹黑框」修复**）。上一版 247,762,481 / 10-21 00:19 有该 bug，已作废 |
