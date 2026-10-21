# 架构说明（EM-001 定稿）

> **模块命名在 EM-001 定死，后续工作包（EM-002~EM-006）不得改名。**
> 改名视为破坏性变更，必须回到规划轮重新评估。

## 1. 包结构

`src/emoji_migrator/` 共 **17** 个模块文件（含 `__init__.py`），采用 `src/` 布局。
EM-001 定死的是其中 9 个（**不得改名**）；EM-004 起在此之上**追加**新模块
（追加即改 `tests/test_compliance.py` 的 `EXPECTED_MODULE_COUNT` 并注明来源，
该常量是防「静默加模块」的闸门）：

```
src/emoji_migrator/
├── __init__.py    # 包元信息（__version__），不导入任何子模块
├── config.py      # 跨模块共享常量：档位枚举、档位尺寸上限、仓库路径、后缀集合
├── platforms.py   # 平台枚举与各平台目标格式（不含阈值，见 §6）
├── sniff.py       # 文件真实格式嗅探（扩展名声称 vs 实际）
├── normalize.py   # 格式与尺寸修正
├── dedupe.py      # 感知哈希（pHash）去重
├── compress.py    # 按档位压缩体积
├── guard.py       # 合规守门阈值与策略元数据
├── cli.py         # 命令行入口
├── qqdir.py       # EM-004：QQ 表情目录探测（注册表只读 + 两级路径链）
├── gui.py         # EM-004：PySide6 三步导入向导（只做编排与呈现）
├── douyin_auth.py # EM-005-B：抖音扫码登录（QWebEngineView + DPAPI cookie 加密）
├── webp_gif.py    # EM-005-C：WebP→GIF 转换（alpha 掩模防黑边 + 自适应量化）
├── douyin_collect.py # EM-005-C：抖音收藏采集（curl_cffi 指纹 + 401 探测 + 签名器）
├── abogus.py      # D-17（EM-005）：a_bogus 签名（f2 main 0.0.3 vendor，Apache-2.0）
├── _gmssl_sm3.py  # D-17：SM3 国密哈希（gmssl 3.2.2 vendor，BSD；abogus 依赖）
├── _gmssl_func.py # D-17：SM3 辅助工具函数（gmssl 3.2.2 vendor，BSD）
└── guides/        # EM-004：图文指引资源（wechat/qq/douyin 各 step1~3.png，非 Python）
```

## 2. 职责

| 模块 | 职责 | EM-001 状态 |
| --- | --- | --- |
| `config.py` | 全局常量：`QualityTier`、`TIER_MAX_EDGE`、`SUPPORTED_SUFFIXES`、仓库路径 | 已有可用常量 |
| `platforms.py` | `Platform` 枚举（微信/QQ/抖音/source）、`PLATFORM_SUFFIXES`（目标格式）。**不含任何阈值** | 已有可用常量；平台枚举与目标格式见 §6 |
| `sniff.py` | 判定文件真实格式，产出 `SniffResult`（含 mismatch 标记） | 仅类型与签名，函数抛 `NotImplementedError` |
| `normalize.py` | 依嗅探结果与平台约束产出 `NormalizePlan` 并执行 | 仅类型与签名，函数抛 `NotImplementedError` |
| `dedupe.py` | pHash 距离聚类，产出 `DedupReport` | 仅类型与签名，函数抛 `NotImplementedError` |
| `compress.py` | 按 `QualityTier` 压缩，产出 `CompressResult` | 仅类型与签名，函数抛 `NotImplementedError` |
| `guard.py` | 合规策略 ID、阈值常量；**不复述被禁 API 标识符** | 已有可用常量 |
| `cli.py` | `argparse` 入口，当前子命令 `compliance` 可运行、`scan` 返回未实现退出码 | 最小可运行骨架 |
| `qqdir.py` | **EM-004 新增**。QQ 表情目录探测：注册表只读（`winreg`）→ 安装目录 → 两级路径链；找不到返回空列表 + 中文报错文案，不猜不遍历（D-07 裁决 B） | EM-004 已实现 |
| `gui.py` | **EM-004 新增**。PySide6 三步向导：选来源 → 选平台 → 导出 + 图文指引。**不实现任何算法**：导出复用 `cli._normalize_dir`，探测复用 `qqdir` | EM-004 已实现 |
| `douyin_auth.py` | **EM-005-B 新增**。抖音扫码登录：`DouyinAuthWindow` 内嵌 `QWebEngineView` 走官方登录页，`cookieAdded` 信号捕获 session；cookie 经 DPAPI（`CryptProtectData`）加密落 `%APPDATA%\emoji-migrator\`，加载全失败面静默 `None`。**零日志**（不打印 cookie 值） | EM-005-B 已实现；采集侧留 EM-005-C |
| `webp_gif.py` | **EM-005-C 新增**。WebP→GIF：alpha 掩模合成白底（消半透明黑边）+ 自适应量化 + 动图帧/loop 保留。Pillow 12.3 实测边界（`Quantize` 枚举、逐帧 duration 读侧部分不可得）记于模块 docstring | EM-005-C 已实现 |
| `douyin_collect.py` | **EM-005-C 新增**。收藏采集库层：curl_cffi Chrome145 指纹 + cookie 注入式（不 import `douyin_auth`）+ `SessionExpired`（HTTP 401，D-15）+ 下载域名白名单。默认签名器为 vendor 的 `abogus`（D-17）；端点未实测（A-4），宽容提取为过渡策略 | EM-005-C 已实现；A-4 真机联调待真人 cookie |
| `abogus.py` | **D-17 vendor**（2026-10-20 人工授权）。f2 main 0.0.3 的 a_bogus 签名算法，除 1 行 import 指向包内 gmssl vendor 外逐字保留；provenance 块强制（测试守护）。整体豁免 ruff（pyproject per-file-ignores） | 已 vendor；服务端校验未实测（A-4） |
| `_gmssl_sm3.py` / `_gmssl_func.py` | **D-17 vendor**。SM3 国密哈希及辅助函数（gmssl 3.2.2 wheel 原样解出，仅 import/`__all__` 适配）。正确性由 GB/T 32905 标准向量测试钉死 | 已 vendor |

## 3. 依赖方向

```
        ┌──────┐
        │  gui │   ← EM-004 导入向导（编排层，无算法）
        └──┬───┘
     ┌─────┼──────────┐
     ▼     ▼          ▼
  ┌─────┐ ┌─────┐ ┌──────────┐
  │ cli │ │qqdir│ │douyin_auth│
  └─────┘ └─────┘ └──────────┘
     │     EM-004  │  EM-005-B：登录窗 + DPAPI
     │     只读探测│  （叶子：仅 stdlib + PySide6 + ctypes，
     │     仅 winreg│   不 import 任何包内模块）
     │            │
  ┌──┴────────────┴──┐
  ▼     ▼     ▼      ▼
