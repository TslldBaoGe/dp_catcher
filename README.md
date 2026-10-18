# DrissionPage 元素捕获器

一个本地运行的「网页可视化元素捕获器」：在浏览器里点一下元素，就能拿到**经过真实校验的定位表达式**、
**可直接运行的 DrissionPage Python 代码**，并且能立刻**测试点击 / 输入 / 取文本 / 取属性**等动作。

定位不是"猜"出来的——每条候选表达式都会用 DrissionPage 真解析一次，核对**匹配数量**、
**首个匹配是不是你点的那个元素**、**高亮框是否与元素重合**，然后才给出结论。

---

## 1. 核心特性

| 能力 | 说明 |
| --- | --- |
| 实时镜像 | 内置 Chromium 画面以 JPEG 帧推送到网页，变更驱动抓帧（DOM 变化才截图），空闲几乎不耗 CPU |
| 两种拾取方式 | ① 在网页控制台的实时画面上点；② 直接在真实浏览器窗口里点（注入拾取器，悬停高亮） |
| 定位校验 | 每条候选都核对「匹配数 / 是否命中目标 / 几何是否一致」，结论分为 **推荐 / 可用 / 不唯一 / 错位 / 未匹配 / 语法错误** |
| 高亮验证 | 一键把匹配到的**所有**元素框出来（镜像上画框 + 真实浏览器里也画框），多匹配时逐个编号 |
| 候选清单 | 自动给出 id / data-* 属性 / name / class / 标签+属性 / 文本 / CSS 路径 / XPath，并按稳定性排序 |
| 相对定位 | 自动构造 `page.ele('@data-uid=1002').ele('tag:td@@class=name')` 这类抗结构变化的写法 |
| 代码生成 | 实时生成 DrissionPage 代码：定位片段 / 信息读取 / 完整可运行脚本，带 `wait_ele` 重试封装 |
| 动作测试 | 点击（含右键/双击/JS 点击）、输入、清空、悬停、聚焦、下拉选择、勾选、滚动到可见… 取文本 / HTML / 属性 / property / value / 状态 / 位置尺寸 / 元素截图 |
| 框架与 Shadow DOM | 支持 iframe（可进入框架定位）与 Shadow DOM 内部元素，代码里生成 `get_frame(...)` / `.shadow_root.ele(...)` |
| 捕获列表 | 把多个元素 + 测试过的动作串起来，一键导出完整脚本 |
| 线程安全 | DrissionPage 的所有调用串行化到同一把锁，帧泵异常自愈，浏览器断开可自动重连 |

---

## 2. 环境与安装

- Python 3.9+（已在 **3.10.13 / `F:\Miniconda39\envs\python31013`** 上实测）
- 本机已安装 Chrome 或 Edge（脚本会自动寻找）

```bash
# 依赖（该环境里 DrissionPage / fastapi / uvicorn 已就绪，可跳过）
F:\Miniconda39\envs\python31013\python.exe -m pip install -r requirements.txt
```

## 3. 启动

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

## 4. 界面使用

```
┌──────────────────────────── 顶部：拾取模式 / 画质 / 浏览器开关 ────────────────────────────┐
│ ← → ⟳  地址栏  访问   定位域: 主文档 › #innerFrame                       测试页           │
├──────────────────────────────────────────┬───────────────────────────────────────────────┤
│                                          │ 元素 │ 定位 │ Python 代码 │ 测试 │ 捕获列表    │
│            实时镜像画面                   │                                               │
│      （点击＝捕获元素 / 真实点击）        │  元素属性、候选定位、代码、动作测试           │
│      高亮框、多匹配编号、十字准星          │                                               │
├──────────────────────────────────────────┤                                               │
│ － 适应 ＋ 67%   x:123 y:456   画面 3/秒   │                                               │
└──────────────────────────────────────────┴───────────────────────────────────────────────┘
```

1. **拾取元素**：保持「拾取模式」勾选，在画面上点你要的元素。
   - 也可以直接在真实浏览器窗口里点（悬停即高亮，Esc 退出拾取模式）。
   - 点错了？在「元素」面板点「选父级」，或点父级链里的任意一层切换目标。
2. **看定位**：切到「定位」面板，候选按推荐度排序，每条都标注匹配数与结论。
   - 点某条候选即可切换，画面上会立刻高亮出**所有**匹配元素（多匹配时带序号）。
   - 想手写表达式？在输入框里填（如 `@name=wd`），点「校验并高亮」。
3. **拿代码**：「Python 代码」面板有完整脚本 / 定位片段 / 信息读取三档，可复制或下载 `.py`。
4. **测动作**：「测试」面板逐个点按钮即真实操作页面，结果与对应代码一起展示；
   成功的动作会被记录，导出的脚本里就带上这些步骤。
5. **攒流程**：满意后点「加入捕获列表」，可以改名；最后「导出完整脚本」。

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

---

## 5. 生成的代码长什么样

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

