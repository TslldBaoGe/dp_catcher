# DrissionPage 元素捕获器

一个本地运行的「网页可视化元素捕获器」：在浏览器里点一下元素，就能拿到**经过真实校验的定位表达式**、
**可直接运行的 DrissionPage Python 代码**，并且能立刻**测试点击 / 输入 / 取文本 / 取属性**等动作。

定位不是"猜"出来的——每条候选表达式都会用 DrissionPage 真解析一次，核对**匹配数量**、
**首个匹配是不是你点的那个元素**、**高亮框是否与元素重合**，然后才给出结论。
每个元素还会得到一个 **A / B / C / D 稳定性评级 + 0-100 分**，并列出 9 种常用 DrissionPage 写法，
可一键复制或直接运行。

桌面版使用 Electron 承载界面、FastAPI 作为本地后端，打包成**免安装文件夹版 EXE**，
双击即用，不需要命令行和浏览器标签页。

---

## 1. 核心特性

| 能力 | 说明 |
| --- | --- |
| 实时镜像 | 内置 Chromium 画面以 JPEG 帧推送到网页，变更驱动抓帧（DOM 变化才截图），空闲几乎不耗 CPU |
| 两种拾取方式 | ① 在网页控制台的实时画面上点；② 直接在真实浏览器窗口里点（注入拾取器，悬停高亮） |
| 定位校验 | 每条候选都核对「匹配数 / 是否命中目标 / 几何是否一致」，结论分为 **推荐 / 可用 / 不唯一 / 错位 / 未匹配 / 语法错误** |
| 高亮验证 | 一键把匹配到的**所有**元素框出来（镜像上画框 + 真实浏览器里也画框），多匹配时逐个编号 |
| 稳定性评级 | 每个元素给出 A/B/C/D 总评级与 0-100 分，逐条定位也有分数、等级和加减分明细 |
| 元素用法 | 同一元素自动生成等待 / 点击 / 输入 / 读文本 / 读属性 / 断言 / 滚动 / 截图 / 批量遍历 9 种写法，可复制、可直接运行 |
| 候选清单 | 自动给出 id / data-* 属性 / name / class / 标签+属性 / 文本 / CSS 路径 / XPath，并按稳定性排序 |
| 相对定位 | 自动构造 `page.ele('@data-uid=1002').ele('tag:td@@class=name')` 这类抗结构变化的写法 |
| 代码生成 | 实时生成 DrissionPage 代码：定位片段 / 信息读取 / 完整可运行脚本，带 `wait_ele` 重试封装 |
| 动作测试 | 点击（含右键/双击/JS 点击）、输入、清空、悬停、聚焦、下拉选择、勾选、滚动到可见… 取文本 / HTML / 属性 / property / value / 状态 / 位置尺寸 / 元素截图 |
| 框架与 Shadow DOM | 支持 iframe（可进入框架定位）与 Shadow DOM 内部元素，代码里生成 `get_frame(...)` / `.shadow_root.ele(...)` |
| 捕获列表 | 把多个元素 + 测试过的动作串起来，一键导出完整脚本 |
| 桌面客户端 | Electron + FastAPI，免安装文件夹版 EXE，自动分配端口、自动关停浏览器、日志与截图写入用户目录 |
| 线程安全 | DrissionPage 的所有调用串行化到同一把锁，帧泵异常自愈，浏览器断开可自动重连 |
| **Chrome 扩展版** | 在**你自己的浏览器**里直接点选元素，登录态/插件/Cookie 都不用另配；见 [extension/README.md](extension/README.md) |

---

## 2. 环境与安装

- Python 3.9+（已在 **3.10.13 / `F:\Miniconda39\envs\python31013`** 上实测）
- 本机已安装 Chrome 或 Edge（脚本会自动寻找）

```bash
# 依赖（该环境里 DrissionPage / fastapi / uvicorn 已就绪，可跳过）
F:\Miniconda39\envs\python31013\python.exe -m pip install -r requirements.txt
```

