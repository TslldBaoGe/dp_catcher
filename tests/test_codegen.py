# -*- coding: utf-8 -*-
"""代码生成单元测试（不需要浏览器）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import codegen  # noqa: E402


class TestLocatorRendering(unittest.TestCase):

    def test_simple_string(self):
        expr = codegen.render_locator('#username')
        self.assertEqual(expr, "('#username')")

    def test_chain_locator(self):
        expr = codegen.render_locator(['@data-uid=1002', 'tag:td@@class=name'])
        self.assertEqual(expr, "('@data-uid=1002').ele('tag:td@@class=name')")

    def test_scope_frame_path(self):
        scope = codegen.render_scope(['#innerFrame'])
        self.assertEqual(scope, "page.get_frame('#innerFrame')")

    def test_nested_frame_path(self):
        scope = codegen.render_scope(['#a', '#b'], base='page')
        self.assertEqual(scope, "page.get_frame('#a').get_frame('#b')")

    def test_shadow_chain(self):
        shadow = [{'selector': '#shadowHost', 'id': 'shadowHost'}]
        expr = codegen.render_locator('#shadowBtn', shadow)
        self.assertEqual(expr, "('#shadowHost').shadow_root.ele('#shadowBtn')")

    def test_shadow_prefix_in_chain(self):
        expr = codegen.render_locator(['#shadowHost', 'shadow:#shadowBtn'])
        self.assertEqual(expr, "('#shadowHost').shadow_root.ele('#shadowBtn')")

    def test_nested_shadow_prefix(self):
        expr = codegen.render_locator(['#host1', 'shadow:#host2', 'shadow:#inner'])
        self.assertEqual(expr,
                         "('#host1').shadow_root.ele('#host2').shadow_root.ele('#inner')")

    def test_quote_escaping(self):
        expr = codegen.render_locator("text=它's \"x\"")
        self.assertIn("\\'", expr)
        line = codegen.locate_line("text=a'b", 'ele')
        self.assertIn("text=a\\'b", line)

    def test_locate_line_with_frame_and_comment(self):
        line = codegen.locate_line('#frameInput', 'ele', 'page', ['#innerFrame'],
                                   comment='框架内输入框')
        self.assertIn("ele = page.get_frame('#innerFrame').ele('#frameInput')", line)
        self.assertTrue(line.endswith('# 框架内输入框'))
        self.assertTrue(line.startswith('    '))


class TestActionSnippets(unittest.TestCase):

    def test_click(self):
        self.assertEqual(codegen.action_line('click', 'ele', {}), '    ele.click()')

    def test_input_quotes(self):
        code = codegen.action_line('input', 'ele', {'text': "a'b"})
        self.assertIn("ele.input('a\\'b')", code)

    def test_input_with_clear(self):
        code = codegen.action_line('input', 'ele', {'text': 'x', 'clear_first': True})
        self.assertIn('ele.clear()', code)
        self.assertIn("ele.input('x')", code)

    def test_attr_name(self):
        code = codegen.action_line('get_attr', 'ele', {'name': 'placeholder'})
        self.assertIn("ele.attr('placeholder')", code)

    def test_select_by_text(self):
        code = codegen.action_line('select', 'ele', {'by': 'text', 'value': '上海'})
        self.assertIn("ele.select.by_text('上海')", code)

    def test_unknown_action_returns_empty(self):
        self.assertEqual(codegen.action_line('nope', 'ele', {}), '')

    def test_all_documented_actions_render(self):
        for action in ['click', 'click_js', 'right_click', 'double_click', 'hover', 'focus',
                       'blur', 'input', 'clear', 'check', 'uncheck', 'select', 'scroll_to',
                       'get_text', 'get_raw_text', 'get_html', 'get_inner_html', 'get_attr',
                       'get_attrs', 'get_property', 'get_value', 'get_states', 'get_rect',
                       'get_css', 'get_screenshot']:
            with self.subTest(action=action):
                self.assertTrue(codegen.action_line(action, 'ele', {'name': 'x', 'text': 'y'}).strip())


class TestFullScript(unittest.TestCase):

    def test_script_is_valid_python(self):
        steps = [
            {'name': '用户名', 'spec': '#username', 'framePath': [], 'shadow': [],
             'why': '唯一 id',
             'actions': [{'action': 'input', 'opts': {'text': 'zhangsan', 'clear_first': True}},
                         {'action': 'click', 'opts': {}}]},
            {'name': '表格姓名', 'spec': ['@data-uid=1002', 'tag:td@@class=name'],
             'framePath': [], 'shadow': [], 'why': '相对定位',
             'actions': [{'action': 'get_text', 'opts': {}}]},
            {'name': '框架内输入', 'spec': '#frameInput', 'framePath': ['#innerFrame'],
             'shadow': [], 'why': '唯一 id', 'actions': []},
        ]
        script = codegen.full_script(steps, url='http://example.com', window=[1600, 1000])
        compile(script, 'generated.py', 'exec')  # 语法必须正确

    def test_script_contains_key_lines(self):
        steps = [{'name': 'a', 'spec': '#x', 'framePath': [], 'shadow': [], 'why': '',
                  'actions': [{'action': 'click', 'opts': {}}]}]
        script = codegen.full_script(steps, url='http://e.com')
        self.assertIn('from DrissionPage import ChromiumPage, ChromiumOptions', script)
        self.assertIn('def wait_ele(', script)
        self.assertIn("page.get('http://e.com')", script)
        self.assertIn("ele = page.ele('#x')", script)
        self.assertIn('ele.click()', script)

    def test_empty_steps_still_valid(self):
        script = codegen.full_script([], url='http://e.com')
        compile(script, 'generated.py', 'exec')

    def test_step_without_spec_is_skipped_safely(self):
        script = codegen.full_script([{'name': 'x', 'spec': None}], url='')
        compile(script, 'generated.py', 'exec')
        self.assertIn('已跳过', script)


class TestReportSnippet(unittest.TestCase):

    def test_report_lists_attrs(self):
        desc = {'framePath': [], 'shadow': [], 'attrs': {'id': 'a', 'name': 'b'}}
        code = codegen.report_snippet(desc, '#a')
        self.assertIn("ele.attr('id')", code)
        self.assertIn("ele.attr('name')", code)
        self.assertIn('ele.states.is_displayed', code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
