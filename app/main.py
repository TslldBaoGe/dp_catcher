"""启动入口：python run.py 或 python -m app.main"""
from __future__ import annotations

import argparse
import sys
import webbrowser

import uvicorn

from .config import settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='DrissionPage 元素捕获器')
    parser.add_argument('--host', default=settings.host, help='Web 服务监听地址')
    parser.add_argument('--port', type=int, default=settings.port, help='Web 服务端口')
    parser.add_argument('--debug-port', type=int, default=settings.debug_port,
                        help='Chrome 调试端口')
    parser.add_argument('--headless', action='store_true', help='无头模式（不显示浏览器窗口）')
    parser.add_argument('--no-browser', action='store_true', help='不自动打开控制台页面')
    parser.add_argument('--no-auto-start', action='store_true', help='不自动启动浏览器')
    args = parser.parse_args(argv)

    settings.host = args.host
    settings.port = args.port
    settings.debug_port = args.debug_port
    if args.headless:
        settings.headless = True
    if args.no_auto_start:
        settings.auto_start = False

    url = f'http://{args.host}:{args.port}/'
    print('=' * 68)
    print('  DrissionPage 元素捕获器')
    print('  控制台地址 : %s' % url)
    print('  浏览器调试端口: %s' % settings.debug_port)
    print('  目标测试页 : %s' % settings.start_url)
    print('=' * 68)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run('app.api:app', host=args.host, port=args.port, log_level='info')
    return 0


if __name__ == '__main__':
    sys.exit(main())
