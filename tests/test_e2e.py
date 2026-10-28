# -*- coding: utf-8 -*-
"""端到端测试：真实 Chromium + 内置测试页，覆盖拾取 / 校验 / 高亮 / 动作 / 框架 / Shadow。

运行：python -m unittest tests.test_e2e -v
跳过：设置环境变量 DPC_SKIP_BROWSER_TESTS=1
"""
from __future__ import annotations

import base64
import os
import struct
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DEMO_PAGE, settings  # noqa: E402
from app.core import workflow  # noqa: E402
from app.core.browser import Subscriber, session  # noqa: E402

DEMO_URL = 'file:///' + str(DEMO_PAGE).replace(os.sep, '/')
SKIP = os.environ.get('DPC_SKIP_BROWSER_TESTS') == '1'


def jpeg_size(data: bytes):
    """从 JPEG 字节流里读出宽高，避免测试依赖 Pillow。"""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            height, width = struct.unpack('>HH', data[i + 5:i + 9])
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack('>H', data[i + 2:i + 4])[0]
        i += 2 + seg
    return None


@unittest.skipIf(SKIP, '已按环境变量跳过浏览器测试')
class BrowserE2ETest(unittest.TestCase):
    """共享同一个浏览器实例，顺序执行。"""

    @classmethod
    def setUpClass(cls):
        settings.debug_port = int(os.environ.get('DPC_TEST_DEBUG_PORT', 9351))
        settings.auto_start = False
        settings.window_width, settings.window_height = 1500, 950
        settings.stream_enabled = True
        settings.load_mode = 'eager'
        cls.session = session
        res = session.start(DEMO_URL)
        if not res.get('ok'):
            raise RuntimeError('浏览器启动失败: %s %s' % (res.get('error_type'), res.get('error')))
        cls.page = session.page
        time.sleep(0.6)
        session._op(session._inject_all)
        cls.reset_page()

    @classmethod
    def tearDownClass(cls):
        try:
            session.stop()
        except Exception:
            pass

    @classmethod
    def reset_page(cls):
        cls.page.get(DEMO_URL)
        # load_mode=none 不等资源；iframe(srcdoc) 需要额外时间。
        # 注意：不能用 get_frame() 去探活 —— 它在文档未就绪时会长时间阻塞，
        # 这里用页面内 JS 检查 contentDocument 是否可用。
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                ready = cls.page.run_js(
                    'var f=document.getElementById("innerFrame");'
                    'var d=f && f.contentDocument;'
                    'return !!(d && d.getElementById("frameInput"));', timeout=2)
                if ready:
                    break
            except Exception:
                pass
            time.sleep(0.3)
        time.sleep(0.2)

    # ------------------------------------------------------------- 工具

    def top_rect(self, locator):
        """元素在顶层视口中的矩形（与 getBoundingClientRect 一致）。

        注意：iframe 解析出来是 ChromiumFrame，它的 run_js 在子文档里执行，
        所以要用 frame_ele 或 rect 属性取父文档中的位置。
        """
        ele = self.page.ele(locator, timeout=2)
        self.assertTrue(ele, '找不到元素 %s' % locator)
        target = getattr(ele, 'frame_ele', None) or ele
        try:
            got = target.run_js('var r=this.getBoundingClientRect();'
                                'return [r.left, r.top, r.width, r.height];')
            return [float(v) for v in got]
        except Exception:
            x, y = ele.rect.viewport_location
            w, h = ele.rect.size
            return [float(x), float(y), float(w), float(h)]

    def inner_rect(self, frame_locator, inner_locator):
        """iframe 内部元素相对该框架视口的矩形。

        load_mode 现在是 none（不等资源加载），iframe 内容可能还没就绪，
        这里显式重试，避免把「加载慢」误判成「功能坏了」。
        """
        deadline = time.time() + 8
        last = None
        while time.time() < deadline:
            try:
                fr = self.page.get_frame(frame_locator)
                ele = fr.ele(inner_locator, timeout=1.5)
                if ele:
                    got = ele.run_js('var r=this.getBoundingClientRect();'
                                     'return [r.left, r.top, r.width, r.height];')
                    return [float(v) for v in got]
            except Exception as exc:
                last = exc
            time.sleep(0.3)
        raise AssertionError('iframe 内元素 %s 在 8 秒内未就绪: %s' % (inner_locator, last))

    def center_of(self, locator):
        r = self.top_rect(locator)
        return r[0] + r[2] / 2.0, r[1] + r[3] / 2.0

    # ------------------------------------------------------- 基础与注入

    def test_01_picker_injected(self):
        kind = self.page.run_js('return typeof window.__DPC__;')
        self.assertEqual(kind, 'object')
        ver = self.page.run_js('return window.__DPC__.ver;')
        self.assertIsInstance(ver, int)

    def test_02_drain_reports_viewport(self):
        info = self.session._op(self.session._drain_locked)['data']
        self.assertIsNotNone(info)
        for key in ('rev', 'url', 'sx', 'sy', 'iw', 'ih', 'dpr'):
            self.assertIn(key, info)
        self.assertGreater(info['iw'], 100)
        self.assertGreater(info['ih'], 100)
        self.assertIn('demo_page.html', info['url'])

    def test_03_revision_advances_on_dom_change(self):
        before = self.session._op(self.session._drain_locked)['data']['rev']
        self.page.run_js("var d=document.createElement('div');d.id='__revprobe';"
                         "document.body.appendChild(d);")
        time.sleep(0.15)
        after = self.session._op(self.session._drain_locked)['data']['rev']
        self.assertGreater(after, before, 'DOM 变化应推动 rev 增长（帧泵据此按需抓帧）')
        self.page.run_js("var d=document.getElementById('__revprobe'); if(d) d.remove();")

    # ------------------------------------------------------------ 抓帧

    def test_04_frame_capture_is_valid_jpeg_with_expected_size(self):
        info = self.session._op(self.session._drain_locked)['data']
        frame = self.session._grab_frame(info, time.perf_counter())
        self.assertIsNotNone(frame, '抓帧失败')
        raw = base64.b64decode(frame['data'])
        self.assertGreater(len(raw), 2000)
        size = jpeg_size(raw)
        self.assertIsNotNone(size, '返回的数据不是合法 JPEG')
        # 出图是整视口设备像素截图：不能用 clip.scale 缩放，
        # 否则 headed Chrome 会把缩放后的抓图表面短暂贴到窗口上（肉眼闪烁）。
        dpr = max(1.0, float(info.get('dpr') or 1.0))
        self.assertAlmostEqual(size[0], info['iw'] * dpr, delta=3,
                               msg='图像宽度应为 CSS 视口宽 × devicePixelRatio')
        self.assertAlmostEqual(size[1], info['ih'] * dpr, delta=3, msg='图像高度不符')
        self.assertAlmostEqual(frame['cw'], info['iw'], delta=0.5,
                               msg='帧必须带上 CSS 视口宽度，前端据此换算点击坐标')

    # ------------------------------------------------------------ 拾取

    def test_10_pick_username_by_coordinates(self):
        self.reset_page()
        x, y = self.center_of('#username')
        payload = workflow.pick_workflow(x, y)
        self.assertTrue(payload['ok'], payload.get('error'))
        desc = payload['desc']
        self.assertEqual(desc['tag'], 'input')
        self.assertEqual(desc['id'], 'username')
        self.assertIn('attrs', desc)
        self.assertEqual(desc['attrs'].get('name'), 'username')
        # 拾取矩形应与真实元素一致
        rect = self.top_rect('#username')
        for i in range(4):
            self.assertAlmostEqual(desc['rect'][i], rect[i], delta=1.5)

    def test_11_recommended_locator_is_unique_and_hits_target(self):
        self.reset_page()
        x, y = self.center_of('#username')
        payload = workflow.pick_workflow(x, y)
        best = payload['plan']['recommended']
        self.assertIsNotNone(best)
        self.assertEqual(best['value'], '#username')
        self.assertEqual(best['verdict'], 'unique')
        self.assertEqual(best['count'], 1)
        self.assertIs(best['identity'], True, '应确认首个匹配就是被拾取的元素')
        self.assertTrue(best['geometry'])

    def test_12_candidate_list_contains_multiple_kinds(self):
        self.reset_page()
        x, y = self.center_of('#username')
        plan = workflow.pick_workflow(x, y)['plan']
        kinds = {c['kind'] for c in plan['candidates']}
        self.assertIn('id', kinds)
        self.assertTrue(kinds & {'attr', 'tagattr', 'css'}, '应至少给出一种属性/路径候选')
        for cand in plan['candidates']:
            self.assertIn(cand['verdict'], {'unique', 'ok', 'multi', 'wrong', 'none', 'invalid'})

    def test_13_pick_reports_ancestor_chain(self):
        self.reset_page()
        x, y = self.center_of('#username')
        desc = workflow.pick_workflow(x, y)['desc']
        self.assertTrue(desc.get('chain'), '应给出父级链便于切换选取目标')
        self.assertLess(len(desc['chain']), 9)

    # ------------------------------------------------------------ 校验

    def test_20_validate_counts_are_accurate(self):
        self.reset_page()
        specs = ['#username', '.form-input', 'tag:input', 'text=这段文本不存在xyz']
        res = self.session.validate_specs(specs, []).get('data')
        by = {i['value']: i for i in res['items']}
        self.assertEqual(by['#username']['count'], 1)
        self.assertEqual(by['.form-input']['count'], 3)
        self.assertGreater(by['tag:input']['count'], 3)
        self.assertEqual(by['text=这段文本不存在xyz']['count'], 0)

    def test_21_invalid_syntax_is_reported_not_crashed(self):
        for bad in ('css:>>>bad<<<', 'xpath://*[[', 'badprefix:zzz'):
            with self.subTest(bad=bad):
                res = self.session.validate_specs([bad], [])
                self.assertTrue(res['ok'], '无效表达式不能让整批校验失败')
                item = res['data']['items'][0]
                # 要么返回 0 个匹配，要么带错误信息，但绝不能抛异常
                self.assertLessEqual(item['count'], 0)
                if item['count'] < 0:
                    self.assertTrue(item['error'])

    def test_22_wrong_target_is_detected(self):
        """拾取密码框后校验用户名的表达式：首个匹配不是目标 → 必须判定为错位。"""
        self.reset_page()
        x, y = self.center_of('#password')
        payload = workflow.pick_workflow(x, y)
        desc = payload['desc']
        self.assertEqual(desc['id'], 'password')
        res = self.session.validate_specs(['#username'], [], desc['rect'], desc['tag'])
        item = res['data']['items'][0]
        self.assertEqual(item['count'], 1)
        self.assertIs(item['identity'], False, '身份比对应识别出不是同一个元素')

    def test_23_relative_and_drission_paths_are_offered(self):
        self.reset_page()
        x, y = self.center_of('#username')
        plan = workflow.pick_workflow(x, y)['plan']
        kinds = {c['kind'] for c in plan['candidates']}
        self.assertTrue(kinds & {'css', 'xpath'}, '应包含 DrissionPage 生成的路径候选')

    def test_24_relative_locator_for_repeated_structure(self):
        """重复结构（表格行）应给出「锚定行 + 行内定位」的相对定位候选，并成为推荐。

        tag:td@@class=name 全页有 3 个，但在某一行范围内只有 1 个 ——
        这正是相对定位的价值，也是它比「复制 CSS 选择器」更耐改版的原因。
        """
        self.reset_page()
        row = self.page.ele('@data-uid=1002')
        row.scroll.to_see()
        time.sleep(0.4)
        cell = row.ele('tag:td@@class=name')
        rect = cell.run_js('var r=this.getBoundingClientRect();'
                           'return [r.left,r.top,r.width,r.height];')
        x = float(rect[0]) + float(rect[2]) / 2
        y = float(rect[1]) + float(rect[3]) / 2
        payload = workflow.pick_workflow(x, y)
        self.assertTrue(payload['ok'], payload.get('error'))
        self.assertEqual(payload['desc']['text'], '李四')

        rel = [c for c in payload['plan']['candidates'] if c['kind'] == 'relative']
        self.assertTrue(rel, '重复结构应生成相对定位候选')
        best = rel[0]
        self.assertEqual(best['verdict'], 'unique')
        self.assertEqual(best['count'], 1)
        self.assertIs(best['identity'], True, '相对定位也必须确认命中的是目标元素')
        self.assertEqual(best['value'][0], '@data-uid=1002', '锚点应是稳定的行标识')
        self.assertIn('name', str(best['value'][1]))

        # 同一尾表达式在整页范围下是「错位」的，恰好说明相对定位的必要性
        whole = self.session.validate_specs([best['value'][1]], [], payload['desc']['rect'],
                                           payload['desc']['tag'])['data']['items'][0]
        self.assertGreater(whole['count'], 1)
        self.assertIs(whole['identity'], False)

        # 生成的代码必须是链式调用
        self.assertIn("page.ele('@data-uid=1002').ele(", payload['code']['locate'])

    def test_25_relative_anchor_must_be_unique(self):
        """相对定位的锚点自身必须唯一，否则等于在整页里取第一个匹配。"""
        self.reset_page()
        x, y = self.center_of('#username')
        plan = workflow.pick_workflow(x, y)['plan']
        for cand in plan['candidates']:
            if cand['kind'] != 'relative':
                continue
            anchors = self.session.validate_specs([cand['value'][0]], [])['data']['items'][0]
            self.assertEqual(anchors['count'], 1, '相对定位的锚点必须唯一')

    def test_26_multiline_text_uses_line_segment(self):
        """被 <br> 拆成多行的文本，必须按单行片段生成文本候选。

        真站实测（angular.dev 的 <h2> 里有 <br>）：
        text=Productivity Meets scalability 与 tx=同一整串都匹配不到（count=0），
        只有 tx=Productivity（单行）才能命中。这是文本定位在重前端页面可用的关键。
        """
        self.reset_page()
        # 演示页没有 <br> 结构，这里临时造一个还原现场
        self.page.run_js("""
            var h = document.createElement('h2');
            h.id = '__multiline_probe';
            h.innerHTML = 'Productivity<br>Meets scalability';
            h.style.cssText = 'position:fixed;top:200px;left:600px;z-index:9';
            document.body.appendChild(h);
        """)
        time.sleep(0.3)
        try:
            r = self.top_rect('#__multiline_probe')
            payload = workflow.pick_workflow(r[0] + r[2] / 2, r[1] + r[3] / 2)
            self.assertTrue(payload['ok'], payload.get('error'))
            cands = payload['desc'].get('cands') or []
            text_locs = [c['loc'] for c in cands if c.get('k') == 'text']
            self.assertTrue(
                any(loc == 'tx=Productivity' for loc in text_locs),
                '应生成单行文本候选 tx=Productivity，实际: %s' % text_locs)

            # 整串跨行文本不应成为候选（它必然匹配不到，只会污染列表）
            self.assertNotIn('tx=Productivity Meets scalability', text_locs)

            # 单行候选必须真的能命中目标
            res = self.session.validate_specs(['tx=Productivity'], [],
                                              payload['desc']['rect'], payload['desc']['tag'])
            item = res['data']['items'][0]
            self.assertEqual(item['count'], 1)
            self.assertIs(item['identity'], True, 'tx= 单行文本应精确命中目标元素')
        finally:
            self.page.run_js("var e=document.getElementById('__multiline_probe');"
                             "if(e) e.remove();")

    def test_27_navigation_is_not_blocked_by_slow_page(self):
        """导航不得因页面加载慢而长时间阻塞（曾经阻塞 11 秒，画面完全冻结）。

        这里用「导航到本地页 + 计时」验证基本量级；真实外网站在 stress_sites.py 里覆盖。
        """
        self.reset_page()
        for _ in range(3):
            t0 = time.perf_counter()
            res = self.session.navigate(DEMO_URL)
            dt = (time.perf_counter() - t0) * 1000
            self.assertTrue(res.get('ok'), res.get('error'))
            self.assertLess(dt, 5000, '导航耗时 %.0f ms，超过 5 秒说明又在等页面 load' % dt)

    def test_28_frame_pump_never_freezes_after_navigation(self):
        """导航前后画面都要持续出帧，不能因为导航把帧泵停掉。"""
        import base64 as _b64

        frames = []
        lock = threading.Lock()

        class _Sub(Subscriber):
            def __init__(self):
                super().__init__(None)

            def push_frame(self, msg):
                with lock:
                    frames.append(msg)

        sub = _Sub()
        session.subscribe(sub)
        try:
            time.sleep(1.0)
            self.session.navigate(DEMO_URL)
            with lock:
                n0 = len(frames)
            t0 = time.perf_counter()
            # 制造变化（滚动）
            for _ in range(10):
                self.session.page.run_cdp(
                    'Input.dispatchMouseEvent', type='mouseWheel', x=400, y=400,
                    deltaY=120, deltaX=0, button='none')
                time.sleep(0.1)
            time.sleep(1.0)
            dt = time.perf_counter() - t0
            with lock:
                got = len(frames) - n0
            self.assertGreater(got, 2, '导航后 %.1fs 内只收到 %d 帧，帧泵疑似被阻塞' % (dt, got))
        finally:
            session.unsubscribe(sub)

    # ------------------------------------------------------------ 高亮

    def test_30_highlight_geometry_matches_element(self):
        self.reset_page()
        res = self.session.highlight('#submitBtn', [])
        self.assertTrue(res['ok'])
        group = res['data']['groups'][0]
        self.assertEqual(group['count'], 1)
        self.assertEqual(len(group['rects']), 1)
        want = self.top_rect('#submitBtn')
        got = group['rects'][0]
        for i in range(4):
            self.assertAlmostEqual(got[i], want[i], delta=1.5,
                                   msg='高亮矩形必须与实际元素重合（第 %d 个分量）' % i)

    def test_31_highlight_marks_all_matches(self):
        res = self.session.highlight('tag:li@@class=item', [])
        group = res['data']['groups'][0]
        self.assertEqual(group['count'], 4)
        self.assertEqual(len(group['rects']), 4)
        # 每一行都应各不相同
        tops = {round(r[1]) for r in group['rects']}
        self.assertEqual(len(tops), 4)

    def test_32_highlight_zero_match_is_not_an_error(self):
        res = self.session.highlight('text=绝对不存在的文本zzz', [])
        self.assertTrue(res['ok'])
        self.assertEqual(res['data']['groups'][0]['count'], 0)
        self.assertEqual(res['data']['groups'][0]['rects'], [])

    def test_33_clear_highlight(self):
        self.assertTrue(self.session.clear_highlight()['ok'])

    # ------------------------------------------------------------ 动作

    def test_40_input_clear_and_read_value(self):
        self.reset_page()
        res = self.session.element_action('input', '#username',
                                          text='drission_test', clear_first=True)
        self.assertTrue(res['ok'], res.get('error'))
        self.assertEqual(self.page.ele('#username').property('value'), 'drission_test')
        got = self.session.element_action('get_value', '#username')
        self.assertEqual(got['data']['value'], 'drission_test')
        self.session.element_action('clear', '#username')
        self.assertEqual(self.page.ele('#username').property('value'), '')

    def test_41_click_triggers_page_logic(self):
        self.reset_page()
        self.session.element_action('input', '#username', text='zhangsan', clear_first=True)
        res = self.session.element_action('click', '#submitBtn')
        self.assertTrue(res['ok'], res.get('error'))
        self.assertIn('zhangsan', self.page.ele('#status').text)

    def test_42_get_text_html_attrs(self):
        self.reset_page()
        self.assertEqual(self.session.element_action('get_text', '#submitBtn')['data']['text'],
                         '提交表单')
        html = self.session.element_action('get_html', '#submitBtn')['data']['html']
        self.assertIn('submitBtn', html)
        inner = self.session.element_action('get_html', '#textBox', inner=True)['data']['html']
        self.assertIn('<b>', inner, 'inner=True 时应返回 innerHTML')
        attr = self.session.element_action('get_attr', '#attrBox', name='data-code')
        self.assertEqual(attr['data']['value'], 'A-001')
        attrs = self.session.element_action('get_attrs', '#attrBox')['data']['attrs']
        self.assertEqual(attrs['data-role'], 'demo')
        self.assertEqual(attrs['title'], '这是提示文字')

    def test_43_get_property_and_states(self):
        self.reset_page()
        prop = self.session.element_action('get_property', '#city', name='value')
        self.assertEqual(prop['data']['value'], 'sh')
        st = self.session.element_action('get_states', '#hobbyRead')['data']['states']
        self.assertTrue(st['is_checked'])
        self.assertTrue(st['is_displayed'])
        self.assertTrue(st['is_enabled'])
        st2 = self.session.element_action('get_states', '#email')['data']['states']
        self.assertTrue(st2['is_enabled'])

    def test_44_hover_focus_blur(self):
        self.reset_page()
        res = self.session.element_action('hover', '#hoverTarget')
        self.assertTrue(res['ok'], res.get('error'))
        time.sleep(0.15)
        self.assertIn('on', self.page.ele('#hoverTarget').attr('class') or '')
        self.assertTrue(self.session.element_action('focus', '#username')['ok'])
        self.assertTrue(self.session.element_action('blur', '#username')['ok'])

    def test_45_scroll_to_see_works_off_screen(self):
        self.reset_page()
        self.page.run_js('window.scrollTo(0,0);')
        time.sleep(0.2)
        r0 = self.top_rect('#hoverTarget')
        inner_h = self.page.run_js('return window.innerHeight;')
        self.assertTrue(r0[1] >= inner_h or r0[1] + r0[3] <= 0,
                        '初始时应不在视口内（实际 y=%s, 视口高=%s）' % (r0[1], inner_h))
        self.assertTrue(self.session.element_action('scroll_to', '#hoverTarget')['ok'])
        time.sleep(0.3)
        r = self.top_rect('#hoverTarget')
        self.assertGreaterEqual(r[1], 0, '滚动后元素应进入视口')
        self.assertLess(r[1], 950)

    def test_46_select_checkbox_radio(self):
        self.reset_page()
        self.assertTrue(self.session.element_action('select', '#city', by='text', value='北京')['ok'])
        self.assertEqual(self.page.ele('#city').value, 'bj')
        # DrissionPage 的 by_index 从 1 开始计数
        self.assertTrue(self.session.element_action('select', '#city', by='index', value=4)['ok'])
        self.assertEqual(self.page.ele('#city').value, 'gz')
        self.assertTrue(self.session.element_action('check', '#hobbyCode')['ok'])
        self.assertTrue(self.page.ele('#hobbyCode').states.is_checked)
        self.assertTrue(self.session.element_action('check', '#hobbyCode', uncheck=True)['ok'])
        self.assertFalse(self.page.ele('#hobbyCode').states.is_checked)

    def test_47_element_screenshot(self):
        self.reset_page()
        res = self.session.element_action('get_screenshot', '#submitBtn')
        self.assertTrue(res['ok'], res.get('error'))
        path = res['data']['file']
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 200)

    def test_48_unknown_action_reports_error(self):
        res = self.session.element_action('no_such_action', '#username')
        self.assertFalse(res['ok'])
        self.assertIn('未知动作', res['error'])

    def test_49_missing_element_reports_error_not_crash(self):
        res = self.session.element_action('click', '#definitely-not-here', timeout=0.3)
        self.assertFalse(res['ok'])
        self.assertTrue(res['error'])

    # -------------------------------------------------- 坐标点击 / 滚动

    def test_50_coordinate_click_hits_the_right_element(self):
        self.reset_page()
        self.page.ele('#addItemBtn').scroll.to_see()
        time.sleep(0.35)
        before = len(self.page.eles('tag:li@@class=item'))
        x, y = self.center_of('#addItemBtn')
        res = self.session.click_at(x, y)
        self.assertTrue(res['ok'])
        time.sleep(0.3)
        after = len(self.page.eles('tag:li@@class=item'))
        self.assertEqual(after, before + 1, '坐标点击应精确命中按钮')

    def test_51_wheel_scroll_changes_scroll_position(self):
        self.reset_page()
        self.page.run_js('window.scrollTo(0,0);')
        time.sleep(0.2)
        self.assertEqual(self.page.run_js('return window.scrollY;'), 0)
        res = self.session.scroll(0, 420, 400, 300)
        self.assertTrue(res['ok'])
        time.sleep(0.35)
        self.assertGreater(self.page.run_js('return window.scrollY;'), 0)

    def test_52_scroll_to_top_and_bottom(self):
        res = self.session.scroll_to(False)
        self.assertTrue(res['ok'])
        time.sleep(0.35)
        self.assertGreater(self.page.run_js('return window.scrollY;'), 0, '应滚动到底部')
        res2 = self.session.scroll_to(True)
        self.assertTrue(res2['ok'])
        time.sleep(0.35)
        self.assertLess(self.page.run_js('return window.scrollY;'), 30, '应回到顶部')

    def test_53_insert_text_into_focused_element(self):
        self.reset_page()
        self.session.element_action('focus', '#username')
        res = self.session.insert_text('typed_via_cdp')
        self.assertTrue(res['ok'])
        self.assertEqual(self.page.ele('#username').property('value'), 'typed_via_cdp')

    # ------------------------------------------------------------ 框架

    def test_60_actions_inside_iframe(self):
        self.reset_page()
        path = ['#innerFrame']
        attr = self.session.element_action('get_attr', '#frameInput', frame_path=path,
                                           name='placeholder')
        self.assertTrue(attr['ok'], attr.get('error'))
        self.assertEqual(attr['data']['value'], '框架内输入框')
        ins = self.session.element_action('input', '#frameInput', frame_path=path,
                                          text='frame_text', clear_first=True)
        self.assertTrue(ins['ok'], ins.get('error'))
        frame = self.page.get_frame('#innerFrame')
        self.assertEqual(frame.ele('#frameInput').property('value'), 'frame_text')

    def test_61_highlight_inside_iframe_is_offset_to_top_viewport(self):
        self.reset_page()
        # load_mode=none 时不等待资源，iframe 内容可能还没就绪 → 重试几轮
        res = None
        group = None
        for _ in range(20):
            res = self.session.highlight('#frameInput', ['#innerFrame'])
            if res.get('ok'):
                group = res['data']['groups'][0]
                if group.get('count') == 1:
                    break
            time.sleep(0.35)
        self.assertTrue(res.get('ok'), '高亮失败: %s %s' % (res.get('error_type'), res.get('error')))
        self.assertEqual(group.get('count'), 1,
                         'iframe 内元素未就绪（%s 秒后仍匹配不到）' % 7)
        # 用「iframe 元素在顶层视口的矩形 + 框架内相对坐标」推算期望值
        frame_rect = self.top_rect('#innerFrame')
        inner = self.inner_rect('#innerFrame', '#frameInput')
        want_x = frame_rect[0] + float(inner[0])
        want_y = frame_rect[1] + float(inner[1])
        got = group['rects'][0]
        self.assertAlmostEqual(got[0], want_x, delta=3, msg='框架内元素高亮需要换算到顶层坐标')
        self.assertAlmostEqual(got[1], want_y, delta=3, msg='框架内元素高亮纵向坐标不符')
        self.assertAlmostEqual(got[2], float(inner[2]), delta=2)

    def test_62_pick_inside_iframe(self):
        self.reset_page()
        frame_rect = self.top_rect('#innerFrame')
        inner = self.inner_rect('#innerFrame', '#frameInput')
        x = frame_rect[0] + float(inner[0]) + float(inner[2]) / 2
        y = frame_rect[1] + float(inner[1]) + float(inner[3]) / 2
        payload = workflow.pick_workflow(x, y, ['#innerFrame'])
        self.assertTrue(payload['ok'], payload.get('error'))
        desc = payload['desc']
        self.assertEqual(desc['tag'], 'input')
        self.assertEqual(desc['id'], 'frameInput')
        self.assertEqual(desc['framePath'], ['#innerFrame'])
        best = payload['plan']['recommended']
        self.assertEqual(best['value'], '#frameInput')
        self.assertEqual(best['count'], 1)

    # ------------------------------------------------------- Shadow DOM

    def test_70_shadow_element_is_reachable(self):
        self.reset_page()
        ele = self.page.ele('#shadowHost').shadow_root.ele('#shadowBtn')
        self.assertEqual(ele.text, 'Shadow按钮')

    def test_71_pick_shadow_element_produces_shadow_chain(self):
        self.reset_page()
        self.page.ele('#shadowHost').scroll.to_see()
        time.sleep(0.35)
        # Shadow DOM 与宿主共享同一个视口：getBoundingClientRect 已经是顶层视口坐标，
        # 不能再叠加宿主偏移（这一点和 iframe 不同）。
        btn = self.page.ele('#shadowHost').shadow_root.ele('#shadowBtn', timeout=2).run_js(
            'var r=this.getBoundingClientRect();return [r.left,r.top,r.width,r.height];')
        x = float(btn[0]) + float(btn[2]) / 2
        y = float(btn[1]) + float(btn[3]) / 2
        payload = workflow.pick_workflow(x, y)
        self.assertTrue(payload['ok'], payload.get('error'))
        desc = payload['desc']
        self.assertEqual(desc['tag'], 'button')
        self.assertEqual(desc['id'], 'shadowBtn')
        self.assertTrue(desc.get('inShadow'), '应识别出元素位于 Shadow DOM 内')
        self.assertTrue(desc.get('shadow'), '应给出 shadow 宿主链')

    def test_72_shadow_action_via_chain(self):
        self.reset_page()
        spec = ['#shadowHost', 'shadow:#shadowText']
        res = self.session.element_action('get_text', spec)
        self.assertTrue(res['ok'], res.get('error'))
        self.assertEqual(res['data']['text'], 'shadow 文本内容')

    def test_73_shadow_chain_is_validated_and_code_generates(self):
        self.reset_page()
        self.page.ele('#shadowHost').scroll.to_see()
        time.sleep(0.35)
        # Shadow DOM 与宿主共享同一个视口：getBoundingClientRect 已经是顶层视口坐标，
        # 不能再叠加宿主偏移（这一点和 iframe 不同）。
        btn = self.page.ele('#shadowHost').shadow_root.ele('#shadowBtn', timeout=2).run_js(
            'var r=this.getBoundingClientRect();return [r.left,r.top,r.width,r.height];')
        x = float(btn[0]) + float(btn[2]) / 2
        y = float(btn[1]) + float(btn[3]) / 2
        payload = workflow.pick_workflow(x, y)
        self.assertTrue(payload['ok'])
        best = payload['plan']['recommended']
        self.assertIsNotNone(best, 'Shadow DOM 元素也应给出推荐定位')
        self.assertEqual(best['verdict'], 'unique')
        self.assertTrue(best['identity'])
        code = payload['code']['script']
        self.assertIn('.shadow_root.ele(', code)
        compile(code, 'shadow_script.py', 'exec')

    # ---------------------------------------------------------- 健壮性

    def test_80_locator_survives_dom_mutation(self):
        """动态插入元素后，定位表达式仍然有效（每次都重新解析）。"""
        self.reset_page()
        self.assertTrue(self.session.element_action('click', '#addItemBtn')['ok'])
        time.sleep(0.2)
        res = self.session.element_action('get_text', ['tag:li@@data-id=2', 'tag:span@@class=title'])
        self.assertTrue(res['ok'])
        self.assertEqual(res['data']['text'], '香蕉')
        self.assertEqual(len(self.page.eles('tag:li@@class=item')), 5)

    def test_81_stale_element_is_relocated_by_locator(self):
        self.reset_page()
        n1 = self.session.element_action('get_text', '#submitBtn')['data']['text']
        self.page.refresh()
        time.sleep(0.5)
        n2 = self.session.element_action('get_text', '#submitBtn')['data']['text']
        self.assertEqual(n1, n2, '刷新后按表达式重新解析应依然可用')

    def test_82_navigation_keeps_picker_alive(self):
        self.page.get('about:blank')
        time.sleep(0.4)
        self.page.get(DEMO_URL)
        time.sleep(0.6)
        self.assertEqual(self.page.run_js('return typeof window.__DPC__;'), 'object',
                         '导航后拾取器应自动重新注入')
        self.assertIsNotNone(self.session._op(self.session._drain_locked)['data'])

    def test_83_pick_outside_viewport_or_empty_area_is_handled(self):
        self.reset_page()
        res = self.session.pick_at(5, 5, [])
        # 左上角是页头，应当能拾取到元素；关键是不得抛异常
        self.assertIn('ok', res)

    def test_84_status_reports_running(self):
        st = self.session.status()
        self.assertTrue(st['running'])
        self.assertIn('frames', st['stats'])
        self.assertIn('metrics', st)

    def test_85_page_data_endpoints(self):
        self.reset_page()
        html = self.session.page_data('html')['data']['html']
        self.assertIn('shadowHost', html)
        self.assertIn('DrissionPage', html)
        self.assertTrue(self.session.page_data('url')['data']['url'].startswith('file://'))
        self.assertIn('DrissionPage', self.session.page_data('title')['data']['title'])

    def test_86_frame_capture_after_scroll_matches_viewport_offset(self):
        """滚动后 clip 截图必须跟随视口，否则点击坐标会错位。"""
        self.page.run_js('window.scrollTo(0, 500);')
        time.sleep(0.3)
        info = self.session._op(self.session._drain_locked)['data']
        self.assertGreater(info['sy'], 100)
        frame = self.session._grab_frame(info, time.perf_counter())
        self.assertIsNotNone(frame)
        # 帧元数据里的滚动位置供前端参考；整视口截图不再用 clip 起点
        self.assertAlmostEqual(frame['sy'], info['sy'], delta=1)
        size = jpeg_size(base64.b64decode(frame['data']))
        dpr = max(1.0, float(info.get('dpr') or 1.0))
        self.assertAlmostEqual(size[0], info['iw'] * dpr, delta=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
