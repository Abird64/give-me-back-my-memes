# 11 · 前端重构：WebEngine 壳 + 《把我的表情还回来》

> EM-011（2026-10-21 人工授权）。本文是前端重做的**落地记录**：
> 为什么换壳、怎么换、踩了哪些坑、怎么防复发。

## 1. 结论

前端从**纯 PySide6 向导**换成 **PySide6 壳 + QtWebEngine 承载 HTML**，
产品名定为 **《把我的表情还回来》**（副标 GIVE ME BACK MY MEMES）。

三个决定性理由：

1. **视觉语言**：新风格是孟菲斯/波普拼贴——3px 纯黑描边、**硬边偏移阴影（无模糊）**、
   文字描边（`-webkit-text-stroke`）、半调网点背景、斜置拼贴、自定义展示字体。
   这些在 **QSS 里几乎无法实现**（QSS 不支持伪元素样式、网点渐变、`text-stroke`、
   外部字体打包）。HTML/CSS 一行就够。
2. **依赖已就位**：`douyin_auth`（EM-005-B）已在用 `QWebEngineView` 打开抖音登录页；
   PySide6 6.12.0 的 `QtWebEngineWidgets` / `QtWebEngineCore` 实测均可导入。
3. **旧前端的逻辑硬伤无法靠调样式解决**（见 §2），必须换信息架构。

## 2. 旧前端的 3 个逻辑硬伤（依据 `src/emoji_migrator/gui.py`）

| # | 硬伤 | 证据 |
|---|------|------|
| 1 | **来源/平台页错位** | `_start_weixin_export`（微信导出）、`_start_collect`（抖音采集）本质是「获取来源」，却塞在第 2 步「选平台」页。代码注释自己承认矛盾：*"放在第 2 页顶部而不是来源页"* |
| 2 | **「平台」概念双向滥用** | 微信/抖音既当**来源**（采集）又当**目标**（导出），向导只给一个「选平台」步骤，输入轴与输出轴被揉成一团 |
| 3 | **「质量档位」是假控件** | L566/L283 明写 *"当前版本不做压缩，档位仅作记录"*；`EM-010` 已下线档位常量。留着就是骗用户 |

视觉层：旧 `gui.py` 几乎零样式（只有 `WizardStyle.ModernStyle` + 一处 `font-weight:bold`）→ 裸 Qt 默认皮肤。

## 3. 新信息架构：相册式三阶段

```
导入三端  →  我的相册  →  选择导出
（来源轴）   （整理）      （输出轴）
```

- **来源轴**：微信导出 / QQ 扫描 / 抖音采集 / 拖入文件夹 —— 全在第 1 步。
- **相册**：所有表情进贴纸册页，可按平台、按分类（册页）筛选、搜索、多选。
- **输出轴**：选目标端 → 导出 → 图文指引。「质量档位」**已删除**，
  改为只显示真实数字（文件数 / 去重数 / 目标端格式）。

**面向小白的关键决策**：三端的格式差异说明**默认隐藏**，只在悬浮/聚焦时用气泡显示
（`.tipbox` 真实 DOM 元素，不用 `::after` + `attr()`）——小白不必先读技术细节。

## 4. 文件清单

| 路径 | 作用 |
|------|------|
| `src/emoji_migrator/webui.py` | WebEngine 壳 + `WebBridge`（QWebChannel 桥） |
| `src/emoji_migrator/webui/index.html` | 前端页面（单文件，图标与 CSS 全内联） |
| `src/emoji_migrator/webui/fonts/` | 展示体 woff2（**按实际用字裁剪，82KB / 5 个文件**）+ `fonts.css` |
| `tests/test_webui.py` | 30 条守护测试 |
| `design-prototype/emoji-migrator-mockup-v3.html` | 设计原型（评审用，字体走 CDN） |
| `design-prototype/.impeccable.md` | 设计上下文（impeccable skill 产物） |

**模块数闸门**：`tests/test_compliance.py` 的 `EXPECTED_MODULE_COUNT` 由 `9 + 13`
改为 `9 + 14`（新增 `webui.py`）；`tests/test_module_all.py` 的 `MODULE_NAMES`
同步加 `emoji_migrator.webui`。两处都是机器闸门——**故意改小会立刻报红**。

## 5. 平台图标：官方矢量（不用 emoji）

原型阶段用 `💬🐧🎵` 冒充平台 logo，正式版全部换成官方形象：

| 平台 | 来源 | 许可 |
|------|------|------|
| 微信 | simple-icons `wechat`（双气泡） | CC0-1.0 |
| QQ | simple-icons `qq`（企鹅） | CC0-1.0 |
| 抖音 | Wikimedia Commons `Douyin Baike logo.svg`（音符） | **Public domain**，`AttributionRequired=false`；`Restrictions=trademarked`（商标权归字节跳动，**指代性使用**为正常用法） |

