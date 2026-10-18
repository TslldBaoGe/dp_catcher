"""把定位方案与动作翻译成可直接运行的 DrissionPage Python 代码。"""
from __future__ import annotations

from typing import Optional

ACTION_SNIPPETS = {
    'click': "ele.click()",
    'click_js': "ele.click(by_js=True)",
    'right_click': "ele.click.right()",
    'double_click': "ele.click.multi(times=2)",
    'hover': "ele.hover()",
    'focus': "ele.focus()",
    'blur': "ele.run_js('this.blur();')",
    'input': "ele.input('{text}')",
    'clear': "ele.clear()",
    'check': "ele.check()",
    'uncheck': "ele.check(uncheck=True)",
    'select': "ele.select.by_{by}('{text}')",
    'scroll_to': "ele.scroll.to_see()",
    'get_text': "print(ele.text)",
    'get_raw_text': "print(ele.raw_text)",
    'get_html': "print(ele.html)",
    'get_inner_html': "print(ele.inner_html)",
    'get_attr': "print(ele.attr('{name}'))",
    'get_attrs': "print(ele.attrs)",
    'get_property': "print(ele.property('{name}'))",
    'get_value': "print(ele.value)",
    'get_states': "print(ele.states.is_displayed, ele.states.is_enabled, ele.states.is_checked)",
    'get_rect': "print(ele.rect.viewport_location, ele.rect.size)",
    'get_css': "print(ele.style('{name}'))",
    'get_screenshot': "ele.get_screenshot(path='snapshots', name='ele.png')",
}

HEADER = '''# -*- coding: utf-8 -*-
"""由「DrissionPage 元素捕获器」生成的示例代码。

依赖：pip install DrissionPage
说明：wait_ele 对「元素还没渲染出来」的情况做了重试，网络慢时更稳。
"""
import time

from DrissionPage import ChromiumPage, ChromiumOptions


def wait_ele(owner, locator, timeout=10, retry=3):
    """等待元素出现；找不到时不抛异常，交由调用方判断。"""
    for _ in range(retry):
        ele = owner.ele(locator, timeout=timeout)
        if ele:
            return ele
        time.sleep(0.5)
    return None


def main():
'''


def q(text) -> str:
    """把任意文本安全地放进单引号字符串里。"""
    s = '' if text is None else str(text)
    s = s.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
    return s


def render_scope(frame_path: Optional[list], base: str = 'page') -> str:
    expr = base
    for loc in frame_path or []:
        expr += ".get_frame('%s')" % q(loc)
    return expr


def render_locator(spec, shadow_chain: Optional[list] = None) -> str:
    """生成定位表达式链：支持字符串、相对定位链、Shadow DOM。

    链式表达式中的 `shadow:` 前缀表示先进入上一级元素的 Shadow Root，例如
    ['#shadowHost', 'shadow:#shadowBtn'] → ('#shadowHost').shadow_root.ele('#shadowBtn')
    """
    parts = [str(s) for s in spec] if isinstance(spec, (list, tuple)) else [str(spec)]

    if shadow_chain and len(parts) == 1:
        expr = ''
        hosts = []
        for item in shadow_chain:
            sel = item.get('selector') if isinstance(item, dict) else None
            if not sel and isinstance(item, dict) and item.get('id'):
                sel = '#' + item['id']
            if sel:
                hosts.append(sel)
        for host in hosts:
            expr += ('.ele' if expr else '') + "('%s').shadow_root" % q(host)
        expr += ('.ele' if expr else '') + "('%s')" % q(parts[-1])
        return expr

    expr = ''
    for i, part in enumerate(parts):
        if i == 0:
            expr = "('%s')" % q(part)
        elif part.startswith('shadow:'):
            expr += ".shadow_root.ele('%s')" % q(part[len('shadow:'):])
        else:
            expr += ".ele('%s')" % q(part)
    return expr


def locate_line(spec, var: str = 'ele', base: str = 'page',
                frame_path: Optional[list] = None, shadow_chain: Optional[list] = None,
                comment: str = '', indent: str = '    ') -> str:
    scope = render_scope(frame_path, base)
    expr = render_locator(spec, shadow_chain)
    line = '%s%s = %s.ele%s' % (indent, var, scope, expr)
    if comment:
        line += '  # %s' % comment
    return line


