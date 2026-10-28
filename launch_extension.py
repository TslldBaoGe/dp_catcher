# -*- coding: utf-8 -*-
"""启动一个已装好扩展的 Chrome 窗口，供日常使用。

为什么不用 --load-extension：
  Chrome 137+ 会忽略该命令行开关（实测无扩展 target），
  必须以 CDP 的 Extensions.loadUnpacked 加载。

为什么内置一个小 http 服务：
  <all_urls> 不包含 file://，扩展无法注入本地文件页面；
  用 http 提供演示页可以免去手动勾选「允许访问文件网址」。

用法：
    python launch_extension.py            # 默认端口 9334
    python launch_extension.py --port 9400 --no-demo
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
EXT = PROJECT / 'extension'
DEMO_DIR = PROJECT / 'demo'
PROFILE = PROJECT / '.chrome-profile'
START_URL = 'https://www.baidu.com'


def find_chrome() -> str | None:
    candidates = [
        os.environ.get('DPC_BROWSER_PATH') or '',
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def start_demo_server() -> tuple:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

    httpd = socketserver.TCPServer(
        ('127.0.0.1', 0),
        functools.partial(Handler, directory=str(DEMO_DIR)),
    )
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/demo_page.html' % port


def wait_cdp(port: int, timeout: float = 40.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port,
                                        timeout=3) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception:
            time.sleep(0.4)
    return None


def load_extension(version: dict, ext_path: Path) -> str | None:
    try:
        import websocket
    except ImportError:
        print('[!] 缺少 websocket-client，无法自动加载扩展')
        print('    请执行: pip install websocket-client')
        return None
    try:
        ws = websocket.create_connection(version['webSocketDebuggerUrl'], timeout=30)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Extensions.loadUnpacked',
                                'params': {'path': str(ext_path)}}))
            deadline = time.time() + 25
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get('id') == 1:
                    if msg.get('error'):
                        print('[!] 加载扩展失败:', str(msg['error'])[:200])
                        return None
                    return (msg.get('result') or {}).get('id')
        finally:
            ws.close()
    except Exception as exc:
        print('[!] 加载扩展异常:', type(exc).__name__, str(exc)[:160])
        return None


def open_tab(port: int, url: str) -> bool:
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:%d/json/new?%s' % (port, urllib.parse.quote(url, safe='')),
            method='PUT')
        with urllib.request.urlopen(req, timeout=15) as r:
            json.loads(r.read().decode('utf-8'))
        return True
    except Exception as exc:
        print('[!] 打开标签页失败:', type(exc).__name__, str(exc)[:120])
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description='启动带扩展的 Chrome')
    parser.add_argument('--port', type=int, default=9334, help='远程调试端口')
    parser.add_argument('--no-demo', action='store_true', help='不打开内置演示页')
    parser.add_argument('--url', default='', help='启动后额外打开的网址')
    args = parser.parse_args()

    chrome = find_chrome()
    if not chrome:
        print('[!] 未找到 Chrome / Edge')
        return 2
    if not (EXT / 'manifest.json').exists():
        print('[!] 扩展目录不完整:', EXT)
        return 2

    PROFILE.mkdir(parents=True, exist_ok=True)

    httpd = None
    demo_url = None
    if not args.no_demo:
        httpd, demo_url = start_demo_server()

    print('Chrome :', chrome)
    print('扩展   :', EXT)
    print('Profile:', PROFILE)
    print('调试端口: %d' % args.port)
    print()

    proc = subprocess.Popen([
        chrome,
        '--remote-debugging-port=%d' % args.port,
        '--user-data-dir=%s' % PROFILE,
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=Translate',
        '--window-size=1500,980',
        'about:blank',
    ])

    version = wait_cdp(args.port)
    if not version:
        print('[!] Chrome 调试端口未就绪，扩展可能未加载')
        print('    Chrome 窗口已打开，可手动在 chrome://extensions 加载已解压的扩展')
        return 1
    print('浏览器 :', version.get('Browser'))

    ext_id = load_extension(version, EXT)
    if ext_id:
        print('扩展ID :', ext_id)
        print('状态   : 已加载 ✓')
    else:
        print('状态   : 加载失败（可在 chrome://extensions 手动加载）')

    time.sleep(1.0)
    target = demo_url or args.url or START_URL
    if target:
        open_tab(args.port, target)
        print('已打开 :', target)
    if args.url and args.url != target:
        open_tab(args.port, args.url)

    print()
    print('=' * 62)
    print(' 使用方法：')
    print('   1. 在任意网页点工具栏上的「DrissionPage 元素捕获器」图标')
    print('      （若图标没显示，点地址栏右侧的拼图图标把它固定）')
    print('   2. 面板里点「开始拾取」，或按 Alt+P')
    print('   3. 鼠标移到元素上会高亮，点一下即捕获')
    print()
    print(' 提示：本窗口使用独立配置目录，不影响你日常浏览器的数据。')
    print('       关闭这个窗口即可结束；重新运行本脚本会再次装好扩展。')
    print('=' * 62)

    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if httpd is not None:
            httpd.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
