"""业务编排：把「拾取 → 候选复核 → 高亮 → 生成代码」串成一次调用。

前端只需一次请求即可拿到完整结果，避免多次往返造成 UI 状态撕裂。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from ..config import settings
from . import codegen
from .browser import session
from .locators import build_locator_plan


class CatcherState:
    """一次会话内的捕获状态（单用户本地工具，进程内保存即可）。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.desc: Optional[dict] = None
        self.plan: Optional[dict] = None
        self.spec = None
        self.frame_path: list = []
        self.highlight_spec = None
        self.history: list = []
        self.actions: list = []
        self._seq = 0

    def next_id(self) -> int:
        self._seq += 1
        return self._seq

    def as_dict(self) -> dict:
        return {
            'hasElement': self.desc is not None,
            'spec': self.spec,
            'tag': (self.desc or {}).get('tag'),
            'historyCount': len(self.history),
        }


state = CatcherState()


def _plan_and_highlight(desc: dict, frame_path: list, spec=None) -> dict:
    plan = build_locator_plan(session, desc, frame_path)
    chosen = spec
    if chosen is None and plan.get('ok') and plan.get('recommended'):
        chosen = plan['recommended']['value']
    highlight = None
    if chosen is not None:
        res = session.highlight(chosen, frame_path)
        if res.get('ok'):
            highlight = res['data']
    return {'plan': plan, 'spec': chosen, 'highlight': highlight}


def pick_workflow(x: float, y: float, frame_path: Optional[list] = None) -> dict:
    frame_path = list(frame_path or [])
    picked = session.pick_at(x, y, frame_path)
    if not picked.get('ok'):
        return {'ok': False, 'error': picked.get('error'), 'error_type': picked.get('error_type'),
                'ms': picked.get('ms')}
    desc = picked['data']
    with state.lock:
        state.desc = desc
        state.frame_path = frame_path
        state.actions = []
    bundle = _plan_and_highlight(desc, frame_path)
    _store(bundle, desc, frame_path)
    return _envelope(bundle, desc, picked.get('ms'))


def adopt_desc(desc: dict) -> dict:
    """页面侧已有元素描述（如拾取器内切换父级后）→ 校验 + 高亮 + 生成代码。"""
    frame_path = list(desc.get('framePath') or [])
    with state.lock:
        state.desc = desc
        state.frame_path = frame_path
        state.actions = []
    bundle = _plan_and_highlight(desc, frame_path)
    _store(bundle, desc, frame_path)
    return _envelope(bundle, desc, None)


def apply_workflow(spec, frame_path: Optional[list] = None) -> dict:
    """用户切换到另一条候选表达式：重新校验 + 高亮 + 生成代码。"""
    with state.lock:
        desc = state.desc
        if frame_path is None:
            frame_path = list(state.frame_path)
    if not desc:
        return {'ok': False, 'error': '还没有捕获任何元素', 'error_type': 'NoElement'}
    frame_path = list(frame_path or [])
    bundle = _plan_and_highlight(desc, frame_path, spec=spec)
    _merge_applied(spec, frame_path, desc, bundle)
    _store(bundle, desc, frame_path)
    return _envelope(bundle, desc, None)


def _merge_applied(spec, frame_path: list, desc: dict, bundle: dict) -> None:
    """把手动指定/切换的表达式并入候选清单，让用户直接看到它的校验结论。"""
    from . import locators as _loc
    scored = _loc.score_single(session, spec, frame_path, desc)
    plan = bundle.get('plan')
    if not scored or not plan or not plan.get('ok'):
        return
    cands = list(plan.get('candidates') or [])
    key = _spec_key(scored['value'])
    replaced = False
    for i, cand in enumerate(cands):
        if _spec_key(cand['value']) == key:
            cands[i] = scored
            replaced = True
            break
    if not replaced:
        cands.insert(0, scored)
    plan['candidates'] = cands
    if scored['verdict'] in ('unique', 'ok') and scored['count'] > 0:
        plan['recommended'] = scored


def _spec_key(value) -> str:
    if isinstance(value, (list, tuple)):
        return ' >> '.join(str(v) for v in value)
    return str(value)


def usage_workflow(spec, frame_path: Optional[list] = None, opts: Optional[dict] = None) -> dict:
    """生成当前元素/定位表达式的常用使用片段。"""
    opts = opts or {}
    with state.lock:
        desc = state.desc
        plan = state.plan or {}
        if frame_path is None:
            frame_path = list(state.frame_path)
    if not desc or spec is None:
        return {'ok': False, 'error': '还没有捕获任何元素', 'error_type': 'NoElement'}
    frame_path = list(frame_path or [])
    candidate = None
    for item in plan.get('candidates') or []:
        if _spec_key(item.get('value')) == _spec_key(spec):
            candidate = item
            break
    if candidate is None:
        candidate = plan.get('recommended')
    return {
        'ok': True,
        'spec': spec,
        'specLabel': _spec_label(spec),
        'usages': codegen.usage_snippets(
            spec, frame_path, desc.get('shadow'),
            text=opts.get('text') or '',
            attr_name=opts.get('attrName') or 'href'),
        'candidate': candidate,
        'overall': plan.get('overall'),
        'shadow': desc.get('shadow') or [],
    }


def refresh_workflow() -> dict:
    """页面变化后重新校验当前表达式（元素是否还在、位置是否变了）。"""
    with state.lock:
        desc = state.desc
        spec = state.spec
        frame_path = list(state.frame_path)
    if not desc or spec is None:
        return {'ok': False, 'error': '还没有捕获任何元素', 'error_type': 'NoElement'}
    return apply_workflow(spec, frame_path)


