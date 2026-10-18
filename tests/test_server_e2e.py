# -*- coding: utf-8 -*-
"""服务端集成测试：真正起一个 uvicorn 进程，验证 HTTP + WebSocket 全链路。

运行：python -m unittest tests.test_server_e2e -v
跳过：设置环境变量 DPC_SKIP_BROWSER_TESTS=1
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import DEMO_PAGE  # noqa: E402

SKIP = os.environ.get('DPC_SKIP_BROWSER_TESTS') == '1'
PORT = int(os.environ.get('DPC_TEST_PORT', 8791))
DEBUG_PORT = int(os.environ.get('DPC_TEST_SERVER_DEBUG_PORT', 9365))
BASE = 'http://127.0.0.1:%d' % PORT
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_URL = 'file:///' + str(DEMO_PAGE).replace(os.sep, '/')


def jpeg_size(data: bytes):
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack('>HH', data[i + 5:i + 9])
            return w, h
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        seg = struct.unpack('>H', data[i + 2:i + 4])[0]
        i += 2 + seg
    return None


def http(path, payload=None, method=None, timeout=90):
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
        method = method or 'POST'
    req = urllib.request.Request(url, data=data, headers=headers, method=method or 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode('utf-8')
        ctype = resp.headers.get('Content-Type', '')
        if 'json' in ctype:
            return json.loads(body)
        return body


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def kill_browser_of_port(port: int) -> None:
    """兜底清理：服务被强杀时，它启动的 Chromium 会变成孤儿进程。

    只匹配本工具的临时 profile 目录，不会影响用户自己的浏览器。
    """
    marker = 'DrissionPage' + os.sep + 'userData' + os.sep + str(int(port))
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*" + marker + "*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', script],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def key_of(value) -> str:
    """候选表达式可能是字符串或相对定位链，统一成可比较的键。"""
    if isinstance(value, (list, tuple)):
        return ' >> '.join(str(v) for v in value)
    return str(value)


@unittest.skipIf(SKIP, '已按环境变量跳过浏览器测试')
class ServerE2ETest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not port_free(PORT):
            raise RuntimeError('端口 %d 已被占用，请设置 DPC_TEST_PORT' % PORT)
        env = dict(os.environ)
        env.update({
            'DPC_PORT': str(PORT),
            'DPC_DEBUG_PORT': str(DEBUG_PORT),
            'DPC_START_URL': DEMO_URL,
            'DPC_AUTO_START': '1',
            'DPC_STREAM_PRESET': 'standard',
            'PYTHONPATH': PROJECT_DIR,
            'PYTHONUTF8': '1',
        })
        cls.proc = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1',
             '--port', str(PORT), '--log-level', 'warning'],
            cwd=PROJECT_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        cls.ready = cls._wait_ready(90)

    @classmethod
    def _wait_ready(cls, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                out = cls.proc.stdout.read().decode('utf-8', 'replace')
                raise RuntimeError('服务进程退出：\n' + out[-3000:])
            try:
                st = http('/api/status', timeout=10)
                if st.get('running'):
                    return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    @classmethod
    def tearDownClass(cls):
        # 先请服务自己把浏览器关掉，再退出进程，避免留下孤儿 Chromium
        try:
            http('/api/browser/stop', {}, timeout=30)
            time.sleep(0.8)
        except Exception:
            pass
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=20)
        except Exception:
            try:
                cls.proc.kill()
            except Exception:
                pass
        kill_browser_of_port(DEBUG_PORT)

    def test_00_server_is_up_with_browser_running(self):
        self.assertTrue(self.ready, '服务或浏览器未能在时限内就绪')
        st = http('/api/status')
        self.assertTrue(st['running'])
        self.assertEqual(st['debug_port'], DEBUG_PORT)

    def test_01_settings_endpoint(self):
        cfg = http('/api/settings')
        self.assertIn('presets', cfg)
        self.assertIn('standard', cfg['presets'])
        self.assertIn('keys', cfg)
        self.assertIn('Enter', cfg['keys'])

    def test_02_dashboard_page_served(self):
        html = http('/')
        self.assertIn('元素捕获器', html)
        self.assertIn('/static/app.js', html)

    def test_03_static_assets_served(self):
        for path in ('/static/app.js', '/static/style.css', '/static/picker.js'):
            with self.subTest(path=path):
                body = http(path)
                self.assertGreater(len(body), 500)

    def test_04_demo_page_route(self):
        html = http('/demo')
        self.assertIn('shadowHost', html)

    def test_10_navigate_and_highlight(self):
        res = http('/api/navigate', {'url': DEMO_URL})
        self.assertTrue(res['ok'], res.get('error'))
        hl = http('/api/highlight', {'spec': '#username'})
        self.assertTrue(hl['ok'], hl.get('error'))
        group = hl['data']['groups'][0]
        self.assertEqual(group['count'], 1)
        self.assertEqual(len(group['rects']), 1)

    def test_11_pick_workflow_over_http(self):
        http('/api/navigate', {'url': DEMO_URL})
        rect = http('/api/highlight', {'spec': '#username'})['data']['groups'][0]['rects'][0]
        picked = http('/api/pick', {'x': rect[0] + rect[2] / 2, 'y': rect[1] + rect[3] / 2})
        self.assertTrue(picked['ok'], picked.get('error'))
        self.assertEqual(picked['desc']['id'], 'username')
        self.assertEqual(picked['spec'], '#username')
        self.assertEqual(picked['plan']['recommended']['verdict'], 'unique')
        self.assertGreater(len(picked['plan']['candidates']), 2)
        self.assertIn('DrissionPage', picked['code']['script'])
        compile(picked['code']['script'], 'x.py', 'exec')

    def test_12_wrong_target_is_reported(self):
        http('/api/navigate', {'url': DEMO_URL})
        r = http('/api/highlight', {'spec': '#password'})['data']['groups'][0]['rects'][0]
        http('/api/pick', {'x': r[0] + r[2] / 2, 'y': r[1] + r[3] / 2})
        applied = http('/api/apply', {'spec': '#username'})
        self.assertTrue(applied['ok'])
        by = {key_of(c['value']): c for c in applied['plan']['candidates']}
        self.assertIn('#username', by, '手动指定的表达式也应出现在候选清单里')
        self.assertEqual(by['#username']['count'], 1)
        self.assertEqual(by['#username']['verdict'], 'wrong',
                         '首个匹配不是拾取目标时必须判定为错位')

    def test_13_actions_over_http(self):
        http('/api/navigate', {'url': DEMO_URL})
        ins = http('/api/action', {'action': 'input', 'spec': '#username',
                                   'opts': {'text': 'http_test', 'clear_first': True}})
        self.assertTrue(ins['ok'], ins.get('error'))
        self.assertIn("ele.input('http_test')", ins['code'])
        self.assertIn('ele.clear()', ins['code'])
        got = http('/api/action', {'action': 'get_property', 'spec': '#username',
                                   'opts': {'name': 'value'}})
        self.assertEqual(got['data']['value'], 'http_test')
        click = http('/api/action', {'action': 'click', 'spec': '#submitBtn'})
        self.assertTrue(click['ok'], click.get('error'))
        status = http('/api/action', {'action': 'get_text', 'spec': '#status'})
        self.assertIn('http_test', status['data']['text'])

    def test_14_history_and_export(self):
        http('/api/navigate', {'url': DEMO_URL})
        r = http('/api/highlight', {'spec': '#username'})['data']['groups'][0]['rects'][0]
        http('/api/pick', {'x': r[0] + r[2] / 2, 'y': r[1] + r[3] / 2})
        http('/api/action', {'action': 'input', 'spec': '#username',
                             'opts': {'text': 'hist', 'clear_first': True}})
        added = http('/api/history/add', {'name': '用户名输入框'})
        self.assertTrue(added['ok'], added.get('error'))
        self.assertEqual(added['entry']['name'], '用户名输入框')
        self.assertTrue(any(a['action'] == 'input' for a in added['entry']['actions']),
                        '成功的测试动作应被记录进导出脚本')
        hist = http('/api/history', method='GET')
        self.assertTrue(any(e['id'] == added['entry']['id'] for e in hist['history']))
        exported = http('/api/export', {'url': DEMO_URL})
        self.assertIn('用户名输入框', exported['script'])
        compile(exported['script'], 'exported.py', 'exec')
        http('/api/history/clear', {})

    def test_15_pick_mode_and_preset_toggles(self):
        on = http('/api/pick-mode', {'on': True})
        self.assertTrue(on['ok'])
        off = http('/api/pick-mode', {'on': False})
        self.assertTrue(off['ok'])
        pre = http('/api/browser/preset', {'name': 'hd'})
        self.assertEqual(pre['preset'], 'hd')
        back = http('/api/browser/preset', {'name': 'standard'})
        self.assertEqual(back['preset'], 'standard')

    def test_16_error_envelope_for_bad_action(self):
        res = http('/api/action', {'action': 'nope', 'spec': '#username'})
        self.assertFalse(res['ok'])
        self.assertTrue(res['error'])
        res2 = http('/api/action', {'action': 'click', 'spec': '#nope-not-here'})
        self.assertFalse(res2['ok'])

    def test_20_websocket_hello_frame_and_pick(self):
        try:
            import websocket  # websocket-client
        except ImportError:
            self.skipTest('未安装 websocket-client')
        ws = websocket.create_connection('ws://127.0.0.1:%d/ws' % PORT, timeout=40)
        try:
            hello = json.loads(ws.recv())
            self.assertEqual(hello['type'], 'hello')

            # 触发页面变化 → 应收到镜像帧
            http('/api/navigate', {'url': DEMO_URL})
            frame = None
            deadline = time.time() + 40
            while time.time() < deadline:
                ws.settimeout(15)
                msg = json.loads(ws.recv())
                if msg.get('type') == 'frame':
                    frame = msg
                    break
            self.assertIsNotNone(frame, 'WebSocket 未推送镜像帧')
            raw = base64.b64decode(frame['data'])
            size = jpeg_size(raw)
            self.assertIsNotNone(size, '帧不是合法 JPEG')
            self.assertGreater(size[0], 100)
            self.assertGreater(frame['cw'], 100, '帧必须带 CSS 视口宽度用于坐标换算')
            self.assertIn('demo_page', frame['url'] or '')

            # 通过 WebSocket 拾取
            rect = http('/api/highlight', {'spec': '#username'})['data']['groups'][0]['rects'][0]
            ws.settimeout(30)
            ws.send(json.dumps({'cmd': 'pick', 'x': rect[0] + rect[2] / 2,
                                'y': rect[1] + rect[3] / 2, 'token': 7}))
            picked = None
            deadline = time.time() + 40
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get('type') == 'picked':
                    picked = msg
                    break
            self.assertIsNotNone(picked, '未收到拾取结果')
            self.assertEqual(picked['token'], 7)
            self.assertTrue(picked['payload']['ok'], picked['payload'].get('error'))
            self.assertEqual(picked['payload']['desc']['id'], 'username')

            # ping/pong 心跳
            ws.send(json.dumps({'cmd': 'ping', 'ts': 123}))
            got_pong = False
            deadline = time.time() + 20
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get('type') == 'pong':
                    got_pong = msg.get('ts') == 123
                    break
            self.assertTrue(got_pong, '心跳无响应')
        finally:
            ws.close()

    def test_30_browser_stop_and_restart(self):
        stop = http('/api/browser/stop', {})
        self.assertTrue(stop.get('stopped'))
        time.sleep(1.0)
        self.assertFalse(http('/api/status')['running'])
        start = http('/api/browser/start', {'url': DEMO_URL})
        self.assertTrue(start['ok'], start.get('error'))
        self.assertTrue(http('/api/status')['running'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