def action_line(action: str, var: str = 'ele', opts: Optional[dict] = None,
                indent: str = '    ') -> str:
    opts = opts or {}
    tpl = ACTION_SNIPPETS.get(action)
    if not tpl:
        return ''
    snippet = tpl.format(text=q(opts.get('text', opts.get('value', ''))),
                         name=q(opts.get('name', 'value')),
                         by=opts.get('by', 'value'))
    if action == 'input' and opts.get('clear_first'):
        snippet = 'ele.clear()\n%s%s' % (indent, snippet)
    if opts.get('indent_child'):
        snippet = snippet.replace('\n', '\n' + indent)
    return indent + snippet


def snippet_for(spec, action: str = 'get_text', opts: Optional[dict] = None,
                frame_path: Optional[list] = None, shadow_chain: Optional[list] = None,
                text: str = '', doc: str = '', indent: str = '') -> str:
    """生成「定位 + 动作」两行片段。"""
    pad = indent or '    '
    label = text or doc or ''
    lines = [locate_line(spec, 'ele', 'page', frame_path, shadow_chain,
                         comment=label, indent=pad)]
    act = action_line(action, 'ele', opts, indent=pad)
    if act:
        lines.append(act)
    return '\n'.join(lines)


def full_script(steps: list, url: str = '', headless: bool = False,
                window: Optional[list] = None) -> str:
    """由捕获历史生成一份完整可运行脚本。"""
    window = window or [1600, 1000]
    body = []
    body.append('    co = ChromiumOptions()')
    if headless:
        body.append('    co.headless(True)')
    body.append("    co.set_argument('--window-size=%d,%d')" % (window[0], window[1]))
    body.append('    page = ChromiumPage(co)')
    if url:
        body.append("    page.get('%s')" % q(url))
    body.append('')

    if not steps:
        body.append('    # 还没有捕获任何元素：切到「元素捕获」面板点击页面元素即可')
        body.append('    print(page.title)')
    for i, step in enumerate(steps, 1):
        name = step.get('name') or ('元素%d' % i)
        body.append('    # ---------- %d. %s ----------' % (i, name))
        spec = step.get('spec')
        if not spec:
            body.append('    # 该步骤还没有可用的定位表达式，已跳过')
            body.append('')
            continue
        body.append(locate_line(spec, 'ele', 'page', step.get('framePath'),
                                step.get('shadow'), comment=step.get('why') or '', indent='    '))
        for act in step.get('actions') or []:
            line = action_line(act.get('action'), 'ele', act.get('opts'), indent='    ')
            if line:
                body.append(line)
        if not (step.get('actions')):
            body.append('    print(ele.text)')
        body.append('')

    body.append('')
    body.append('if __name__ == "__main__":')
    body.append('    main()')
    return HEADER + '\n'.join(body) + '\n'


def report_snippet(desc: dict, spec) -> str:
    """生成一次性的信息读取片段（文本 / 属性 / 状态）。"""
    attrs = desc.get('attrs') or {}
    lines = [locate_line(spec, 'ele', 'page', desc.get('framePath'),
                         desc.get('shadow'), comment='定位元素')]
    lines.append("    print('text   :', ele.text)")
    lines.append("    print('html   :', ele.html[:200])")
    if attrs:
        for name in list(attrs)[:4]:
            lines.append("    print('attr   : %s =', ele.attr('%s'))" % (name, name))
    lines.append("    print('states :', ele.states.is_displayed, ele.states.is_enabled)")
    lines.append("    print('rect   :', ele.rect.viewport_location, ele.rect.size)")
    return '\n'.join(lines)


def inspect_template(desc: dict, spec) -> str:
    """给「导出脚本」用的整体模板。"""
    return HEADER + snippet_for(spec, 'get_text', frame_path=desc.get('framePath'),
                                shadow_chain=desc.get('shadow')) + '\n' + \
        'if __name__ == "__main__":\n    main()\n'