def _store(bundle: dict, desc: dict, frame_path: list) -> None:
    with state.lock:
        state.plan = bundle['plan']
        state.spec = bundle['spec']
        state.frame_path = list(frame_path)


def _envelope(bundle: dict, desc: dict, pick_ms) -> dict:
    spec = bundle['spec']
    shadow = desc.get('shadow') or []
    plan = bundle['plan'] or {}
    best = plan.get('recommended') if plan.get('ok') else None
    with state.lock:
        actions = [dict(a) for a in state.actions]
    label = _label(desc)
    step = {'name': label, 'spec': spec, 'framePath': desc.get('framePath'),
            'shadow': shadow, 'why': (best or {}).get('why', ''), 'actions': actions}
    return {
        'ok': True,
        'desc': desc,
        'plan': plan,
        'spec': spec,
        'specLabel': _spec_label(spec),
        'actions': actions,
        'highlight': bundle['highlight'],
        'code': {
            'locate': codegen.snippet_for(spec, 'get_text', frame_path=desc.get('framePath'),
                                         shadow_chain=shadow, text=label) if spec else '',
            'report': codegen.report_snippet(desc, spec) if spec else '',
            'script': codegen.full_script([step], url=desc.get('url') or '',
                                          window=[settings.window_width, settings.window_height]),
            'verdict': (best or {}).get('verdict'),
        },
        'ms': pick_ms,
        'ts': int(time.time() * 1000),
    }


def _spec_label(spec) -> str:
    if isinstance(spec, (list, tuple)):
        return ' >> '.join(str(s) for s in spec)
    return str(spec) if spec is not None else ''


def _label(desc: dict) -> str:
    tag = desc.get('tag') or 'element'
    if desc.get('id'):
        return '%s#%s' % (tag, desc['id'])
    cls = desc.get('cls') or []
    if cls:
        return '%s.%s' % (tag, cls[0])
    text = (desc.get('text') or '').strip()
    if text:
        return '%s(%s)' % (tag, text[:16])
    return tag


# ---------------------------------------------------------------- 捕获历史

def history_add(name: Optional[str] = None, actions: Optional[list] = None) -> dict:
    with state.lock:
        if not state.desc or state.spec is None:
            return {'ok': False, 'error': '还没有捕获任何元素', 'error_type': 'NoElement'}
        desc = state.desc
        best = (state.plan or {}).get('recommended') or {}
        entry = {
            'id': state.next_id(),
            'name': name or _label(desc),
            'spec': state.spec,
            'specLabel': _spec_label(state.spec),
            'framePath': list(desc.get('framePath') or []),
            'shadow': desc.get('shadow') or [],
            'why': best.get('why', ''),
            'tag': desc.get('tag'),
            'text': (desc.get('text') or '')[:60],
            'url': desc.get('url'),
            'count': best.get('count'),
            'verdict': best.get('verdict'),
            'stabilityScore': best.get('stabilityScore'),
            'grade': best.get('grade'),
            'gradeLabel': best.get('gradeLabel'),
            'actions': list(actions if actions is not None else state.actions),
            'ts': int(time.time() * 1000),
        }
        state.history.append(entry)
        return {'ok': True, 'entry': entry, 'history': state.history}


def history_update(entry_id: int, patch: dict) -> dict:
    with state.lock:
        for entry in state.history:
            if entry['id'] == entry_id:
                for key in ('name',):
                    if key in patch and patch[key] is not None:
                        entry[key] = patch[key]
                if 'spec' in patch and patch['spec'] is not None:
                    entry['spec'] = patch['spec']
                    entry['specLabel'] = _spec_label(patch['spec'])
                if 'actions' in patch and patch['actions'] is not None:
                    entry['actions'] = list(patch['actions'])
                return {'ok': True, 'entry': entry}
    return {'ok': False, 'error': '记录不存在', 'error_type': 'NotFound'}


def history_record_action(action: str, opts: dict, ok: bool) -> Optional[dict]:
    """把一次成功的测试动作累积到当前元素上，供导出脚本使用。"""
    if not ok:
        return None
    with state.lock:
        if not state.desc or state.spec is None:
            return None
        entry = {'action': action, 'opts': dict(opts or {})}
        last = state.actions[-1] if state.actions else None
        if not (last and last.get('action') == action and last.get('opts') == entry['opts']):
            state.actions.append(entry)
        return entry


def actions_clear() -> dict:
    with state.lock:
        state.actions = []
        return {'ok': True, 'actions': []}


def action_snippet(action: str, opts: dict) -> str:
    """单项动作对应的 Python 代码（去掉缩进，直接展示）。"""
    line = codegen.action_line(action, 'ele', opts, indent='')
    return line.strip()


def history_remove(entry_id: int) -> dict:
    with state.lock:
        state.history = [e for e in state.history if e['id'] != entry_id]
        return {'ok': True, 'history': state.history}


def history_clear() -> dict:
    with state.lock:
        state.history = []
        return {'ok': True, 'history': []}


def history_list() -> dict:
    with state.lock:
        return {'ok': True, 'history': list(state.history)}


def export_script(url: str = '', headless: bool = False) -> dict:
    with state.lock:
        steps = [dict(e) for e in state.history]
    return {'ok': True, 'script': codegen.full_script(
        steps, url=url, headless=headless,
        window=[settings.window_width, settings.window_height])}