## 3. 桌面客户端（文件夹版 EXE）

### 运行成品

构建完成后双击：

```
dist_client\win-unpacked\DrissionPageCatcher.exe
```

- 免安装绿色版本，整个 `win-unpacked` 目录可以拷到其他机器使用（目标机器需装有 Chrome 或 Edge）。
- 启动时显示启动画面；后端自动分配空闲端口，只监听 `127.0.0.1`，不暴露到局域网。
- 关闭窗口会先请求后端关停浏览器再退出；Electron 异常退出时，后端父进程守护会自行关闭，避免残留后台进程。
- 截图、日志和用户数据写入 Electron 用户数据目录（默认 `%APPDATA%\drissionpage-element-catcher`），不污染程序目录。
- 菜单「文件 → 打开日志目录 / 打开数据目录」可直接定位这些文件。

### 自己构建

前置：Python 3.9+ 与 `requirements.txt` 依赖、Node.js 18+、已安装 `pyinstaller`。

```bat
build_client.bat
```

脚本依次执行：

1. `pyinstaller desktop/backend.spec` → `build/backend/DPCBackend/DPCBackend.exe`
2. `npm install`（首次）
3. `npm run dist:dir` → `dist_client/win-unpacked/`（先在系统临时目录打包，再复制回来）

也可以分步执行：

```bash
F:\Miniconda39\envs\python31013\python.exe -m PyInstaller desktop/backend.spec ^
    --noconfirm --distpath build/backend --workpath build/pyinstaller
npm install
npm run dist:dir
```

开发时直接用 Electron 加载源码（后端从项目目录启动）：

```bat
set DPC_PYTHON=F:\Miniconda39\envs\python31013\python.exe
npm start
```

若已经构建过后端，`npm start` 会优先使用 `build/backend/DPCBackend/DPCBackend.exe`。

---

## 4. 从源码启动

Windows 双击 `start.bat`，或命令行：

```bash
F:\Miniconda39\envs\python31013\python.exe run.py
```

启动后会打印控制台地址（默认 <http://127.0.0.1:8777/>）并自动打开。
受控 Chromium 会同时启动并跳转到内置测试页 `demo/demo_page.html`。

常用参数：

```bash
python run.py --port 9000              # 换控制台端口
python run.py --debug-port 9333        # 换浏览器调试端口
python run.py --headless               # 无头模式（只看镜像画面）
python run.py --no-browser             # 不自动打开控制台网页
python run.py --no-auto-start          # 不自动启动浏览器，进页面后手动点「启动浏览器」
```

---

## 5. 界面使用

```
┌──────────────────────────── 顶部：拾取模式 / 画质 / 浏览器开关 ────────────────────────────┐
│ ← → ⟳  地址栏  访问   定位域: 主文档 › #innerFrame                       测试页           │
├──────────────────────────────────────────┬───────────────────────────────────────────────┤
│                                          │ 元素 │ 定位 │ 用法 │ 代码 │ 测试 │ 捕获列表      │
│            实时镜像画面                   │                                               │
│      （点击＝捕获元素 / 真实点击）        │  元素属性、候选定位、稳定性评级、用法片段     │
│      高亮框、多匹配编号、十字准星          │                                               │
├──────────────────────────────────────────┤                                               │
│ － 适应 ＋ 67%   x:123 y:456   画面 3/秒   │                                               │
└──────────────────────────────────────────┴───────────────────────────────────────────────┘
```

1. **拾取元素**：保持「拾取模式」勾选，在画面上点你要的元素。
   - 也可以直接在真实浏览器窗口里点（悬停即高亮，Esc 退出拾取模式）。
   - 点错了？在「元素」面板点「选父级」，或点父级链里的任意一层切换目标。
2. **看定位**：切到「定位」面板，候选按推荐度排序，每条都标注匹配数、结论、A/B/C/D 等级与 0-100 分。
   - 点某条候选即可切换，画面上会立刻高亮出**所有**匹配元素（多匹配时带序号）。
   - 想手写表达式？在输入框里填（如 `@name=wd`），点「校验并高亮」。