sniff normalize dedupe compress
     └─────┬─────┴──────┘
           ▼
   config  platforms  guard
           │
           ▼
        __init__  （不导入子模块）
```

规则：

1. **单向依赖**：箭头只能从上往下，不得反向或成环。
2. `cli.py` 是唯一允许编排多个业务模块的地方；业务模块之间不互相调用
   （如 `normalize` 不 import `dedupe`），需要串联时由 `cli` 显式编排。
3. `config.py` / `platforms.py` / `guard.py` 是**叶子依赖**，不 import 任何包内模块。
   `qqdir.py` 同为叶子（只 import 标准库），与 `sniff`/`normalize` 同级。
   `douyin_auth.py` 亦为叶子（只 import 标准库 + PySide6 + `ctypes`）——
   **它调 `CryptProtectData`/`CryptDecryptData`（DPAPI）做本机加密，属加密用途而非注入，
   不在 `tests/test_compliance.py` 的 9 项被禁 API 清单内**；按 BL-4 精神已注释用途。
4. `__init__.py` 只放 `__version__` 等元信息，**不导入子模块**，
   以保证 `import emoji_migrator` 不触发整包加载、无循环导入风险。
5. `guard.py` 的常量不含任何被禁 API 标识符——否则守门常量会触发它自己守的闸门。
6. **`gui.py` 是 GUI 侧编排点**（复用 `cli`，不重写串联逻辑），是唯一允许 import
   `douyin_auth` 的地方——业务模块之间仍不互相 import。
7. **EM-004 的 `gui.py` 是编排层，位于 `cli` 之上**：`gui → {cli, qqdir, config,
   platforms, sniff}`。它可以直接 import 叶子模块（不经 `cli`），仍属向下、
   不成环；反向（业务模块 import `gui`）被禁止——`import emoji_migrator`
   不得因此拖入 PySide6。
8. **D-17 vendor 叶子（2026-10-20）**：`abogus.py` / `_gmssl_sm3.py` / `_gmssl_func.py`
   是无包内依赖的叶子（只 import 标准库互相），业务模块 `douyin_collect` 允许
   import 它们（向下依赖叶子，与「业务模块 import config」同构）；
   反向（vendor import 业务模块）禁止。vendor 文件必须带 provenance 头
   （`tests/test_abogus.py` 守护），其正确性靠 SM3 标准向量测试而非 lint
   （整体豁免 ruff，见 pyproject per-file-ignores）。

## 4. 合规基线

`tests/test_compliance.py` 静态扫描 `src/`，断言 9 项被禁 API 标识符零出现
（见该文件的 `FORBIDDEN_APIS` 权威副本）。只读 API `ReadProcessMemory` /
`OpenProcess` 允许出现，但使用处必须注释说明用途。

### 4.1 扫描能力边界（EM-007 补写，务必先读）

上面的「静态扫描」是**纯文本子串扫描**，它的能力边界必须写明，否则会产生虚假的安心感：

1. **不编译、不做 AST 层调用检查。** 扫描器读的是文件文本，对每个被禁标识符做
   `api in text` 子串判断。它不知道什么是调用、什么是字符串字面量、什么是注释。
2. **只拦人工写出的字面量，不拦运行时动态拼接。** 例如用字符串拼接规避标识符
   （`"Write" + "ProcessMemory"`）不会被发现。绕过方式成本极低。
3. **子串锚定是刻意保留的。** 工作包曾建议收紧为词边界匹配（如 `re` + `\b`），
   EM-007 评估后**决定不改**：`api in text` 会把 `WriteProcessMemoryX` 这类
   真实代码里几乎不会出现的标识符误命中，收紧锚定解决的是这个低频误报，
   代价却是**收紧后的模式一旦写错，会漏检真正的被禁 API**。
   保护力优先于误报率，故维持现状。
4. **因此「测试全绿」不等于「已证明合规」。** 全绿只说明：**当前源码文本里
   没有出现这 9 个标识符的字面量**。它不构成合规证明，合规判断仍需人工审查
   `ctypes` / `win32` / `subprocess` 相关的新增代码。

### 4.2 已有断言与其真实状态

| 规则 | 守护它的测试 | 状态 |
| --- | --- | --- |
| 9 项被禁 API 在 `src/` 中零出现 | `test_forbidden_api_absent_from_src`（逐个参数化） | 有效 |
| `src/` 存在且模块数为 `EXPECTED_MODULE_COUNT`（EM-001 的 9 + EM-004 追加的 2） | `test_source_tree_shape_is_intact` | 有效 |
| 扫描器本身能命中（阳性对照） | `test_scanner_detects_forbidden_api_in_a_file` | 有效 |
| `guard.FORBIDDEN_API_COUNT` 与清单长度一致 | `test_guard_count_matches_authoritative_list` | **EM-007 新增**（此前是空承诺：全仓零引用该常量，删掉 1 项清单仍全绿） |
| 只读 API 使用处必须注释说明用途 | `test_readonly_api_usage_is_documented` | **EM-007 新增**（此前 `guard.py` 注释与本节都声称有此规则，但无任何测试守护） |
| 用途注释判定逻辑本身有效（阳性对照） | `test_doc_comment_rule_detects_undocumented_usage` | **EM-007 新增** |
| 豁免表恰好覆盖，不得有多余项 | `test_readonly_api_exemption_table_has_no_extra_entries` | **EM-007 新增** |
| 豁免表键必须是真实源文件 | `test_readonly_api_exemption_table_keys_are_real_files` | **EM-007 新增** |

只读 API 的豁免表是本文件顶部的 `_DOC_ALLOWED`，当前唯一一项是 `guard.py`——
那里是白名单的**声明处**，标识符按定义必须出现，不属于「使用」。

**豁免表的设计要点：必须恰好覆盖，不能多也不能少。** 这让「往豁免表里加一项
来给自己开豁免」这条逃逸路径被堵死——只有当被豁免的文件确实存在该 API 的
未注释出现点时，登记才被允许。

### 4.3 读取编码策略（EM-007 显式决策）

所有源码读取均为 `read_text(encoding="utf-8")` **严格解码**，遇非法字节即抛错停下。
这是有意为之：合规扫描遇非法字节时「报错」比「继续扫」正确。
工作包建议的 `errors="replace"` **未被采纳**——它会把非法字节替换成 `�`，
一旦被禁标识符恰好跨越非法字节就会被**静默漏检**，那是降低保护力而非容错。

## 5. 后续工作包的落点

- **EM-002**：在 `sniff/normalize/dedupe/compress` 中填入算法，补 `fixtures/` 合成素材生成脚本。
- **EM-003**：微信只读解密；只能使用 `guard.ALLOWED_READONLY_APIS` 中的 API，
  且每一处使用都必须带注释说明用途（见 §4.2 的断言）。
- **EM-004**：GUI（PySide6 三步向导 `gui.py`）+ QQ 目录探测 `qqdir.py` + 随包图文指引
  `guides/`。**只做编排与呈现**：导出复用 `cli._normalize_dir`，探测走 D-07 两级链，
  业务逻辑零重写。使用与验收细节见 `docs/06-EM-004-GUI导入向导说明.md`。
- **EM-005**：实测抖音平台约束。注意落点已变——`platforms.py` 的阈值已由 EM-010 下线，
  EM-005 只会产出**目标格式**层面的结论（是否 WebP 优先），**不回填任何体积/像素数字**
  （见 §6）。若实测要求新增阈值，须先推翻 `docs/05-…` 的证据并回规划轮。
- **EM-006**：打包配置（console script 已在本包声明入口点）。⚠️ 需把
  `src/emoji_migrator/guides/` 的 PNG 纳入包数据（package-data），否则打包后指引截图缺失——
  `pyproject.toml` 当前只允许 EM-004 追加 PySide6 依赖声明，该改动须在 EM-006 授权后进行。

## 6. 平台枚举与目标格式（EM-010 起；**本节不含任何阈值**）

> **EM-007 曾把本节写作「档位数字的单一真源」，EM-010 已推翻该前提并重写。**
> 原先收录的体积上限与单边像素常量已从 `platforms.py` 删除，`tests/test_platforms.py`
> 中对应的阈值断言同步移除。历史数值与「曾经有什么阈值」的记录**只保留在**
> `docs/05-表情档位限制的必要性实测复核.md`（历史实测报告，**非活引用**）。

本产品**不做压缩**，而阈值的唯一用途是「压到多大」——不压就没有阈值可谈。
下线的实测依据（完整数据见 `docs/05-表情档位限制的必要性实测复核.md`）：

- QQ 在自己的 `personal_emoji/Ori` 目录里**原样保留了单个达 16.6 MB 的表情**，
  说明先前那套体积上限**从来不是平台的真实限制**；
- 本机 46 个多帧 GIF 在 QQ 自动生成的 `Thumb` 目录里**全被压成静态单帧**——
  平台自动压缩会把动图压死，那恰恰是本产品**不该**做的事；
- 网络上的平台规格不可作判据：检索到的 5 个来源给出三套矛盾数字，且全部来自
  表情压缩工具的营销页，非平台文档。

因此数字是**删除**而非降级为「推荐值」。若未来要恢复阈值，必须先推翻那份实测报告。

### 6.1 现存的两张真源

| 平台 | 目标格式 | 依据 |
| --- | --- | --- |
| `WECHAT` | `.gif` | EM-001 沿用值，需求基线 C-7 未限定微信导出格式 |
| `QQ` | `.png` | C-7 未规定，EM-001 沿用值 |
| `DOUYIN` | `.webp` | C-7 只规定「WebP 优先」，未给其他候选 |
| `SOURCE` | `.png` | 非真实平台，C-7 未规定；输入侧兜底标识 |

⚠️ **`PLATFORM_SUFFIXES` 是「转成什么常规格式」，不是压缩阈值。** 读取时不得把它
当作体积或尺寸上限的来源——本模块已无任何阈值可配。`source` 是输入侧兜底标识，
不是真实平台。

### 6.2 断言台账（`tests/test_platforms.py`）

| 规则 | 守护它的测试 | 状态 |
| --- | --- | --- |
| `Platform` 成员集合恰为 wechat/qq/douyin/source | `test_platform_members_are_exactly_the_four_declared_ones` | **EM-010 新增**（EM-005 依赖 `DOUYIN`，删成员必须红） |
| 每个 `Platform` 成员在目标格式表里有条目（单向） | `test_every_platform_has_an_entry_in_every_table` | **EM-010 收窄**（EM-007 覆盖三张表，现只剩一张） |
| 目标格式表无悬空键（反向） | `test_no_orphan_entries_referencing_removed_platforms` | **EM-010 收窄**（同上，EM-007 断言逻辑保留） |
| 微信档目标格式为 `.gif` | `test_wechat_suffix_is_the_em001_carried_over_target_format` | **EM-010 改写**（原断言含已被删的 QQ 像素值） |
| 抖音档 WebP 优先 | `test_douyin_prefers_webp` | 有效（格式选择，非阈值） |
| ~~平台体积上限 / 单边像素上限~~ | —— | **EM-010 下线**（随常量删除，无守护断言） |

阈值类断言的消失不是保护力下降，而是**规格本身被推翻**：被守护的常量已不存在。
现存断言全部做过突变实验，逐组结果见该次提交的说明：删枚举成员 / 删表条目 /
改格式串 / 注入悬空键，四种改坏方式**均必须变红**。其中「注入悬空键」在加断言前
只会崩在报错路径上（`AttributeError` 而非 `AssertionError`），已据此修正诊断代码。

### 6.3 边界：档位侧数字不在本模块

`config.py` 的 `QualityTier` / `TIER_MAX_EDGE` 属**压缩**档位域，按 D-12 裁决
**本轮不删**（`compress.py` 骨架仍 import 它们）。它与本模块的「目标端平台」
是两件事，改动时不要一起动。

