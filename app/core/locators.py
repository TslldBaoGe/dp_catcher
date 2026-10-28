"""定位候选的评分与复核。

流程：
  1. 页内 JS 已给出候选表达式与「是否唯一 / 是否命中目标」的初判；
  2. 本模块把这些候选交给 DrissionPage 真实解析一次（validate_specs），
     拿到权威的匹配数量、元素标签、几何矩形；
  3. 综合「唯一性 + 身份命中 + 几何一致 + 语法稳定性」给出结论与推荐顺序。
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import settings

# 定位方式的基础稳定性权重（0-100）
KIND_STABILITY = {
    'id': 96,
    'attr': 84,
    'tagattr': 74,
    'class': 66,
    'css': 54,
    'xpath': 44,
    'text': 36,
    'relative': 88,
}

VERDICT_META = {
    'best': {'label': '推荐', 'level': 'ok', 'note': '唯一匹配且精确命中目标元素'},
    'unique': {'label': '可用', 'level': 'ok', 'note': '唯一匹配并命中目标元素'},
    'ok': {'label': '可用', 'level': 'ok', 'note': '命中目标元素'},
    'multi': {'label': '不唯一', 'level': 'warn', 'note': '匹配多个元素，仅首个被使用'},
    'wrong': {'label': '错位', 'level': 'danger', 'note': '首个匹配不是目标元素'},
    'none': {'label': '未匹配', 'level': 'danger', 'note': '页面上找不到该元素'},
    'invalid': {'label': '语法错误', 'level': 'danger', 'note': 'DrissionPage 无法解析该表达式'},
}

GRADE_META = {
    'A': {'label': '稳定', 'level': 'ok', 'note': '定位可靠，适合长期使用'},
    'B': {'label': '较稳', 'level': 'ok', 'note': '可用，页面改版时需要留意'},
    'C': {'label': '一般', 'level': 'warn', 'note': '建议换用更稳的定位写法'},
    'D': {'label': '不稳', 'level': 'danger', 'note': '不建议在生产脚本里依赖'},
}

_DYNAMIC_STRONG = (
    re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'),
    re.compile(r'^[0-9a-fA-F]{16,}$'),
    re.compile(r'.*\d{10,}.*'),
)
_DYNAMIC_WEAK = (
    re.compile(r'^\d{3,}$'),
    re.compile(r'^(ember|react|vue|radix|headlessui|mui|el|ext)-?\d+$', re.I),
)
_CSS_IN_JS = re.compile(r'^(css|sc|jsx|emotion|styled)-[a-z0-9]{4,}$', re.I)


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _spec_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return ' >> '.join(str(v) for v in value)
    return str(value or '')


def _grade_of(score: float) -> str:
    if score >= 85:
        return 'A'
    if score >= 70:
        return 'B'
    if score >= 50:
        return 'C'
    return 'D'


def _structure_penalty(kind: str, value) -> float:
    """结构越深、层级下标越多，越容易随页面改版失效。"""
    text = _spec_text(value)
    if kind == 'xpath':
        depth = text.count('/')
        nth = text.count('[')
        return min(25.0, max(0.0, (depth - 2) * 1.5) + nth * 2.0)
    if kind == 'css':
        depth = text.count('>')
        nth = text.count(':nth-of-type')
        return min(25.0, depth * 3.0 + nth * 4.0)
    if kind == 'relative' and isinstance(value, (list, tuple)) and len(value) > 2:
        return min(12.0, (len(value) - 2) * 4.0)
    return 0.0


def _dynamic_penalty(kind: str, value) -> tuple[float, str]:
    """识别 UUID / 哈希 / 时间戳 / 自增数字 / CSS-in-JS 类名等易变值。"""
    if kind not in ('id', 'attr', 'tagattr', 'class'):
        return 0.0, ''
    text = _spec_text(value)
    for token in re.split(r'[=\s]+', text):
        token = token.strip('"\'#.@')
        if not token or token in ('tag', 'class'):
            continue
        if _CSS_IN_JS.match(token):
            return 8.0, '类名像 CSS-in-JS 自动生成'
        if any(p.match(token) for p in _DYNAMIC_STRONG):
            return 12.0, '属性值像 UUID / 哈希 / 时间戳'
        if any(p.match(token) for p in _DYNAMIC_WEAK):
            return 6.0, '属性值像自增数字或框架生成 id'
    if kind in ('class', 'tagattr'):
        cls_part = text.split('class=', 1)[-1] if 'class=' in text else text
        if len(cls_part.split()) > 3:
            return 5.0, 'class 组合过多，易随样式变化'
    return 0.0, ''


def _stability_of(cand: dict, item: dict, kind: str, value, verdict: str) -> dict:
    """把「定位类型 + 校验结论 + 结构/动态特征」折算成 0-100 的稳定性分。"""
    base = max(KIND_STABILITY.get(kind, 50), _num(cand.get('st'), 0))
    base = min(base, 100.0)
    score = base
    cap = 100.0
    factors = [{'label': '类型基础分（%s）' % _kind_why(kind), 'delta': round(base, 1)}]

    penalty = _structure_penalty(kind, value)
    if penalty:
        score -= penalty
        if penalty >= 12:
            cap = min(cap, 79.0)
        factors.append({'label': '结构路径较深', 'delta': -round(penalty, 1)})

    penalty, label = _dynamic_penalty(kind, value)
    if penalty:
        score -= penalty
        if label == '类名像 CSS-in-JS 自动生成':
            cap = min(cap, 69.0)
        elif label == '属性值像 UUID / 哈希 / 时间戳':
            cap = min(cap, 79.0)
        elif label == '属性值像自增数字或框架生成 id':
            cap = min(cap, 84.0)
        factors.append({'label': label, 'delta': -round(penalty, 1)})

    if verdict in ('unique', 'ok'):
        score += 6
        factors.append({'label': '唯一命中目标', 'delta': 6})
    elif verdict == 'multi':
        score -= 12
        factors.append({'label': '匹配多个元素', 'delta': -12})
    elif verdict == 'wrong':
        score = min(score, 20.0)
        factors.append({'label': '首个匹配不是目标（错位）', 'delta': 0})
    elif verdict in ('none', 'invalid'):
        score = 0.0
        factors.append({'label': '未匹配或语法错误', 'delta': 0})

    if item.get('identity') is None and verdict in ('unique', 'ok'):
        score -= 6
        factors.append({'label': '无法做身份比对', 'delta': -6})
    if cand.get('jsCount') == 1 and cand.get('jsHits'):
        score += 3
        factors.append({'label': '页内初判唯一命中', 'delta': 3})

    score = max(0.0, min(100.0, min(score, cap)))
    grade = _grade_of(score)
    return {
        'stabilityScore': int(round(score)),
        'grade': grade,
        'gradeLabel': GRADE_META[grade]['label'],
        'gradeLevel': GRADE_META[grade]['level'],
        'gradeNote': GRADE_META[grade]['note'],
        'factors': factors,
    }


def _overall(best: Optional[dict]) -> Optional[dict]:
    if not best:
        return None
    return {
        'grade': best['grade'],
        'gradeLabel': best['gradeLabel'],
        'gradeLevel': best['gradeLevel'],
        'gradeNote': best['gradeNote'],
        'score': best['stabilityScore'],
        'verdict': best['verdict'],
        'verdictLabel': best['verdictLabel'],
        'kind': best['kind'],
        'count': best['count'],
        'value': best['value'],
        'why': best['why'],
    }


def build_locator_plan(session, desc: dict, frame_path: Optional[list] = None,
                       extra_kinds: Optional[list] = None) -> dict:
    """把一次拾取结果整理成「候选定位方案 + 推荐表达式 + 校验结论」。"""
    frame_path = list(frame_path or [])
    target_rect = desc.get('rect')
    target_tag = (desc.get('tag') or '').lower()

    raw = list(desc.get('cands') or [])
    js_check = desc.get('candCheck') or []
    for i, cand in enumerate(raw):
        chk = js_check[i] if i < len(js_check) else {}
        cand['jsCount'] = chk.get('count', -1)
        cand['jsHits'] = chk.get('hits')
        cand['jsIdx'] = chk.get('idx')

    # 去重（同一表达式保留稳定性最高的那条）
    seen: dict[str, dict] = {}
    for cand in raw:
        loc = cand.get('loc')
        if not loc:
            continue
        prev = seen.get(loc)
        if prev is None or _num(cand.get('st')) > _num(prev.get('st')):
            seen[loc] = cand
    cands = list(seen.values())

    # Shadow DOM 内部元素：候选必须改写成「宿主 → shadow:内部表达式」才能被 DrissionPage 找到
    if desc.get('inShadow'):
        wrapped = []
        for cand in cands:
            spec = _shadow_spec(desc, cand['loc'])
            if spec is None:
                continue
            cand = dict(cand)
            cand['loc'] = spec
            cand['why'] = (cand.get('why') or '') + '（Shadow DOM 内部）'
            wrapped.append(cand)
        cands = wrapped

    # JS 判断能唯一命中的候选优先复核，其余排在后面
    cands.sort(key=lambda c: (0 if c.get('jsCount') == 1 and c.get('jsHits') else 1,
                              -_num(c.get('st'))))
    cands = cands[: max(1, settings.max_candidates)]

    specs: list = [c['loc'] for c in cands]

    result = session.validate_specs(specs, frame_path, target_rect, target_tag)
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error'), 'error_type': result.get('error_type'),
                'candidates': [], 'recommended': None}

    items = (result.get('data') or {}).get('items') or []
    checked = []
    for i, item in enumerate(items):
        cand = cands[i] if i < len(cands) else {}
        checked.append(_score(cand, item))

    # 由 DrissionPage 侧补充它自己生成的 css / xpath 路径（权威兜底）
    recommended = _pick_best(checked)
    if recommended:
        extra = _drission_paths(session, recommended, frame_path, target_rect, target_tag)
        for ex in extra:
            if ex['value'] not in [c['value'] for c in checked]:
                checked.append(ex)

    # 相对定位候选：最近稳定祖先 + 相对子级
    rel = _relative_candidate(session, desc, checked, frame_path, target_rect, target_tag)
    if rel:
        checked.append(rel)

    checked.sort(key=lambda c: (-c['score'], c['kind'] != 'relative'))
    best = _pick_best(checked)
    return {
        'ok': True,
        'candidates': checked,
        'recommended': best,
        'overall': _overall(best),
        'viewport': (result.get('data') or {}).get('viewport'),
        'target': {
            'tag': target_tag, 'rect': target_rect,
            'identityChecked': any(c.get('identity') is not None for c in checked),
        },
    }


def _score(cand: dict, item: dict) -> dict:
    """综合 JS 初判与 DrissionPage 复核结果打分。

    判定优先看「首个匹配是不是目标元素」——因为 ele() 只会取首个匹配，
    首个错位比「匹配多个」危险得多。
    """
    kind = cand.get('k') or _guess_kind(cand.get('loc', ''))
    # 链式（相对定位）用步骤列表作为 value，才能直接交给 codegen 和DrissionPage 使用
    value = item.get('chain') or item.get('value') or cand.get('loc')
    count = item.get('count', -1)
    error = item.get('error')
    identity = item.get('identity')
    geometry = bool(item.get('geometry'))
    has_target = bool(item.get('hasTarget', True))
    hit = bool(identity) or geometry

    if error:
        verdict = 'invalid'
    elif count <= 0:
        verdict = 'none'
    elif identity is False and not geometry:
        verdict = 'wrong'
    elif count == 1:
        verdict = 'unique' if hit else 'ok'
    else:
        # 多个匹配：只有确认首个是目标才算「不唯一但可用」，否则属错位
        verdict = 'multi' if (hit or not has_target) else 'wrong'

    stability = _num(cand.get('st'), KIND_STABILITY.get(kind, 50))
    score = stability
    if verdict == 'unique':
        score += 120
    elif verdict == 'ok':
        score += 90
    elif verdict == 'multi':
        score += 10
    elif verdict in ('wrong', 'none', 'invalid'):
        score -= 200
    if cand.get('jsCount') == 1 and cand.get('jsHits'):
        score += 12
    if kind == 'relative' and verdict in ('unique', 'ok'):
        score += 18
    if isinstance(value, list):
        score -= 4

    return {
        'kind': kind,
        'value': value,
        'chain': item.get('chain'),
        'why': cand.get('why') or _kind_why(kind),
        'count': count,
        'identity': identity,
        'geometry': geometry,
        'verdict': verdict,
        'verdictLabel': VERDICT_META[verdict]['label'],
        'level': VERDICT_META[verdict]['level'],
        'note': VERDICT_META[verdict]['note'],
        'score': round(score, 1),
        'stability': round(stability, 1),
        'tags': item.get('tags') or [],
        'rects': item.get('rects') or [],
        'firstRect': item.get('first_rect'),
        'error': error,
        'truncated': bool(item.get('truncated')),
        **_stability_of(cand, item, kind, value, verdict),
    }


def _guess_kind(loc: str) -> str:
    if not isinstance(loc, str):
        return 'relative'
    if loc.startswith('xpath:'):
        return 'xpath'
    if loc.startswith('text=') or loc.startswith('text:'):
        return 'text'
    if loc.startswith('css:'):
        return 'css'
    if loc.startswith('tag:'):
        return 'tagattr'
    if loc.startswith('@'):
        return 'attr'
    if loc.startswith('#'):
        return 'id'
    if loc.startswith('.'):
        return 'class'
    return 'css'


def score_single(session, spec, frame_path: Optional[list], desc: dict) -> Optional[dict]:
    """校验用户手写/切换的单条表达式，返回与候选清单一致的评分结构。"""
    if spec is None:
        return None
    target_rect = desc.get('rect')
    target_tag = (desc.get('tag') or '').lower()
    res = session.validate_specs([spec], frame_path, target_rect, target_tag)
    if not res.get('ok'):
        return None
    items = (res.get('data') or {}).get('items') or []
    if not items:
        return None
    tail = spec[-1] if isinstance(spec, (list, tuple)) and spec else spec
    cand = {'k': _guess_kind(tail if isinstance(tail, str) else ''),
            'loc': spec, 'why': '手动指定的表达式'}
    return _score(cand, items[0])


def _shadow_spec(desc: dict, inner: str):
    """把 Shadow DOM 内部的定位表达式改写成链式表达。

    例：['#shadowHost', 'shadow:#shadowBtn'] → page.ele('#shadowHost').shadow_root.ele('#shadowBtn')
    """
    if not isinstance(inner, str) or not inner:
        return None
    chain = desc.get('shadow') or []
    steps = []
    for i, item in enumerate(chain):
        sel = item.get('selector') if isinstance(item, dict) else None
        if not sel and isinstance(item, dict) and item.get('id'):
            sel = '#' + item['id']
        if not sel:
            return None
        steps.append(('shadow:' + sel) if i else sel)
    if not steps:
        return None
    steps.append('shadow:' + inner)
    return steps


def _kind_why(kind: str) -> str:
    return {
        'id': '唯一 id',
        'attr': '属性定位',
        'tagattr': '标签 + 属性',
        'class': 'class 定位',
        'css': 'CSS 选择器',
        'xpath': 'XPath 路径',
        'text': '文本定位',
        'relative': '相对定位（祖先 + 子级）',
    }.get(kind, '定位表达式')


def _pick_best(checked: list) -> Optional[dict]:
    if not checked:
        return None
    good = [c for c in checked if c['verdict'] in ('unique', 'ok')]
    pool = good or [c for c in checked if c['verdict'] == 'multi'] or checked
    return max(pool, key=lambda c: c['score'])


def _drission_paths(session, best: dict, frame_path: list, target_rect, target_tag) -> list:
    """用 DrissionPage 自己的 css_path / xpath 作为兜底候选。"""
    res = session.element_paths(best['value'], frame_path)
    if not res.get('ok') or not res.get('data'):
        return []
    specs = []
    for p in res['data']:
        if p.get('css'):
            specs.append('css:' + p['css'])
        if p.get('xpath'):
            specs.append('xpath:' + p['xpath'])
    if not specs:
        return []
    val = session.validate_specs(specs, frame_path, target_rect, target_tag)
    if not val.get('ok'):
        return []
    out = []
    for item in (val.get('data') or {}).get('items') or []:
        kind = 'xpath' if str(item['value']).startswith('xpath:') else 'css'
        out.append(_score({'k': kind, 'st': KIND_STABILITY[kind],
                           'why': 'DrissionPage 生成的' + ('XPath' if kind == 'xpath' else 'CSS 路径')},
                          item))
    return out


# 相对定位：尾表达式必须是「DrissionPage 会严格限定在锚点内查找」的语法。
# css / xpath 路径在元素内查找时可能退化成整页查找（例如 css 以 #id 开头时），
# 那样得到的「相对定位」只是巧合命中，不具备抗结构变化的意义，因此排除。
RELATIVE_TAIL_KINDS = ('id', 'attr', 'tagattr', 'class', 'text')
# 锚点同样只用语义化定位（id / 属性 / class）；css 结构路径当锚点没有价值
RELATIVE_ANCHOR_KINDS = ('id', 'attr', 'tagattr', 'class')
RELATIVE_ANCHOR_SKIP = ('html', 'body')


def _relative_candidate(session, desc: dict, checked: list, frame_path: list,
                        target_rect, target_tag) -> Optional[dict]:
    """构造「祖先唯一 + 锚点内唯一」的相对定位候选。

    典型收益：`page.ele('@data-uid=1002').ele('tag:td@@class=name')`。
    `tag:td@@class=name` 全页有 3 个，但限定在某一行里只有 1 个 —— 这正是相对定位的价值。
    """
    tails = []
    seen_tails = set()
    for cand in desc.get('cands') or []:
        kind = cand.get('k')
        loc = cand.get('loc')
        if kind not in RELATIVE_TAIL_KINDS or not loc or loc in seen_tails:
            continue
        seen_tails.add(loc)
        tails.append({'loc': loc, 'k': kind, 'st': _num(cand.get('st'))})
    if not tails:
        return None
    tails.sort(key=lambda t: -t['st'])
    tails = tails[:4]

    anchor_specs = []
    seen_anchor = set()
    for anc in (desc.get('chain') or [])[:3]:
        for ac in anc.get('cands') or []:
            loc = ac.get('loc')
            if ac.get('k') not in RELATIVE_ANCHOR_KINDS or not loc or loc in seen_anchor:
                continue
            if loc.strip('#.').lower() in RELATIVE_ANCHOR_SKIP:
                continue
            seen_anchor.add(loc)
            anchor_specs.append(loc)
    if not anchor_specs:
        return None

    # 第一轮：锚点自身必须是唯一的
    res = session.validate_specs(anchor_specs[:6], frame_path, None, None)
    if not res.get('ok'):
        return None
    anchors = [i['value'] for i in (res.get('data') or {}).get('items') or []
               if not i.get('error') and i.get('count') == 1]
    if not anchors:
        return None

    # 第二轮：组合校验，尾表达式必须在锚点内唯一且命中目标
    combos = [[anchor, tail['loc']] for anchor in anchors[:2] for tail in tails]
    if not combos:
        return None
    meta = {' >> '.join(c): t for c, t in zip(combos, [t for a in anchors[:2] for t in tails])}
    res2 = session.validate_specs(combos, frame_path, target_rect, target_tag)
    if not res2.get('ok'):
        return None

    best = None
    for item in (res2.get('data') or {}).get('items') or []:
        if item.get('error') or item.get('count') != 1 or not item.get('identity'):
            continue
        tail_kind = (meta.get(item['value']) or {}).get('k', '')
        scored = _score({'k': 'relative', 'st': KIND_STABILITY['relative'],
                         'why': '相对定位：先锁定稳定祖先，再在其内部用 %s 定位（抗结构变化）'
                                % (tail_kind or '子级定位')}, item)
        if best is None or scored['score'] > best['score']:
            best = scored
    return best