3. **看用法**：「用法」面板列出等待、点击、输入、读取、断言、滚动、截图、批量遍历 9 种写法。
   - 输入文本、属性名可以即时修改，片段会跟着变。
   - 带「运行」按钮的片段会真实操作页面，结果直接显示在面板底部。
4. **拿代码**：「代码」面板有完整脚本 / 定位片段 / 信息读取三档，可复制或下载 `.py`。
5. **测动作**：「测试」面板逐个点按钮即真实操作页面，结果与对应代码一起展示；
   成功的动作会被记录，导出的脚本里就带上这些步骤。
6. **攒流程**：满意后点「加入捕获列表」，可以改名；最后「导出完整脚本」。

### 定位结论怎么读

| 结论 | 含义 | 能不能用 |
| --- | --- | --- |
| **推荐 / 可用** | 唯一匹配，且确认首个匹配就是你点的元素 | 放心用 |
| **不唯一** | 匹配多个，但首个正好是目标（`ele()` 会取首个） | 能用，取全部时注意 |
| **错位** | 匹配到的首个**不是**你点的元素 —— `ele()` 会操作错元素 | 别用 |
| **未匹配** | 页面上找不到 | 别用 |
| **语法错误** | DrissionPage 解析不了 | 别用 |

> 「错位」是最有价值的告警：`#id` 看起来唯一，但它可能指向另一个隐藏模板里的节点。
> 本工具用 `this === 目标元素` 做精确身份比对，只有确认命中才标「推荐」。

### 定位表达式速查（DrissionPage 语法）

| 写法 | 含义 | 示例 |
| --- | --- | --- |
| `#id` / `.class` | CSS 简写 | `#username`、`.form-input` |
| `css:div.a` | CSS 选择器 | `css:input#username` |
| `@attr=value` | 属性等于 | `@name=username`、`@data-uid=1002` |
| `tag:div@@class=a b` | 标签 + 属性（`@@class` 是**整串精确**匹配） | `tag:td@@class=name` |
| `text=内容` | 精确文本 | `text=提交表单` |
| `xpath://...` | XPath | `xpath://input[@id="username"]` |
| 相对定位 | 链式，逐级缩小 | `page.ele('@data-uid=1002').ele('tag:td@@class=name')` |
| 框架 / Shadow | 先切域再定位 | `page.get_frame('#f').ele('#kw')`、`page.ele('#host').shadow_root.ele('#btn')` |

### 稳定性评级怎么算

总评级取「推荐定位」的等级；每条候选定位也有自己的 0-100 分和 A/B/C/D 等级。

| 等级 | 分数 | 含义 |
| --- | --- | --- |
| **A** | 85-100 | 稳定，适合长期使用 |
| **B** | 70-84 | 较稳，页面改版时需要留意 |
| **C** | 50-69 | 一般，建议换用更稳的定位写法 |
| **D** | 0-49 | 不稳，不建议在生产脚本里依赖 |

评分因素：

- **定位类型基础分**：id 96、相对定位 88、data-* 属性 84、标签+属性 74、class 66、CSS 54、XPath 44、文本 36。
- **结构深度**：层级很深的 CSS / XPath 路径会扣分，并限制最高评级。
- **动态值**：UUID / 哈希 / 时间戳最高 B；纯数字或框架生成 id 最高 B；CSS-in-JS 类名最高 C。
- **校验结论**：唯一命中 +6，匹配多个 -12，错位封顶 20 分，未匹配或语法错误直接 0 分。
- **身份比对**：无法比对目标身份 -6；页内初判唯一命中 +3。

### 元素用法片段

「用法」面板对当前定位固定生成 9 条片段：

