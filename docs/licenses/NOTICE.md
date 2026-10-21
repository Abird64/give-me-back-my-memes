# THIRD-PARTY NOTICE —— 把我的表情还回来分发包

本目录随分发包一起提供：**本产品 exe（`把我的表情还回来.exe`）内捆绑
（bundle）的第三方组件**的许可证文本与来源信息。本文件逐条列明
「组件 → 许可证 → 官方源码地址」，并说明 PySide6 / shiboken6（LGPL）
的分发义务履行情况。

> 措辞说明：以下「已满足」均为**事实陈述**（描述本分发包的实际状态），
> 不构成任何法律建议。

## 1. bundle 内第三方组件清单

| 组件 | 用途 | 许可证 | 官方源码 / 项目地址 |
| --- | --- | --- | --- |
| PySide6（essentials + addons，6.12.0） | GUI 框架（Qt for Python） | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only（三选一，本项目按 **LGPLv3** 使用）；商业许可见同目录 `LicenseRef-Qt-Commercial.txt` | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| shiboken6（6.12.0） | PySide6 的 Python 绑定生成器/运行时 | 同 PySide6（LGPLv3） | https://code.qt.io/cgit/pyside/shiboken6.git/ |
| Pillow（12.3.0） | 图片读写（嗅探 / 演示素材 / 帧数校验） | HPND（MIT-CMU 历史许可），见 `pillow-LICENSE.txt` | https://github.com/python-pillow/Pillow |
| numpy（2.5.4） | Pillow / imageio 的数组依赖 | BSD-3-Clause，见 `numpy-LICENSE.txt` | https://github.com/numpy/numpy |
| imageio（2.38.1） | 图像 IO 抽象层 | BSD-2-Clause，见 `imageio-LICENSE.txt` | https://github.com/imageio/imageio |
| imageio-ffmpeg（0.6.0） | 提供 ffmpeg 二进制与路径解析 | BSD-2-Clause（imageio 部分），见 `imageio_ffmpeg-LICENSE.txt` | https://github.com/imageio/imageio-ffmpeg |
| ffmpeg（随 imageio-ffmpeg 分发的 `ffmpeg-win-x86_64-v7.1.exe`） | 未来 GIF/视频转码（EM-003 起） | 该二进制由 imageio-ffmpeg 官方构建分发；ffmpeg 本体按 LGPLv2.1+ / GPL 多许可提供（具体构建选项以上游发布页为准） | https://ffmpeg.org/ ；https://github.com/imageio/imageio-ffmpeg |
| curl_cffi（chrome145 TLS 指纹） | 抖音采集 HTTP 客户端（EM-005） | MIT | https://github.com/lexiforest/curl_cffi |
| f2 · abogus.py（单文件 vendor，main 0.0.3） | 抖音 a_bogus 查询签名（EM-005 D-17） | Apache-2.0，见 `f2-abogus-Apache-2.0.txt` | https://github.com/Johnserf-Seed/f2 |
| gmssl · sm3.py / func.py（两文件 vendor，PyPI 3.2.2） | SM3 国密哈希（abogus 签名算法依赖，EM-005 D-17） | BSD（上游 PyPI METADATA 声明；上游未随包分发全文，登记说明见 `gmssl-BSD.txt`） | https://github.com/duanhongyi/gmssl |
| pycryptodome（3.24.1） | AES-128-CBC 解密（微信表情解密，EM-003） | 公有领域 + BSD 2-Clause（双许可），见 `pycryptodome-LICENSE.rst` | https://github.com/Legrandin/pycryptodome |

> ⚠️ **pycryptodome 是 EM-003 才真正进包的组件**（2026-10-21 实测）：
> 此前 exe 里 `Crypto` 字符串**命中 0 次**——它虽已在 `pyproject.toml` 的
> 依赖里，但因当时无代码 import，**从未被打进过分发包**。
> EM-003 的 `wxdecrypt` / `wxscan` 首次 import 它，故本次补登记。
> 该包许可极宽松（公有领域 + BSD-2-Clause），**无 copyleft 义务**。

