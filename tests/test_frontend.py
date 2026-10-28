# -*- coding: utf-8 -*-
"""前端静态一致性检查（不需要浏览器，秒级）。

这类检查专门用来兜住「没有构建步骤的纯前端」最容易犯的低级错：
  * 用 GET 调用了只接受 POST 的接口（后端回 405，前端静默失败）
  * 引用了 index.html 里不存在的元素 id（静默 null，功能直接不工作）
  * WebSocket 发出去的命令后端根本没处理
"""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import STATIC_DIR  # noqa: E402

APP_JS = (STATIC_DIR / 'app.js').read_text(encoding='utf-8')
INDEX_HTML = (STATIC_DIR / 'index.html').read_text(encoding='utf-8')
PICKER_JS = (STATIC_DIR / 'picker.js').read_text(encoding='utf-8')
API_PY = (STATIC_DIR.parent / 'api.py').read_text(encoding='utf-8')

# 只允许用 GET 调用的接口（与 api.py 中的 @app.get 保持一致）
GET_ENDPOINTS = {
    '/api/settings', '/api/status', '/api/frames', '/api/describe', '/api/history',
    '/api/ports',
}


class TestApiCalls(unittest.TestCase):

    def test_get_calls_only_target_get_endpoints(self):
        """api(path) 不带第二参数时走 GET；若目标是 POST 接口就会 405。"""
        pattern = re.compile(r"api\(\s*'([^']+)'\s*([,)])")
        checked = 0
        for match in pattern.finditer(APP_JS):
            path, next_char = match.group(1), match.group(2)
            if not path.startswith('/api'):
                continue
            checked += 1
            if next_char == ')':
                self.assertIn(path, GET_ENDPOINTS,
                              '%s 被用 GET 调用，但它不是 GET 接口（前端会收到 405）' % path)
        self.assertGreater(checked, 15, '没有解析到预期的接口调用，检查正则是否失效')

    def test_no_call_uses_explicit_post_for_get_endpoints(self):
        """反向检查：GET 接口不要用 POST 调（同样会 405）。"""
        for path in GET_ENDPOINTS:
            with self.subTest(path=path):
                self.assertNotIn("api('%s', {" % path, APP_JS)

    def test_http_status_is_not_swallowed(self):
        """api() 必须显式检查 HTTP 状态码，不能只 await res.json()。"""
        self.assertIn('if (!res.ok)', APP_JS,
                      'api() 必须把非 2xx 当作失败抛出来，否则 405/404 会被静默忽略')


class TestDomIds(unittest.TestCase):

    def test_all_referenced_ids_exist_in_html(self):
        ids = set(re.findall(r'id="([^"]+)"', INDEX_HTML))
        used = set(re.findall(r"\$\(\s*'([A-Za-z][\w-]*)'\s*\)", APP_JS))
        missing = sorted(used - ids)
        self.assertFalse(missing, 'app.js 引用了 index.html 中不存在的 id: %s' % missing)
        self.assertGreater(len(used), 20, '没有解析到预期的 id 引用')

    def test_tab_and_pane_ids_match(self):
        """标签页按钮的 data-tab 必须对应 pane-<tab> 容器。"""
        tabs = set(re.findall(r'data-tab="([^"]+)"', INDEX_HTML))
        panes = set(re.findall(r'id="pane-([^"]+)"', INDEX_HTML))
        self.assertEqual(tabs, panes, '标签页与内容面板不匹配')


class TestWebSocketCommands(unittest.TestCase):

    def test_frontend_commands_are_handled_by_backend(self):
        sent = set(re.findall(r"cmd:\s*'([^']+)'", APP_JS))
        handled = set(re.findall(r"cmd == '([^']+)'", API_PY))
        self.assertTrue(sent, '没有解析到前端发送的 WebSocket 命令')
        unhandled = sorted(sent - handled)
        self.assertFalse(unhandled, '后端没有处理这些 WebSocket 命令: %s' % unhandled)

    def test_backend_push_types_are_handled_by_frontend(self):
        pushed = set(re.findall(r"\{'type': '([^']+)'", API_PY))
        handled = set(re.findall(r"case '([^']+)':", APP_JS))
        # 'pong' 由前端心跳忽略即可，其余都应处理
        unhandled = sorted(pushed - handled - {'pong'})
        self.assertTrue(not unhandled, '前端没有处理这些推送类型: %s' % unhandled)


class TestCodeHighlight(unittest.TestCase):

    def test_highlighter_has_no_control_char_markers(self):
        """高亮占位符若残留，会在代码块里显示成不可见控制字符。"""
        start = APP_JS.index('function highlightPython(')
        end = APP_JS.index('/* ------------------------------------------------------------- WebSocket')
        snippet = APP_JS[start:end]
        self.assertNotIn('\u0001', snippet)
        self.assertNotIn('\u0002', snippet)

    def test_highlighter_escapes_html(self):
        start = APP_JS.index('function highlightPython(')
        end = APP_JS.index('/* ------------------------------------------------------------- WebSocket')
        self.assertIn('esc(code)', APP_JS[start:end],
                      '高亮前必须先转义 HTML，避免代码内容注入页面')


class TestPickerScript(unittest.TestCase):

    def test_picker_guards_against_double_injection(self):
        self.assertIn('if (window.__DPC__ && window.__DPC__.ver === VER)', PICKER_JS)

    def test_picker_does_not_use_invalid_mutation_observer_options(self):
        """attributeFilter: null 会让 observe() 抛 TypeError，进而静默失去变更监听。"""
        call = re.search(r'S\.obs\.observe\((.*?)\);', PICKER_JS, re.S)
        self.assertIsNotNone(call, '没找到 observe() 调用')
        self.assertNotIn('attributeFilter', call.group(1),
                         'observe() 的选项里不能出现 attributeFilter: null')

    def test_picker_observes_deeply(self):
        """必须监听子树与属性，否则页面局部更新不会被察觉。"""
        call = re.search(r'S\.obs\.observe\((.*?)\);', PICKER_JS, re.S).group(1)
        for key in ('childList: true', 'subtree: true', 'attributes: true'):
            with self.subTest(key=key):
                self.assertIn(key, call)

    def test_picker_ignores_own_overlay_mutations(self):
        """自身高亮造成的 DOM 变动不应推动 rev，否则帧泵会被自己刷屏。"""
        self.assertIn("t.closest('#' + LAYER_ID)", PICKER_JS)

    def test_picker_reads_provide_required_api(self):
        for name in ('__DPC__', 'pickAt', 'describe', 'drain', 'setPick', 'drawRects',
                     'clearDraw', 'hover', 'chainOf', 'verifyCands'):
            with self.subTest(name=name):
                self.assertIn(name, PICKER_JS)

    def test_picker_pierces_open_shadow_roots(self):
        """document.elementFromPoint 只返回宿主，必须用 shadowRoot.elementFromPoint 穿透。"""
        self.assertIn('shadowRoot.elementFromPoint', PICKER_JS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