| 片段 | 说明 | 可运行 |
| --- | --- | --- |
| 等待出现 | `ele = page.ele(...)` 后判空并抛错 | 否 |
| 点击 | `ele.click()` 真实鼠标点击 | 是 |
| 输入文本 | `ele.clear()` + `ele.input('...')` | 填写文本后是 |
| 读取文本 | `print(ele.text)` | 是 |
| 读取属性 | `print(ele.attr('href'))`，属性名可改 | 是 |
| 存在/可见断言 | `assert ele` + `ele.states.is_displayed` | 否 |
| 滚动到可见 | `ele.scroll.to_see()` | 是 |
| 元素截图 | `ele.get_screenshot(...)` | 是 |
| 批量遍历 | `page.eles(...)` 后逐个取文本 | 否 |

框架与 Shadow DOM 会自动带进片段，例如
`page.get_frame('#innerFrame').ele('#kw')`、`page.ele('#host').shadow_root.ele('#btn')`。

---

## 7. 生成的代码长什么样

```python
# -*- coding: utf-8 -*-
import time
from DrissionPage import ChromiumPage, ChromiumOptions


def wait_ele(owner, locator, timeout=10, retry=3):
    for _ in range(retry):
        ele = owner.ele(locator, timeout=timeout)
        if ele:
            return ele
        time.sleep(0.5)
    return None


def main():
    co = ChromiumOptions()
    co.set_argument('--window-size=1600,1000')
    page = ChromiumPage(co)
    page.get('https://example.com/login')

    # ---------- 1. 用户名输入框 ----------
    ele = page.ele('#username')          # 唯一 id（最稳定）
    ele.clear()
    ele.input('zhangsan')

    # ---------- 2. 提交按钮 ----------
    ele = page.ele('#submitBtn')
    ele.click()

    # ---------- 3. 第二行姓名 ----------
    ele = page.ele('@data-uid=1002').ele('tag:td@@class=name')
    print(ele.text)

    # ---------- 4. 框架内输入框 ----------
    ele = page.get_frame('#innerFrame').ele('#frameInput')
    print(ele.text)


if __name__ == "__main__":
    main()
```

---

## 8. HTTP / WebSocket 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` `/demo` `/static/*` | 控制台页面 / 内置测试页 / 静态资源 |
| GET | `/api/settings` `/api/status` | 配置与运行状态（URL、视口、帧数、帧、统计） |
| POST | `/api/browser/start` `stop` `restart` `resize` `preset` `stream` | 浏览器与画质控制 |
| POST | `/api/navigate` `/api/history-nav` | 导航 / 前进后退刷新 |
| POST | `/api/scroll` `/api/scroll-to` `/api/click-at` `/api/move` `/api/type` `/api/key` | 坐标级输入 |
| POST | `/api/pick` | 坐标 → 元素描述 + 定位方案 + 高亮 + 代码（一次拿全） |
| POST | `/api/apply` `/api/chain` `/api/refresh-element` | 切换表达式 / 选父级 / 重新校验 |
| POST | `/api/usages` | 按定位表达式生成 9 条用法片段 + 稳定性评级 |
| POST | `/api/highlight` `/api/clear-highlight` | 高亮匹配结果 / 清除 |
| POST | `/api/action` | 执行动作（`action` + `spec` + `opts`），返回结果与对应代码 |
| POST | `/api/page-data` | 页面 HTML / 标题 / URL / Cookie / 截图 |
| GET/POST | `/api/history*` `/api/export` | 捕获列表增删改查 / 导出脚本 |
| POST | `/api/desktop/shutdown` | 仅桌面壳可用，校验 `DPC_DESKTOP_TOKEN` 后关停浏览器与后端 |
| WS | `/ws` | 推送 `frame`（镜像帧）、`page_event`（真实窗口拾取）、`nav`；接收 `pick` / `apply` / `pickmode` / `preset` / `ping` |

所有接口统一返回 `{"ok": bool, "data": …, "error": …, "error_type": …, "ms": …}`。

---

## 9. 测试

```bash
python tests/run_all.py                 # 全部用例（会真实启动 Chromium）
python tests/run_all.py --quick         # 只跑纯单元测试（秒级，无需浏览器）
python tests/run_all.py --only e2e -v   # 只跑浏览器端到端
```