### 本目录文件对照

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `LGPL-3.0-only.txt` | LGPLv3 许可证全文 | GNU 官方发布页 https://www.gnu.org/licenses/lgpl-3.0.txt 原样下载（2026-10-14；md5 `3000208d539ec061b899bce1d9ce9404`，可对账） |
| `pyside6_essentials-LicenseRef-Qt-Commercial.txt` | PySide6 Essentials 商业许可条款 | `pyside6_essentials-6.12.0.dist-info/licenses/`（随 wheel 分发） |
| `pyside6_addons-LicenseRef-Qt-Commercial.txt` | PySide6 Addons 商业许可条款 | `pyside6_addons-6.12.0.dist-info/licenses/`（随 wheel 分发） |
| `shiboken6-LicenseRef-Qt-Commercial.txt` | shiboken6 商业许可条款 | `shiboken6-6.12.0.dist-info/licenses/`（随 wheel 分发） |
| `pillow-LICENSE.txt` | Pillow 许可证 | `pillow-12.3.0.dist-info/licenses/LICENSE` |
| `numpy-LICENSE.txt` | numpy 许可证 | `numpy-2.5.4.dist-info/licenses/LICENSE.txt` |
| `imageio-LICENSE.txt` | imageio 许可证 | `imageio-2.38.1.dist-info/licenses/LICENSE` |
| `imageio_ffmpeg-LICENSE.txt` | imageio-ffmpeg 许可证 | `imageio_ffmpeg-0.6.0.dist-info/LICENSE` |
| `f2-abogus-Apache-2.0.txt` | Apache-2.0 许可证全文（f2 abogus.py vendor） | Apache 官方发布页 https://www.apache.org/licenses/LICENSE-2.0.txt 原样下载（2026-10-20；md5 `3b83ef96387f14655fc854ddc3c6bd57`，可对账） |
| `gmssl-BSD.txt` | gmssl 许可证登记说明 + BSD-3-Clause 条款文本 | 上游未随包分发全文（wheel/sdist/仓库均无 LICENSE 文件，2026-10-20 实测）；本文件按上游 PyPI METADATA `License: BSD` 声明代录标准文本，来源口径见文件内说明 |
| `pycryptodome-LICENSE.rst` | pycryptodome 许可证全文（公有领域 + BSD 2-Clause 双许可说明） | `pycryptodome-3.24.1.dist-info/LICENSE.rst` 原样复制（2026-10-21；md5 `acdcd7162c6fe05747189fc722895381`，可对账） |

> 上述 dist-info 文件均从构建机 venv 中**原样复制**，未做任何改写。
> 构建时 PyInstaller 把整个本目录复制为 exe 解包目录内的
> `THIRD-PARTY-LICENSES/`（见 `docs/07-打包发布与验证.md` 收录的 spec 全文），
> 分发时与 exe 同级提供。

## 2. PySide6 / shiboken6（LGPL）分发义务履行情况

PySide6 与 shiboken6 以 **DLL 动态链接**方式随本产品分发。本项目分发包
已满足以下义务：

1. **许可证文本已随包**：LGPLv3 全文见本目录 `LGPL-3.0-only.txt`，
   商业许可选项文本见三个 `*LicenseRef-Qt-Commercial.txt`；
   PySide6 各发行版声明的许可三元组（LGPL-3.0-only / GPL-2.0-only /
   GPL-3.0-only）以其 wheel METADATA 为准，本项目按 LGPLv3 履行义务。
2. **动态链接，DLL 可被用户替换**：本产品以普通 import 方式使用
   PySide6（`import PySide6.QtWidgets` 等），未对 PySide6/shiboken6 做任何
   静态链接或修改；bundle 内的 Qt DLL（`Qt6Core.dll` 等）与
   `shiboken6` 系列 DLL 均为上游未改动的二进制，用户可以自行替换。
3. **为调试目的的反向工程免责**：LGPLv3 第 4 条（Combined Works 条款）导语
   允许（且本产品不禁止）为调试该库与作品组合而对该库进行反向工程；本产品
   未以任何条款限制用户此项权利。
4. **官方源码获取**：PySide6 / shiboken6 官方源码按上述地址获取
   （Qt 官方 Git）；对应本产品所用版本 6.12.0 的上游发布页为
   https://download.qt.io/official_releases/QtForPython/ 。
5. **许可证声明随附**：本文件（NOTICE）随分发提供，注明组件名称、
   许可证与来源。

## 3. 复核方法（干净签出可复跑）

bundle 实际包含哪些顶层包，由构建期自检 exe 现场打印
（`build/selftest_entry.py` 的 `BUNDLE-TOP-LEVEL-PACKAGES` 行），
完整输出存于 `docs/em006-packaging-evidence/selftest-output.txt`；
PyInstaller 的 `build/work/<入口脚本名>/warn-<入口脚本名>.txt` 亦保留用于
许可证清单核对（`build/` 与 `dist/` 均在 `.gitignore`，属既定设计，本地构建时生成）。