图标**直接内联**进 HTML（`currentColor` 染色，抖音用「圆底 + 白音符」圆章形态）。
独立源文件留在 `design-prototype/assets/icon-{wechat,qq,douyin}.svg`，便于二次编辑。

## 6. 实测踩的坑（每个都有守护测试）

### 6.1 SVG 不能用正则拆（3 个连环坑）

抖音那张官方 SVG 的 path 在多层 `<g transform="matrix(...) scale(...)">` 里，
且是「外圈圆 + 内音符」双子路径，靠 `fill-rule:evenodd` 挖空。

| 坑 | 症状 | 修法 |
|----|------|------|
| 正则只扒 `<path>` | **丢掉父级 transform** → 坐标不缩放 → 图形跑出 viewBox → **渲染成空白** | 用 `xml.etree` 解析，保留完整层级 |
| 剥 `style` 时用 `startswith("fill")` | **误伤 `fill-rule:evenodd`** → 音符被填实 → 视觉上「消失」 | 精确匹配属性名，只丢 `fill` / `fill-opacity` |
| 沿用源文件 viewBox | 图形是视口的 **1086 倍** | 让浏览器实测 bbox，**反推**紧贴 viewBox（`-1.744 -1.700 29.929 29.929`） |

### 6.2 QWebChannel 桥接连不上（3 个连环坑）

| 坑 | 症状 | 修法 |
|----|------|------|
| 没引 `qwebchannel.js` | `window.QWebChannel` 全局不存在 | `<script src="qrc:///qtwebchannel/qwebchannel.js">` |
| 只引脚本没 `new` | 脚本只定义全局类，**不握手** | `new QWebChannel(qt.webChannelTransport, cb)` |
| 从 `window.bridge` 取对象 | **Qt 6 不把对象挂到 window** → 永远 undefined | 取 `channel.objects.bridge`，再统一存到 `window.bridge` |
| 槽调用当同步值用 | QWebChannel 槽返回 **Promise** → 拿到 `[object Promise]` | 全链路 `async/await` |

### 6.3 无 GPU 的机器上页面永远不加载

实测在无 GPU 环境（远程桌面 / 虚拟机 / 老显卡）：
Chromium 反复报 `Failed to create GLES3 context` → `kFatalFailure`，
表现为 `loadFinished` 一直是 False、**窗口一片空白**。

修法：`_enable_software_rendering()` 在 **QApplication 创建之前**设
`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu --disable-gpu-compositing --disable-software-rasterizer`
（Chromium 进程只在 profile 初始化时读一次，事后设置无效）。

### 6.4 中文平台名 vs 英文枚举

`Platform` 枚举的值是**英文**（`"wechat"` / `"qq"` / `"douyin"`），
而前端传的是**中文**（"微信"/"QQ"/"抖音"）→ `Platform("微信")` 抛 ValueError →
**点「开始导出」必然失败**。修法：`_PLATFORM_BY_LABEL` 中文→枚举映射
（英文标识也照收）。

### 6.5 导出目录自我覆盖

`cli._normalize_dir(source, out_dir, ...)` 的入参语义是「从 source 归一化到 out_dir」。
传同一个目录会让管线**自我覆盖**。目录约定：

```
<out>/collected/<平台>/    采集产物
<out>/export/              导出结果
```

## 7. 离线与打包

- 前端**不联网**：展示体字体已按实际用字**裁剪**（ZCOOL KuaiLe 93 个分片只命中 4 个），
  合计 **82KB / 5 个文件**；正文中文字用系统字体（微软雅黑 / PingFang）。
  构建脚本在 `design-prototype/` 下（`build_fonts_slim.py` 思路已固化在测试里）。
- `pyproject.toml` 的 `package-data` 增列 `webui/*.html` / `webui/fonts/*.woff2` /
  `webui/fonts/*.css`（EM-011 人工授权 2026-10-21）。
- ⚠️ **打包时**还需把 `webui/` 收进 PyInstaller 的 spec（EM-006 域，需另行授权），
  否则 exe 里没有前端页面。

## 8. 验证结果

| 项 | 结果 |
|----|------|
| `pytest tests` | **619 passed** |
| `ruff check src tests` | All checks passed |
| 真机启动（WebEngine） | `loadFinished: True` |
| 端到端桥接 | `OUTDIR=C:\Users\<用户名>\emoji-migrator-out\20261021-132418 \| LOGIN=已登录 \| RULES_LEN=112` → **PASS** |
| 页面结构 | `tabs:3 views:3 icons:6 tipbox:3 hasBridge:"object"` |
| 滚动性能（修复后） | 728 贴纸 / 页高 15537px：`avg 16.0ms` · `p95 17.1ms` · `>32ms 帧 0` · 长任务 1 |