| 模块 | 覆盖内容 |
| --- | --- |
| `tests/test_frontend.py` | 前端静态一致性：接口请求方法是否与后端匹配（防 405 静默失败）、`app.js` 引用的元素 id 是否都存在、WebSocket 命令前后端是否对齐、拾取器关键实现是否还在 |
| `tests/test_codegen.py` | 代码生成：表达式渲染、引号转义、框架/Shadow 链、9 条用法片段，生成的脚本必须能 `compile` |
| `tests/test_locators.py` | 评分与编排：唯一/错位/不唯一/未匹配/语法错误、A-D 稳定性评级、动态值扣分、去重、相对定位、批量下发 |
| `tests/test_e2e.py` | 真实浏览器：拾取、身份比对、高亮几何、相对定位、点击/输入/取文本/取属性/下拉/勾选、坐标点击、滚轮、框架、Shadow DOM、刷新后重定位、导航后拾取器自愈 |
| `tests/test_server_e2e.py` | 真起 uvicorn：HTTP 全接口（含用法片段与桌面关停令牌）+ WebSocket 帧推送与拾取 + 浏览器停止/重启 |
| `tests/smoke_dashboard.py` | 控制台前端人工冒烟：捕获 JS 报错、镜像帧、模拟点击捕获、缩放、设置面板，并截图 |
| `tests/stress_sites.py` | 真站压测：16 个高难度网站（反爬/SPA/Shadow DOM/长页面）测导航耗时、首帧延迟、定位命中率 |
| `tests/verify_packaged.py` | 打包产物验证：直接驱动 exe 跑完整捕获链路（19 项检查） |
| `tests/verify_extension.py` | Chrome 扩展验证：真实 Chrome 加载扩展，检查注入/拾取/候选/动作/Shadow DOM（20 项） |

跳过浏览器用例：`set DPC_SKIP_BROWSER_TESTS=1`。

---

## 10. 配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DPC_HOST` / `DPC_PORT` | `127.0.0.1` / `8777` | 控制台监听地址 |
| `DPC_DEBUG_PORT` | `9333` | Chromium 调试端口 |
| `DPC_BROWSER_PATH` | 自动 | 指定 chrome.exe / msedge.exe |
| `DPC_HEADLESS` | `0` | `1` 无头运行 |
| `DPC_WIN_W` / `DPC_WIN_H` | `1600` / `1000` | 受控窗口尺寸 |
| `DPC_START_URL` | 内置测试页 | 启动浏览器后打开的地址 |
| `DPC_STREAM_PRESET` | `standard` | `smooth` / `standard` / `hd` |
| `DPC_POLL_MS` | `70` | 变更轮询间隔（越小越跟手，越费 CPU） |
| `DPC_LOAD_MODE` | `eager` | 导航等待策略，捕获场景更跟手 |
| `DPC_MAX_HIGHLIGHT` | `200` | 单次高亮最多绘制多少个框 |
| `DPC_AUTO_START` | `1` | 启动服务时自动拉起浏览器 |
| `DPC_DATA_DIR` | 项目 `snapshots/` / 桌面版用户目录 | 截图等可写数据的根目录 |
| `DPC_DESKTOP` | 自动 | 桌面壳启动时设为 `1`，`/api/settings` 会返回 `desktop: true` |
| `DPC_DESKTOP_TOKEN` | 空 | 桌面壳生成的关停令牌；未设置时关停接口直接拒绝 |

---

## 11. 稳定性是怎么保证的

1. **串行化 CDP 调用**：DrissionPage 的 driver 不是线程安全的，所有操作（含抓帧）都走同一把可重入锁。
2. **变更驱动抓帧**：页内 `MutationObserver` + 滚动/路由事件维护 `rev` 计数，只有页面真的变了才截图；
   空闲时按 `force_ms` 低频刷新一次兜底（动画/Canvas）。自身高亮的 DOM 变动被排除，避免自我刷屏。