## 6. HTTP / WebSocket 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` `/demo` `/static/*` | 控制台页面 / 内置测试页 / 静态资源 |
| GET | `/api/settings` `/api/status` | 配置与运行状态（URL、视口、帧数、帧、统计） |
| POST | `/api/browser/start` `stop` `restart` `resize` `preset` `stream` | 浏览器与画质控制 |
| POST | `/api/navigate` `/api/history-nav` | 导航 / 前进后退刷新 |
| POST | `/api/scroll` `/api/scroll-to` `/api/click-at` `/api/move` `/api/type` `/api/key` | 坐标级输入 |
| POST | `/api/pick` | 坐标 → 元素描述 + 定位方案 + 高亮 + 代码（一次拿全） |
| POST | `/api/apply` `/api/chain` `/api/refresh-element` | 切换表达式 / 选父级 / 重新校验 |
| POST | `/api/highlight` `/api/clear-highlight` | 高亮匹配结果 / 清除 |
| POST | `/api/action` | 执行动作（`action` + `spec` + `opts`），返回结果与对应代码 |
| POST | `/api/page-data` | 页面 HTML / 标题 / URL / Cookie / 截图 |
| GET/POST | `/api/history*` `/api/export` | 捕获列表增删改查 / 导出脚本 |
| WS | `/ws` | 推送 `frame`（镜像帧）、`page_event`（真实窗口拾取）、`nav`；接收 `pick` / `apply` / `pickmode` / `preset` / `ping` |

所有接口统一返回 `{"ok": bool, "data": …, "error": …, "error_type": …, "ms": …}`。

---

## 7. 测试

```bash
python tests/run_all.py                 # 全部 115 个用例（会真实启动 Chromium）
python tests/run_all.py --quick         # 只跑纯单元测试（秒级，无需浏览器）
python tests/run_all.py --only e2e -v   # 只跑浏览器端到端
```

| 模块 | 覆盖内容 |
| --- | --- |
| `tests/test_frontend.py` | 前端静态一致性：接口请求方法是否与后端匹配（防 405 静默失败）、`app.js` 引用的元素 id 是否都存在、WebSocket 命令前后端是否对齐、拾取器关键实现是否还在 |
| `tests/test_codegen.py` | 代码生成：表达式渲染、引号转义、框架/Shadow 链、生成的脚本必须能 `compile` |
| `tests/test_locators.py` | 评分与编排：唯一/错位/不唯一/未匹配/语法错误、去重、相对定位、批量下发 |
| `tests/test_e2e.py` | 真实浏览器：拾取、身份比对、高亮几何、相对定位、点击/输入/取文本/取属性/下拉/勾选、坐标点击、滚轮、框架、Shadow DOM、刷新后重定位、导航后拾取器自愈 |
| `tests/test_server_e2e.py` | 真起 uvicorn：HTTP 全接口 + WebSocket 帧推送与拾取 + 浏览器停止/重启 |
| `tests/smoke_dashboard.py` | 控制台前端人工冒烟：捕获 JS 报错、镜像帧、模拟点击捕获、缩放，并截图 |

跳过浏览器用例：`set DPC_SKIP_BROWSER_TESTS=1`。

---

## 8. 配置（环境变量）

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

---

## 9. 稳定性是怎么保证的

1. **串行化 CDP 调用**：DrissionPage 的 driver 不是线程安全的，所有操作（含抓帧）都走同一把可重入锁。
2. **变更驱动抓帧**：页内 `MutationObserver` + 滚动/路由事件维护 `rev` 计数，只有页面真的变了才截图；
   空闲时按 `force_ms` 低频刷新一次兜底（动画/Canvas）。自身高亮的 DOM 变动被排除，避免自我刷屏。
3. **抓帧尺寸归一**：Chrome 的 `clip.scale` 会再乘 devicePixelRatio，这里除回去，
   保证出图 ≈ 视口 × 预设倍率；点击坐标换算用的是**显示尺寸与 CSS 视口宽度**，与像素倍率无关，所以缩放后依然精准。
4. **从不缓存元素对象**：所有动作都按定位表达式重新解析，页面刷新/局部重绘不会导致 `ElementLostError`。
5. **帧泵自愈**：任何异常都不会让抓帧线程退出；拾取器丢失（新文档）会自动补注入。
6. **结构化的错误**：所有接口把异常转成 `{ok:false, error, error_type}`，前端只提示不崩。
7. **浏览器断线可重连**：检测到连接断开会尝试 `reconnect()`，界面上可直接重启浏览器。

## 10. 常见问题

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

## 11. 目录结构

```
python网页drissionpage库元素捕获器/
├─ run.py                  # 启动入口
├─ start.bat / start.sh    # 一键启动脚本
├─ requirements.txt
├─ app/
│  ├─ main.py              # 参数解析 + uvicorn
│  ├─ api.py               # FastAPI 路由 + WebSocket
│  ├─ config.py            # 配置与画质预设
│  ├─ core/
│  │  ├─ browser.py        # 浏览器会话：生命周期、锁、帧泵、动作、几何
│  │  ├─ locators.py       # 候选评分与复核（唯一/错位/不唯一判定）
│  │  ├─ codegen.py        # DrissionPage 代码生成
│  │  └─ workflow.py       # 拾取→校验→高亮→代码 编排 + 捕获列表
│  └─ static/
│     ├─ index.html  style.css  app.js   # 控制台前端
│     └─ picker.js                        # 注入目标页的拾取器
├─ demo/demo_page.html     # 内置测试页（表单/表格/列表/框架/Shadow）
├─ tests/                  # 115 个用例 + 前端冒烟脚本
└─ snapshots/              # 截图输出
```
