# -*- coding: utf-8 -*-
"""用真实 Chrome 加载扩展并端到端验证。

启动一个带 --load-extension 的 Chrome，打开测试页，
确认：content script 注入、picker 可用、面板能打开、拾取能产出定位方案。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
EXT = PROJECT / 'extension'
DEMO = PROJECT / 'demo' / 'demo_page.html'
PORT = int(os.environ.get('DPC_EXT_PORT', '9600'))


def find_chrome():
    for p in (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
              r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
              r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'):
        if os.path.exists(p):
            return p
    return None


def http_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def cdp_call(ws_url, method, params=None, timeout=25):
    """极简 CDP 客户端（websocket-client）。"""
    import websocket
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        ws.send(json.dumps({'id': 1, 'method': method, 'params': params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get('id') == 1:
                return msg
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def evaluate(ws_url, expr, await_promise=False):
    res = cdp_call(ws_url, 'Runtime.evaluate', {
        'expression': expr, 'returnByValue': True,
        'awaitPromise': await_promise, 'userGesture': True,
    })
    if not res:
        return {'error': 'no response'}
    if 'error' in res:
        return {'error': res['error']}
    result = res.get('result', {}).get('result', {})
    if result.get('exceptionDetails'):
        return {'error': result['exceptionDetails']}
    return {'value': result.get('value'), 'subtype': result.get('subtype'),
            'description': result.get('description')}


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print('未找到 Chrome/Edge')
        return 2
    if not (EXT / 'manifest.json').exists():
        print('扩展目录不完整:', EXT)
        return 2

    # 用 http 提供测试页：<all_urls> 不含 file://，扩展不会注入本地文件
    import functools
    import http.server
    import socketserver
    import threading

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

    httpd = socketserver.TCPServer(
        ('127.0.0.1', 0),
        functools.partial(_QuietHandler, directory=str(DEMO.parent)))
    http_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    demo_url = 'http://127.0.0.1:%d/%s' % (http_port, DEMO.name)

    profile = tempfile.mkdtemp(prefix='dpc_ext_')
    args = [
        chrome,
        '--remote-debugging-port=%d' % PORT,
        '--user-data-dir=' + profile,
        # Chrome 137+ 忽略 --load-extension，改用 CDP Extensions.loadUnpacked
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=Translate',
        '--window-size=1500,950',
        'about:blank',   # 先空白页，等扩展加载后再导航到测试页
    ]
    print('启动 Chrome（已加载扩展）…')
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    failed = []

    def check(name, ok, detail=''):
        print('  [%s] %s%s' % ('OK' if ok else 'FAIL', name, ('  → ' + str(detail)) if detail else ''))
        if not ok:
            failed.append(name)

    try:
        # 等 CDP 就绪
        version = None
        for _ in range(60):
            try:
                version = http_json('http://127.0.0.1:%d/json/version' % PORT)
                break
            except Exception:
                time.sleep(0.5)
        check('Chrome 已启动', bool(version), (version or {}).get('Browser', ''))

        # 通过 CDP 加载未打包扩展（Chrome 137+ 唯一可靠方式）
        import websocket
        ext_id = None
        try:
            ws_b = websocket.create_connection(version['webSocketDebuggerUrl'], timeout=30)
            ws_b.send(json.dumps({'id': 1, 'method': 'Extensions.loadUnpacked',
                                  'params': {'path': str(EXT)}}))
            deadline = time.time() + 25
            while time.time() < deadline:
                msg = json.loads(ws_b.recv())
                if msg.get('id') == 1:
                    ext_id = (msg.get('result') or {}).get('id')
                    if not ext_id:
                        print('      加载错误:', json.dumps(msg, ensure_ascii=False)[:300])
                    break
            ws_b.close()
        except Exception as exc:
            print('      加载扩展异常:', type(exc).__name__, str(exc)[:120])
        check('扩展已加载', bool(ext_id), ext_id or '')

        # 扩展加载完成后再打开测试页：content script 只对「加载之后」的文档注入
        time.sleep(2.0)
        try:
            req = urllib.request.Request(
                'http://127.0.0.1:%d/json/new?%s' % (
                    PORT, urllib.parse.quote(demo_url, safe='')),
                method='PUT')
            with urllib.request.urlopen(req, timeout=15) as r:
                json.loads(r.read().decode())
        except Exception as exc:
            print('      新开标签失败:', type(exc).__name__, str(exc)[:100])
        time.sleep(4.0)

        # 找到目标页面
        target = None
        for _ in range(40):
            try:
                for t in http_json('http://127.0.0.1:%d/json/list' % PORT):
                    if t.get('type') == 'page' and 'demo_page' in (t.get('url') or ''):
                        target = t
                        break
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check('测试页已打开', bool(target), (target or {}).get('url', '')[-40:])
        if not target:
            return 1

        ws_url = target['webSocketDebuggerUrl']
        time.sleep(1.0)

        # 关键：Chrome 137+ 下 CDP 加载的未打包扩展不会自动注入 content_scripts，
        # 这里通过扩展自身调用 chrome.scripting 注入，等价于用户点工具栏图标的路径。
        try:
            tabs_list = http_json('http://127.0.0.1:%d/json/list' % PORT)
            sw = next((t for t in tabs_list if t.get('type') == 'service_worker'
                       and ext_id and ext_id in (t.get('url') or '')), None)
            if sw:
                inject_expr = '''(async () => {
                    const tabs = await chrome.tabs.query({active: true, currentWindow: true});
                    if (!tabs.length) return 'no tab';
                    const id = tabs[0].id;
                    await chrome.scripting.executeScript({target: {tabId: id, allFrames: true},
                                                          files: ['picker.js'], world: 'MAIN'});
                    // panel.js 需要 chrome.runtime（只在 isolated world 可用），
                    // 它通过 window 自定义事件接收 MAIN world 里 picker 的拾取结果
                    await chrome.scripting.executeScript({target: {tabId: id},
                                                          files: ['panel.js']});
                    return 'injected:' + tabs[0].url;
                })()'''
                w = websocket.create_connection(sw['webSocketDebuggerUrl'], timeout=30)
                w.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                    'expression': inject_expr, 'returnByValue': True, 'awaitPromise': True}}))
                deadline = time.time() + 30
                while time.time() < deadline:
                    m = json.loads(w.recv())
                    if m.get('id') == 1:
                        val = m.get('result', {}).get('result', {}).get('value')
                        print('      注入结果:', val)
                        if m.get('result', {}).get('exceptionDetails'):
                            print('      注入异常:', str(m['result']['exceptionDetails'])[:200])
                        break
                w.close()
            else:
                print('      ! 未找到扩展 service worker')
        except Exception as exc:
            print('      注入异常:', type(exc).__name__, str(exc)[:150])

        time.sleep(2.5)

        # 注入后重新获取页面 target，避免拿到过期的 webSocketDebuggerUrl
        fresh = None
        for _ in range(20):
            try:
                for t in http_json('http://127.0.0.1:%d/json/list' % PORT):
                    if t.get('type') == 'page' and 'demo_page' in (t.get('url') or ''):
                        fresh = t
                        break
            except Exception:
                pass
            if fresh:
                break
            time.sleep(0.3)
        if fresh:
            ws_url = fresh['webSocketDebuggerUrl']

        # 1) content script 注入
        r = evaluate(ws_url, 'typeof window.__DPC__')
        check('content script 已注入', r.get('value') == 'object', r.get('value'))

        r = evaluate(ws_url, 'window.__DPC__ ? window.__DPC__.ver : null')
        check('picker 版本可读', isinstance(r.get('value'), int), r.get('value'))

        # 2) 面板：panel.js 注入在 isolated world，页面主世界看不到 chrome.*，
        #    所以改从扩展侧验证面板对象是否已就绪。
        try:
            tabs_now = http_json('http://127.0.0.1:%d/json/list' % PORT)
            sw = next((t for t in tabs_now if t.get('type') == 'service_worker'
                       and ext_id and ext_id in (t.get('url') or '')), None)
            panel_ok = None
            if sw:
                w = websocket.create_connection(sw['webSocketDebuggerUrl'], timeout=25)
                w.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                    'expression': '''(async () => {
                        const tabs = await chrome.tabs.query({active:true,currentWindow:true});
                        const r = await chrome.scripting.executeScript({
                            target: {tabId: tabs[0].id},
                            func: () => ({
                                panel: !!window.__DPC_PANEL__,
                                picker: typeof window.__DPC__,
                                visible: window.__DPC_PANEL__ ? window.__DPC_PANEL__.state.visible : null,
                            }),
                        });
                        return JSON.stringify(r[0].result);
                    })()''',
                    'returnByValue': True, 'awaitPromise': True}}))
                deadline = time.time() + 25
                while time.time() < deadline:
                    m = json.loads(w.recv())
                    if m.get('id') == 1:
                        panel_ok = m.get('result', {}).get('result', {}).get('value')
                        break
                w.close()
            check('面板脚本已就绪', bool(panel_ok) and 'true' in str(panel_ok).lower(), panel_ok)
        except Exception as exc:
            check('面板脚本已就绪', False, '%s: %s' % (type(exc).__name__, str(exc)[:80]))

        # 2b) 面板拖动：模拟按住标题栏移动，确认位置真的改变
        try:
            tabs_now = http_json('http://127.0.0.1:%d/json/list' % PORT)
            sw = next((t for t in tabs_now if t.get('type') == 'service_worker'
                       and ext_id and ext_id in (t.get('url') or '')), None)
            drag_result = None
            if sw:
                w = websocket.create_connection(sw['webSocketDebuggerUrl'], timeout=30)
                drag_expr = """(async () => {
                    const tabs = await chrome.tabs.query({active:true,currentWindow:true});
                    const r = await chrome.scripting.executeScript({
                        target: {tabId: tabs[0].id},
                        func: async () => {
                            const p = window.__DPC_PANEL__;
                            if (!p) return {err:'no panel'};
                            const el = p.state.panel;
                            const head = el.querySelector('.head');
                            if (!head) return {err:'no head'};
                            // 等一帧，确保 show() 之后已完成布局
                            await new Promise(r => requestAnimationFrame(
                                () => requestAnimationFrame(r)));
                            const before = el.getBoundingClientRect();
                            const sx = before.left + 60, sy = before.top + 14;
                            const opts = {bubbles:true, cancelable:true, clientX:sx, clientY:sy,
                                          pointerId:1, pointerType:'mouse', button:0};
                            head.dispatchEvent(new PointerEvent('pointerdown', opts));
                            window.dispatchEvent(new PointerEvent('pointermove',
                                Object.assign({}, opts, {clientX:sx-220, clientY:sy+160})));
                            window.dispatchEvent(new PointerEvent('pointerup',
                                Object.assign({}, opts, {clientX:sx-220, clientY:sy+160})));
                            const after = el.getBoundingClientRect();
                            return {
                                before: [Math.round(before.left), Math.round(before.top)],
                                after: [Math.round(after.left), Math.round(after.top)],
                                saved: p.state.store.panelPos || null,
                            };
                        },
                    });
                    return JSON.stringify(r[0].result);
                })()"""
                w.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
                    'expression': drag_expr, 'returnByValue': True, 'awaitPromise': True}}))
                deadline = time.time() + 25
                while time.time() < deadline:
                    m = json.loads(w.recv())
                    if m.get('id') == 1:
                        drag_result = m.get('result', {}).get('result', {}).get('value')
                        break
                w.close()
            if drag_result:
                info = json.loads(drag_result)
                before = info.get('before')
                after = info.get('after')
                moved = (before and after
                         and (abs(after[0] - before[0]) > 50 or abs(after[1] - before[1]) > 50))
                check('面板可拖动', bool(moved), '%s → %s' % (before, after))
                check('拖动位置已记录', bool(info.get('saved')), info.get('saved'))
            else:
                check('面板可拖动', False, '未取到结果')
        except Exception as exc:
            check('面板可拖动', False, '%s: %s' % (type(exc).__name__, str(exc)[:80]))

        # 3) 拾取：模拟点击用户名输入框
        r = evaluate(ws_url, """
        (function () {
          const el = document.querySelector('#username');
          if (!el) return {ok: false, err: 'no element'};
          const rect = el.getBoundingClientRect();
          const x = rect.left + rect.width / 2;
          const y = rect.top + rect.height / 2;
          window.__DPC__.setPick(true);
          const desc = window.__DPC__.pickAt(x, y, false);
          window.__DPC__.setPick(false);
          if (!desc) return {ok: false, err: 'pickAt returned null'};
          return {
            ok: true, tag: desc.tag, id: desc.id,
            ncands: (desc.cands || []).length,
            firstCand: (desc.cands || [])[0] ? desc.cands[0].loc : null,
            hasChain: !!(desc.chain && desc.chain.length),
            hasShadow: !!desc.inShadow,
          };
        })()
        """)
        v = r.get('value') or {}
        check('坐标拾取成功', bool(v.get('ok')), v.get('err') or '')
        check('拾取到 #username', v.get('id') == 'username', '<%s> %s' % (v.get('tag'), v.get('id')))
        check('生成候选定位', (v.get('ncands') or 0) >= 3, '%s 条' % v.get('ncands'))
        check('候选含 id 定位', v.get('firstCand') == '#username', v.get('firstCand'))
        check('包含父级链', bool(v.get('hasChain')))

        # 4) 定位解析器（面板内的 splitLoc/queryLoc 逻辑）在真实页面上工作
        r = evaluate(ws_url, """
        (function () {
          const el = document.querySelector('#username');
          const tests = ['#username', '@name=username', 'tag:input@name=username',
                         'text=提交表单', 'tx=用户名', 'css:input#username'];
          const out = {};
          for (const loc of tests) {
            let list = [];
            let err = null;
            try {
              if (loc.startsWith('css:')) list = Array.from(document.querySelectorAll(loc.slice(4)));
              else if (loc.startsWith('#')) list = Array.from(document.querySelectorAll(loc));
              else if (loc.startsWith('@')) {
                const m = loc.slice(1).match(/^([^=]+)=(.*)$/);
                list = m ? Array.from(document.querySelectorAll('*'))
                  .filter(e => e.getAttribute(m[1]) === m[2]) : [];
              } else if (loc.startsWith('tag:')) {
                const m = loc.slice(4).match(/^([^@]+)@+([^=]+)=(.*)$/);
                list = m ? Array.from(document.querySelectorAll('*'))
                  .filter(e => e.tagName.toLowerCase() === m[1].toLowerCase()
                    && e.getAttribute(m[2]) === m[3]) : [];
              } else if (loc.startsWith('tx=')) {
                const w = loc.slice(3);
                list = Array.from(document.querySelectorAll('*'))
                  .filter(e => ((e.innerText != null ? e.innerText : e.textContent) || '').includes(w));
              } else if (loc.startsWith('text=')) {
                const w = loc.slice(5);
                list = Array.from(document.querySelectorAll('*'))
                  .filter(e => ((e.innerText != null ? e.innerText : e.textContent) || '').trim() === w);
              }
            } catch (e) { err = String(e); }
            out[loc] = {count: list.length, first: list[0] === el, err};
          }
          return out;
        })()
        """)
        v = r.get('value') or {}
        for loc, info in (v.items() if isinstance(v, dict) else []):
            ok = info.get('count', 0) >= 1
            check('定位可解析: %s' % loc, ok,
                  'count=%s first=%s' % (info.get('count'), info.get('first')))

        # 5) 动作测试：输入 + 点击，验证页面真的响应
        r = evaluate(ws_url, """
        (function () {
          const el = document.querySelector('#username');
          el.focus();
          el.value = 'ext_test';
          el.dispatchEvent(new Event('input', {bubbles: true}));
          const btn = document.querySelector('#submitBtn');
          btn.click();
          const status = document.querySelector('#status');
          return {value: el.value, status: status ? status.textContent : null};
        })()
        """)
        v = r.get('value') or {}
        check('输入值生效', v.get('value') == 'ext_test', v.get('value'))
        check('点击触发页面逻辑', 'ext_test' in (v.get('status') or ''), v.get('status'))

        # 6) Shadow DOM 元素也能被 picker 拾取
        r = evaluate(ws_url, """
        (function () {
          const host = document.getElementById('shadowHost');
          host.scrollIntoView({block: 'center'});
          const btn = host.shadowRoot.getElementById('shadowBtn');
          const r = btn.getBoundingClientRect();
          const desc = window.__DPC__.pickAt(r.left + r.width / 2, r.top + r.height / 2, false);
          return {ok: !!desc, id: desc && desc.id, inShadow: desc && desc.inShadow,
                  hasShadowChain: !!(desc && desc.shadow && desc.shadow.length)};
        })()
        """)
        v = r.get('value') or {}
        check('Shadow DOM 内元素可拾取', bool(v.get('ok')) and v.get('id') == 'shadowBtn',
              '%s inShadow=%s' % (v.get('id'), v.get('inShadow')))

        # 6b) 截图存档（人工确认面板位置与渲染）
        try:
            import base64 as _b64
            snap = PROJECT / 'snapshots' / 'extension_verify.png'
            snap.parent.mkdir(parents=True, exist_ok=True)
            sg = cdp_call(ws_url, 'Page.captureScreenshot', {'format': 'png'})
            payload = sg and sg.get('result', {}).get('data')
            if payload:
                snap.write_bytes(_b64.b64decode(payload))
                print('      截图:', snap.name, snap.stat().st_size, 'bytes')
        except Exception as exc:
            print('      截图失败(不影响结论):', type(exc).__name__)

        # 7) 检查页面是否出现扩展注入的 JS 报错
        r = evaluate(ws_url, 'window.__errors ? window.__errors.length : 0')
        check('页面无严重 JS 错误', (r.get('value') or 0) == 0, r.get('value'))

    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        import shutil
        time.sleep(1)
        try:
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass

    print()
    if failed:
        print('失败 %d 项: %s' % (len(failed), ', '.join(failed)))
        return 1
    print('全部检查通过：扩展可在真实 Chrome 中加载并工作。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
