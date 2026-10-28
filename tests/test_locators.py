# -*- coding: utf-8 -*-
"""定位方案评分与编排逻辑测试（用假会话，不需要浏览器）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import locators  # noqa: E402


class FakeSession:
    """模拟 BrowserSession.validate_specs，只按表达式返回预置结果。"""

    def __init__(self, table, paths=None):
        self.table = table
        self.paths = paths if paths is not None else []
        self.calls = []

    def validate_specs(self, specs, frame_path=None, target_rect=None, target_tag=None,
                       max_items=None):
        self.calls.append(list(specs))
        has_target = bool(target_rect or target_tag)
        items = []
        for spec in specs:
            key = spec if isinstance(spec, str) else ' >> '.join(spec)
            preset = self.table.get(key)
            if preset is None:
                items.append({'value': key, 'chain': spec if isinstance(spec, list) else None,
                              'count': 0, 'identity': None, 'geometry': False, 'rects': [],
                              'tags': [], 'first_rect': None, 'tag': None,
                              'error': None, 'hasTarget': has_target})
            else:
                rects = preset.get('rects', [[10, 20, 100, 30]])
                item = {'value': key, 'chain': spec if isinstance(spec, list) else None,
                        'count': preset.get('count', 1), 'identity': preset.get('identity'),
                        'geometry': preset.get('geometry', False),
                        'rects': rects, 'tags': preset.get('tags', [preset.get('tag', 'input')]),
                        'first_rect': rects[0] if rects else None,
                        'tag': preset.get('tag', target_tag), 'error': preset.get('error'),
                        'hasTarget': has_target}
                if preset.get('error'):
                    item['count'] = -1
                items.append(item)
        return {'ok': True, 'data': {'items': items, 'viewport': [1200, 800]}, 'ms': 1.0}

    def element_paths(self, locator, frame_path=None):
        return {'ok': True, 'data': list(self.paths), 'ms': 1.0}

    def _op(self, fn, *args, **kwargs):
        return {'ok': True, 'data': fn(*args, **kwargs), 'ms': 1.0}

    def _owner_locked(self, frame_path):
        return self

    def ele(self, locator, timeout=0.8):
        return None


class TestScoring(unittest.TestCase):

    def test_unique_hit_beats_everything(self):
        cand = {'k': 'id', 'st': 96}
        item = {'value': '#a', 'count': 1, 'identity': True, 'geometry': True,
                'rects': [[0, 0, 10, 10]], 'tags': ['div'], 'first_rect': [0, 0, 10, 10],
                'tag': 'div', 'error': None, 'hasTarget': True}
        got = locators._score(cand, item)
        self.assertEqual(got['verdict'], 'unique')
        self.assertGreater(got['score'], 200)
        self.assertEqual(got['level'], 'ok')

    def test_multi_match_with_correct_first_is_warn(self):
        item = {'value': 'tag:input', 'count': 7, 'identity': True, 'geometry': True,
                'rects': [[0, 0, 10, 10]], 'tags': ['input'], 'first_rect': [0, 0, 10, 10],
                'tag': 'input', 'error': None, 'hasTarget': True}
        got = locators._score({'k': 'tagattr'}, item)
        self.assertEqual(got['verdict'], 'multi')
        self.assertEqual(got['level'], 'warn')

    def test_multi_match_with_wrong_first_is_danger(self):
        """匹配多个且首个不是目标：ele() 会操作错元素，必须按危险告警。"""
        item = {'value': 'tag:input', 'count': 7, 'identity': False, 'geometry': False,
                'rects': [[0, 0, 10, 10]], 'tags': ['input'], 'first_rect': [0, 0, 10, 10],
                'tag': 'input', 'error': None, 'hasTarget': True}
        got = locators._score({'k': 'tagattr'}, item)
        self.assertEqual(got['verdict'], 'wrong')
        self.assertEqual(got['level'], 'danger')

    def test_multi_match_without_target_info_is_neutral_warn(self):
        item = {'value': 'tag:input', 'count': 7, 'identity': None, 'geometry': False,
                'rects': [[0, 0, 10, 10]], 'tags': ['input'], 'first_rect': [0, 0, 10, 10],
                'tag': 'input', 'error': None, 'hasTarget': False}
        got = locators._score({'k': 'tagattr'}, item)
        self.assertEqual(got['verdict'], 'multi')

    def test_wrong_first_match_is_danger(self):
        item = {'value': '#wrong', 'count': 1, 'identity': False, 'geometry': False,
                'rects': [[5, 5, 9, 9]], 'tags': ['a'], 'first_rect': [5, 5, 9, 9],
                'tag': 'a', 'error': None, 'hasTarget': True}
        got = locators._score({'k': 'id', 'st': 96}, item)
        self.assertEqual(got['verdict'], 'wrong')
        self.assertEqual(got['level'], 'danger')

    def test_zero_match_is_none(self):
        item = {'value': '#nope', 'count': 0, 'identity': None, 'geometry': False,
                'rects': [], 'tags': [], 'first_rect': None, 'tag': None, 'error': None,
                'hasTarget': True}
        self.assertEqual(locators._score({'k': 'id'}, item)['verdict'], 'none')

    def test_invalid_syntax_is_reported(self):
        item = {'value': 'css:::', 'count': -1, 'identity': None, 'geometry': False,
                'rects': [], 'tags': [], 'first_rect': None, 'tag': None,
                'error': 'invalid selector', 'hasTarget': True}
        got = locators._score({'k': 'css'}, item)
        self.assertEqual(got['verdict'], 'invalid')
        self.assertEqual(got['level'], 'danger')
        self.assertIn('invalid selector', got['error'])

    def test_geometry_only_still_counts_as_hit(self):
        item = {'value': 'css:div', 'count': 1, 'identity': None, 'geometry': True,
                'rects': [[0, 0, 10, 10]], 'tags': ['div'], 'first_rect': [0, 0, 10, 10],
                'tag': 'div', 'error': None, 'hasTarget': True}
        self.assertEqual(locators._score({'k': 'css'}, item)['verdict'], 'unique')

    def test_accepted_without_identity_probe(self):
        item = {'value': 'css:div', 'count': 1, 'identity': None, 'geometry': False,
                'rects': [[0, 0, 10, 10]], 'tags': ['div'], 'first_rect': [0, 0, 10, 10],
                'tag': 'div', 'error': None, 'hasTarget': True}
        self.assertEqual(locators._score({'k': 'css'}, item)['verdict'], 'ok')

    def test_kind_guessing(self):
        cases = {'xpath://div': 'xpath', 'text=登录': 'text', 'text:登录': 'text',
                 'css:div.a': 'css', 'tag:input@name=x': 'tagattr', '@id=a': 'attr',
                 '#a': 'id', '.a': 'class'}
        for loc, want in cases.items():
            with self.subTest(loc=loc):
                self.assertEqual(locators._guess_kind(loc), want)

    def test_pick_best_prefers_unique_over_higher_base_score(self):
        unique = {'verdict': 'unique', 'score': 150}
        multi = {'verdict': 'multi', 'score': 900}
        self.assertIs(locators._pick_best([multi, unique]), unique)

    def test_pick_best_falls_back_to_multi(self):
        multi = {'verdict': 'multi', 'score': 20}
        bad = {'verdict': 'none', 'score': 5}
        self.assertIs(locators._pick_best([bad, multi]), multi)


class TestStabilityGrade(unittest.TestCase):

    def _item(self, **kw):
        base = {'value': '#a', 'count': 1, 'identity': True, 'geometry': True,
                'rects': [[0, 0, 10, 10]], 'tags': ['input'],
                'first_rect': [0, 0, 10, 10], 'tag': 'input', 'error': None,
                'hasTarget': True}
        base.update(kw)
        return base

    def test_static_id_is_grade_a(self):
        got = locators._score({'k': 'id', 'st': 96}, self._item())
        self.assertEqual(got['grade'], 'A')
        self.assertGreaterEqual(got['stabilityScore'], 85)
        self.assertLessEqual(got['stabilityScore'], 100)
        self.assertIn('唯一命中目标', [f['label'] for f in got['factors']])

    def test_multi_lowers_score_below_unique(self):
        unique = locators._score({'k': 'id', 'st': 96}, self._item())
        multi = locators._score({'k': 'id', 'st': 96}, self._item(count=4))
        self.assertLess(multi['stabilityScore'], unique['stabilityScore'])
        self.assertNotEqual(multi['grade'], 'A')

    def test_wrong_and_none_are_grade_d(self):
        wrong = locators._score({'k': 'css', 'st': 60},
                                self._item(identity=False, geometry=False, count=2))
        none = locators._score({'k': 'css', 'st': 60},
                               self._item(count=0, identity=None))
        self.assertEqual(wrong['grade'], 'D')
        self.assertLessEqual(wrong['stabilityScore'], 20)
        self.assertEqual(none['grade'], 'D')
        self.assertEqual(none['stabilityScore'], 0)

    def test_dynamic_uuid_id_capped_at_b(self):
        value = '#a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        got = locators._score({'k': 'id', 'st': 96}, self._item(value=value))
        self.assertEqual(got['verdict'], 'unique')
        self.assertEqual(got['grade'], 'B')
        self.assertIn('属性值像 UUID / 哈希 / 时间戳',
                      [f['label'] for f in got['factors']])

    def test_css_in_js_class_capped_at_c(self):
        got = locators._score({'k': 'class', 'st': 66},
                              self._item(value='.css-1x9f2ab'))
        self.assertEqual(got['grade'], 'C')
        self.assertIn('类名像 CSS-in-JS 自动生成',
                      [f['label'] for f in got['factors']])

    def test_deep_css_path_scores_lower_than_shallow(self):
        shallow = locators._score({'k': 'css', 'st': 54},
                                  self._item(value='css:div.a'))
        deep = locators._score({'k': 'css', 'st': 54},
                               self._item(value='css:div.a>div.b>div.c>div.d>input.e'))
        self.assertLess(deep['stabilityScore'], shallow['stabilityScore'])
        self.assertIn(deep['grade'], ('C', 'D'))

    def test_unknown_identity_is_penalized(self):
        known = locators._score({'k': 'css', 'st': 54}, self._item())
        unknown = locators._score({'k': 'css', 'st': 54},
                                  self._item(identity=None))
        self.assertLess(unknown['stabilityScore'], known['stabilityScore'])
        self.assertIn('无法做身份比对', [f['label'] for f in unknown['factors']])

    def test_score_always_in_range_and_grade_known(self):
        items = [self._item(), self._item(identity=False, count=2),
                 self._item(count=0, identity=None)]
        for item in items:
            for cand in ({'k': 'id', 'st': 100}, {'k': 'xpath', 'st': 0}):
                with self.subTest(cand=cand, count=item['count']):
                    got = locators._score(cand, item)
                    self.assertGreaterEqual(got['stabilityScore'], 0)
                    self.assertLessEqual(got['stabilityScore'], 100)
                    self.assertIn(got['grade'], 'ABCD')
                    self.assertTrue(got['gradeLabel'])
                    self.assertTrue(got['factors'])


class TestBuildPlan(unittest.TestCase):

    def _desc(self):
        return {
            'tag': 'input', 'id': 'username', 'rect': [10, 20, 100, 30],
            'cands': [
                {'k': 'id', 'loc': '#username', 'st': 96, 'why': '唯一 id',
                 'probe': {'t': 'css', 'v': '#username'}},
                {'k': 'attr', 'loc': '@name=username', 'st': 72, 'why': '属性',
                 'probe': {'t': 'attr', 'v': ['name', 'username']}},
                {'k': 'css', 'loc': 'css:input#username', 'st': 50, 'why': '路径',
                 'probe': {'t': 'css', 'v': 'input#username'}},
                {'k': 'text', 'loc': 'text=', 'st': 36, 'why': '空文本候选',
                 'probe': {'t': 'text', 'v': ''}},
            ],
            'candCheck': [
                {'count': 1, 'hits': True, 'idx': 0},
                {'count': 1, 'hits': True, 'idx': 0},
                {'count': 2, 'hits': True, 'idx': 0},
                {'count': 0, 'hits': False, 'idx': -1},
            ],
            'chain': [{'tag': 'div', 'id': '', 'cls': ['row'],
                       'cands': [{'k': 'class', 'loc': '.row'}]}],
            'framePath': [], 'shadow': [],
        }

    def test_recommended_is_the_unique_identity_hit(self):
        table = {
            '#username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
            '@name=username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
            'css:input#username': {'count': 2, 'identity': True, 'tag': 'input'},
            'text=': {'count': 0},
            '.row >> #username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
        }
        session = FakeSession(table)
        plan = locators.build_locator_plan(session, self._desc(), [])
        self.assertTrue(plan['ok'])
        self.assertEqual(plan['recommended']['value'], '#username')
        self.assertEqual(plan['recommended']['verdict'], 'unique')
        self.assertTrue(plan['recommended']['identity'])
        self.assertEqual(plan['overall']['grade'], plan['recommended']['grade'])
        self.assertEqual(plan['overall']['score'],
                         plan['recommended']['stabilityScore'])

    def test_multi_match_candidate_is_flagged(self):
        table = {
            '#username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
            '@name=username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
            'css:input#username': {'count': 2, 'identity': True, 'tag': 'input'},
        }
        session = FakeSession(table)
        plan = locators.build_locator_plan(session, self._desc(), [])
        by_val = {c['value']: c for c in plan['candidates']}
        self.assertEqual(by_val['css:input#username']['verdict'], 'multi')
        self.assertEqual(by_val['css:input#username']['count'], 2)

    def test_zero_match_candidate_is_marked_none(self):
        table = {'#username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'}}
        session = FakeSession(table)
        plan = locators.build_locator_plan(session, self._desc(), [])
        by_val = {c['value']: c for c in plan['candidates']}
        self.assertEqual(by_val['@name=username']['verdict'], 'none')

    def test_duplicate_expressions_are_deduplicated(self):
        desc = self._desc()
        desc['cands'].append({'k': 'css', 'loc': '#username', 'st': 40, 'why': '重复',
                              'probe': {'t': 'css', 'v': '#username'}})
        desc['candCheck'].append({'count': 1, 'hits': True, 'idx': 0})
        session = FakeSession({'#username': {'count': 1, 'identity': True, 'tag': 'input'}})
        plan = locators.build_locator_plan(session, desc, [])
        values = [c['value'] for c in plan['candidates']]
        self.assertEqual(len(values), len(set(v if isinstance(v, str) else str(v) for v in values)))

    def test_relative_candidate_is_generated(self):
        table = {
            '#username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
            '.row': {'count': 1, 'identity': True, 'tag': 'div'},
            '.row >> #username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
        }
        session = FakeSession(table)
        plan = locators.build_locator_plan(session, self._desc(), [])
        rel = [c for c in plan['candidates'] if c['kind'] == 'relative']
        self.assertTrue(rel, '应当生成相对定位候选')
        self.assertEqual(rel[0]['value'], ['.row', '#username'])
        self.assertEqual(rel[0]['verdict'], 'unique')

    def test_relative_candidate_requires_unique_anchor(self):
        """锚点自身不唯一时不应给出相对定位候选（否则等于在整页里找第一个）。"""
        table = {'#username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
                 '.row': {'count': 5, 'identity': True, 'tag': 'div'},
                 '.row >> #username': {'count': 1, 'identity': True, 'tag': 'input'}}
        plan = locators.build_locator_plan(FakeSession(table), self._desc(), [])
        rel = [c for c in plan['candidates'] if c['kind'] == 'relative']
        self.assertFalse(rel)

    def test_relative_candidate_requires_in_anchor_hit(self):
        """尾表达式在锚点内必须唯一命中目标，否则放弃相对定位。"""
        table = {'#username': {'count': 1, 'identity': True, 'geometry': True, 'tag': 'input'},
                 '.row': {'count': 1, 'identity': True, 'tag': 'div'},
                 '.row >> #username': {'count': 3, 'identity': False, 'tag': 'input'},
                 '.row >> @name=username': {'count': 2, 'identity': True, 'tag': 'input'}}
        plan = locators.build_locator_plan(FakeSession(table), self._desc(), [])
        rel = [c for c in plan['candidates'] if c['kind'] == 'relative']
        self.assertFalse(rel)

    def test_session_error_is_propagated(self):
        class Broken(FakeSession):
            def validate_specs(self, *a, **k):
                return {'ok': False, 'error': '浏览器已断开', 'error_type': 'PageDisconnectedError'}
        plan = locators.build_locator_plan(Broken({}), self._desc(), [])
        self.assertFalse(plan['ok'])
        self.assertIn('浏览器已断开', plan['error'])

    def test_all_specs_sent_to_session_in_one_batch(self):
        session = FakeSession({'#username': {'count': 1, 'identity': True, 'tag': 'input'}})
        locators.build_locator_plan(session, self._desc(), [])
        self.assertTrue(session.calls)
        self.assertLessEqual(len(session.calls[0]), 12)


if __name__ == '__main__':
    unittest.main(verbosity=2)