## 9. 性能：滚动卡的定位与修复（2026-10-21）

用户反馈「滑动滚动的时候非常卡」。在**真实 Qt WebEngine** 内构造 728 张贴纸 /
页高 15537px 的长页面，用 `PerformanceObserver(longtask)` + 逐帧间隔采样，
做了 5 组 A/B：

| 变体 | 平均/帧 | p95 | >32ms 帧数 | 最长任务 |
|------|---------|-----|-----------|---------|
| 基线（`background-attachment:fixed`） | **62.4ms** | 130.7ms | 105 | 176ms |
| 去掉 `.deco` 背景拼贴层 | 73.7ms | 156.5ms | 115 | 188ms |
| **去掉 `background-attachment:fixed`** | **16.1ms** | 17.8ms | 1 | 136ms |
| 去掉贴纸随机旋转 | 38.2ms | 128.6ms | 62 | 160ms |
| 修复后（渐变挂 `html`） | **16.0ms** | 17.1ms | **0** | 130ms |

**结论：`background-attachment: fixed` 是唯一主因，慢 3.9 倍**。
fixed 背景在每帧都要重做一次整屏三段渐变合成；挂在 `html` 上时渐变按
**视口**尺寸计算、由 canvas 铺满整个滚动区域，视觉无差别但不参与逐帧重合成。
装饰层和贴纸旋转都**不是**瓶颈，保留。

> 踩过的坑：无头 chrome 的 `--virtual-time-budget` 与 rAF 循环互锁，
> 虚拟时间下页面永不推进 → 测不出东西；`rAF` 在不可见/离屏窗口会被节流，
> 必须用 `setInterval` 驱动；Qt 的 `runJavaScript` 回调**不解 Promise**，
> 页面内 Promise 必须写进 `window.__result` 再由 Python 轮询取。

## 10. 交互修正：抖音登录/采集分离 + 长任务反馈

用户反馈「点抖音不弹扫码窗，直接导出了」——**属实，是真 bug**：

- 旧 `gui.py` 里「登录抖音」和「采集收藏」本来就是**两个独立控件**；
  合并前端时被塞进同一个按钮，而 `webui.py` 只暴露了 `douyin_login_state`
  （只读本地缓存）——**压根没有弹扫码窗的槽**。
  结果：未登录时点按钮只得到一行"请先扫码登录"的提示文字，永远不弹窗；
  已登录时又直接开采集，用户完全看不到发生了什么。
- 微信「点了反应不大」也是真的：导出实测 **289 秒**，原先点下去按钮只是禁用、
  文字一闪就没了，既不知道在跑什么也不知道要跑多久。

修法：

| 改动 | 位置 |
|------|------|
| 新增 `WebBridge.douyin_login()` 槽，弹 `DouyinAuthWindow`（复用 EM-005-B，不重写） | `webui.py` |
| 抖音卡片拆成「扫码登录」+「采集收藏表情」两个按钮，未登录时采集按钮禁用 | `index.html` |
| 新增 `douyinLogin()` / `refreshDouyinUi()`，登录成功后 emit `douyinLogined` 解锁采集 | `index.html` |
| 长任务统一走 `markRunning(k)` / `clearRunning(k)`：按钮变主色 + 转圈 + 进度条 + 已用时/预计 | `index.html` |
| 主按钮改按 id 取（`MAIN_BTN`），**不再用 `card.querySelector('.btn')`** | `index.html` |

⚠️ 最后一个坑：抖音卡片有两个 `.btn`，`querySelector('.btn')` 抓到的是
**第一个（登录）**，于是点「采集」禁用的却是登录按钮。已由
`test_platform_main_button_uses_id_not_query_selector` 守住。

## 11. 未做 / 下一步

- **真机功能验证**：微信导出、抖音采集、QQ 扫描三条采集链路**仍未在真机上跑过**
  （需要真实微信/QQ/抖音环境）。本轮只验了桥接连通、页面渲染与滚动性能。
- **相册的贴纸内容**：当前是占位 emoji；真实落地要从采集产物目录读取缩略图。
  （注意：贴纸数量上去后仍要复测滚动性能，本轮的性能结论是在 728 张的量级上测的。）
- **PyInstaller 打包**：`webui/` 资源未进 spec，需 EM-006 域授权。
- **旧 `gui.py`**：仍保留（未被 `webui` 取代）。要不要保留双入口，待人工裁决。
- **`docs/08-使用说明（小白版）.md` 已删除**：它写的是旧向导界面
  「选来源 → 选平台+质量档位 → 导出」。面向用户的使用说明改由仓库根 `README.md` 承担。