3. **整视口抓帧**：不使用 `clip.scale` 缩放——headed Chrome 在离屏抓图期间会把
   缩放后的抓图表面短暂贴到窗口上，肉眼就是整页一闪一闪。现在抓完整视口（设备像素），
   由前端按 CSS 尺寸缩放显示；点击坐标换算用的是**显示尺寸与 CSS 视口宽度**，与像素倍率无关。
4. **从不缓存元素对象**：所有动作都按定位表达式重新解析，页面刷新/局部重绘不会导致 `ElementLostError`。
5. **帧泵自愈**：任何异常都不会让抓帧线程退出；拾取器丢失（新文档）会自动补注入。
6. **结构化的错误**：所有接口把异常转成 `{ok:false, error, error_type}`，前端只提示不崩。
7. **浏览器断线可重连**：检测到连接断开会尝试 `reconnect()`，界面上可直接重启浏览器。

## 12. 常见问题

**点了没反应 / 提示该坐标上没有元素？**
先确认左侧画面是最新的；元素不在视口内时先滚动画面对准它（滚轮或「滚动到可见」）。

**镜像画面不动？**
画面是变更驱动的：静止页面本来就没有新帧。可点顶部「画质」调档，或按 `⟳` 刷新页面。

**为什么定位到了「错位」的表达式？**
说明这个表达式匹配到的第一个元素不是你要的（常见于隐藏模板、同名节点）。请选「推荐」那条，
或改用相对定位。

**iframe 里的元素怎么捕获？**
点中 iframe 后，在「元素」面板点「进入框架」，顶部定位域会变成 `主文档 › #innerFrame`，
之后捕获/测试都作用在框架内；代码里会自动生成 `page.get_frame('#innerFrame').ele(...)`。

**Shadow DOM 里的元素呢？**
直接点即可：拾取器会穿透 open shadow root，代码里生成 `page.ele('#host').shadow_root.ele('#btn')`。

**能连我自己已经打开的 Chrome 吗？**
可以：先用 `--remote-debugging-port=9333` 启动 Chrome，再让本工具用同一个 `DPC_DEBUG_PORT`，
DrissionPage 会接管该实例。

---

## 13. 目录结构

```
python网页drissionpage库元素捕获器/
├─ run.py                  # 源码启动入口
├─ start.bat / start.sh    # 一键启动（浏览器控制台模式）
├─ build_client.bat        # 一键构建后端 + 文件夹版 EXE
├─ package.json            # Electron / electron-builder 配置
├─ requirements.txt
├─ app/
│  ├─ main.py              # 参数解析 + uvicorn
│  ├─ api.py               # FastAPI 路由 + WebSocket
│  ├─ config.py            # 配置、画质预设、冻结资源/可写数据目录
│  ├─ core/
│  │  ├─ browser.py        # 浏览器会话：生命周期、锁、帧泵、动作、几何
│  │  ├─ locators.py       # 候选评分与复核 + A-D 稳定性评级
│  │  ├─ codegen.py        # DrissionPage 代码/用法片段生成
│  │  └─ workflow.py       # 拾取→校验→高亮→代码 编排 + 捕获列表
│  └─ static/
│     ├─ index.html  style.css  app.js   # 控制台前端
│     └─ picker.js                        # 注入目标页的拾取器
├─ desktop/
│  ├─ backend_main.py      # 桌面后端入口：端口/日志/父进程守护
│  ├─ backend.spec         # PyInstaller onedir 打包配置
│  └─ electron/
│     ├─ main.js           # 后端进程管理 + 窗口 + 菜单 + 关停
│     ├─ preload.js        # 错误页重试/打开日志的 IPC 桥
│     ├─ splash.html  error.html
│     └─ icon.ico  icon.png
├─ demo/demo_page.html     # 内置测试页（表单/表格/列表/框架/Shadow）
├─ tests/                  # 单元 + 浏览器端到端用例
└─ snapshots/              # 源码模式截图输出
```
