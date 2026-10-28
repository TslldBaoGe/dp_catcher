# DrissionPage 元素捕获器 · Chrome 扩展

在你**自己的浏览器**里直接点元素，生成经过校验的 DrissionPage 定位表达式与 Python 代码。
不需要另开受控浏览器，登录态、插件、Cookie 全都是你自己的。

## 安装

1. 打开 Chrome，地址栏输入 `chrome://extensions`
2. 右上角打开「**开发者模式**」
3. 点「**加载已解压的扩展程序**」，选择本目录（`extension/`）
4. 建议同时打开扩展详情页里的「**允许访问文件网址**」（这样 `file://` 页面也能用）

> 安装或更新扩展后，已打开的页面需要**刷新一次**才能生效。
>
> **看不到工具栏图标？** 这是 Chrome 的默认行为（新扩展不自动占位）。
> 直接按 `Alt+P` 即可使用；或点地址栏右侧拼图图标，在列表里点它。
> 详见 [安装文档.md](安装文档.md) 的 Q1。

## 使用

| 操作 | 说明 |
| --- | --- |
| **按 `Alt+P`** | **最省事：面板自动弹出并进入拾取模式（不需要图标）** |
| 点工具栏上的扩展图标 | 在当前页右侧打开/关闭面板（需先固定图标） |
| 面板里点「开始拾取」 | 进入拾取模式，鼠标移动时元素会高亮，点一下即捕获 |
| `Alt+P` | 快捷键开关拾取模式 |
| `Esc` | 退出拾取模式 |
| 拖动标题栏 | 移动面板位置（自动记住，不会拖丢） |

捕获后右侧面板会出现五页内容：

- **元素** —— 标签/属性/文本/位置/评级、全部 HTML 属性、父级链（点任意一层可切换目标）
- **定位** —— 所有候选表达式，带匹配数量与结论（推荐 / 可用 / 不唯一 / 错位 / 未匹配 / 语法错误）
- **代码** —— 完整可运行脚本 / 定位片段 / 8 类常用写法（等待、点击、输入、读取、断言、滚动、批量遍历）
- **测试** —— 直接在当前页执行动作：点击、双击、右键、JS 点击、悬停、聚焦、输入、清空、勾选、取文本/HTML/属性/value/状态/位置/样式
- **列表** —— 把多个元素+动作攒成一份脚本，一键导出

## 和桌面版的区别

| | Chrome 扩展 | 桌面版 |
| --- | --- | --- |
| 运行位置 | 你自己的浏览器 | 独立受控 Chromium |
| 登录态 | 直接可用 | 需要自己登录 |
| 定位校验 | 浏览器内完成 | Python + DrissionPage 真解析 |
| 镜像画面 | 不需要（就是当前页面） | 通过 WebSocket 推送 |
| 适用场景 | 日常抓取、调试已有登录态站点 | 需要完全隔离的环境、批量自动化 |

扩展版的校验用与 DrissionPage 一致的解析语义（`css:` / `xpath:` / `tag:div@attr=v` / `text=` / `tx=`），
并对「首个匹配是否真的是你点的那个元素」做精确身份比对——所以「错位」能被识别出来。

## 定位表达式速查

| 写法 | 含义 | 示例 |
| --- | --- | --- |
| `#id` / `.class` | CSS 简写 | `#username` |
| `css:div.a` | CSS 选择器 | `css:input#username` |
| `@attr=value` | 属性等于 | `@name=username`、`@data-uid=1002` |
| `tag:div@@class=a b` | 标签 + 属性（`@@class` 整串精确匹配） | `tag:td@@class=name` |
| `text=内容` | 全部文本精确相等 | `text=提交表单` |
| `tx=内容` | 文本包含（适合有子元素的容器） | `tx=登录` |
| `xpath://...` | XPath | `xpath://input[@id="username"]` |

> 在面板「定位」页底部的输入框里可以手写任意表达式，回车即校验并高亮。

## 支持的场景

- ✅ **iframe**：picker 注入到所有 frame，框架内的元素也能拾取
- ✅ **Shadow DOM**：穿透 open shadow root，代码里生成 `.shadow_root.ele(...)`
- ✅ **动态页面**：每次校验都实时重新解析，不缓存元素对象
- ✅ **重复结构**：自动给出「锚点 + 容器内定位」的相对定位写法

## 生成代码示例

```python
# -*- coding: utf-8 -*-
import time

from DrissionPage import ChromiumPage, ChromiumOptions


def main():
    co = ChromiumOptions()
    page = ChromiumPage(co)
    page.get('https://example.com/login')

    # ---------- input#username ----------
    ele = page.ele('#username')
    ele.clear()
    ele.input('zhangsan')

    # ---------- button#submitBtn ----------
    ele = page.ele('#submitBtn')
    ele.click()


if __name__ == "__main__":
    main()
```

## 文件说明

```
extension/
├─ manifest.json    扩展清单（MV3）
├─ background.js    后台：点击图标时注入脚本、快捷键、设置持久化
├─ picker.js        注入页面主世界：拾取、候选生成、高亮绘制
├─ panel.js         面板逻辑：校验、评级、代码生成、动作测试（Shadow DOM 隔离样式）
├─ panel.css        面板样式
├─ panel.html       面板的独立页面（可选，正常使用时注入到当前页）
└─ icons/           扩展图标
```

## 已知限制

- `chrome://`、`edge://`、扩展商店等浏览器内部页面无法注入（浏览器安全限制）
- `file://` 页面需要在扩展详情里手动开启「允许访问文件网址」
- 扩展无法直接驱动真实的「鼠标点击」（只能派发 DOM 事件）；需要真实鼠标事件时请用桌面版
