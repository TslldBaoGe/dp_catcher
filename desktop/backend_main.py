# -*- coding: utf-8 -*-
"""桌面版后端入口：由 Electron 作为子进程启动，只监听 127.0.0.1。"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _setup_logging(log_file: str | None, verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding='utf-8')
        fh.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-7s %(name)s | %(message)s'))
        handlers.append(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter('%(levelname)s %(name)s | %(message)s'))
        handlers.append(sh)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        handlers=handlers or [logging.NullHandler()], force=True)
    for name in ('DrissionPage', 'urllib3', 'websocket', 'asyncio'):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='DrissionPage 元素捕获器后端')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--data-dir', default='')
    parser.add_argument('--log-file', default='')
    parser.add_argument('--parent-pid', type=int, default=0)
    parser.add_argument('--token', default='')
    parser.add_argument('--debug-port', type=int, default=0)
    parser.add_argument('--no-auto-start', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ['DPC_DATA_DIR'] = args.data_dir
    if args.token:
        os.environ['DPC_DESKTOP_TOKEN'] = args.token
    if args.debug_port:
        os.environ['DPC_DEBUG_PORT'] = str(args.debug_port)
    os.environ['DPC_DESKTOP'] = '1'
    os.environ['DPC_AUTO_START'] = '0' if args.no_auto_start else '1'
    os.environ.setdefault('PYTHONUTF8', '1')

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    _setup_logging(args.log_file, args.verbose)
    log = logging.getLogger('dpc.desktop')

    import uvicorn
    from app.api import app as fastapi_app, set_shutdown_hook
    from app.config import DATA_DIR, settings
    from app.core.browser import session

    settings.auto_start = not args.no_auto_start
    log.info('后端启动 port=%s data=%s parent=%s', args.port, DATA_DIR, args.parent_pid)

    config = uvicorn.Config(fastapi_app, host='127.0.0.1', port=args.port,
                            log_config=None, access_log=False, log_level='info')
    server = uvicorn.Server(config)
    set_shutdown_hook(lambda: setattr(server, 'should_exit', True))

    def watch_parent() -> None:
        while True:
            time.sleep(1.5)
            if not _pid_alive(args.parent_pid):
                log.warning('Electron 主进程已退出，后端自行关闭')
                try:
                    session.stop(True)
                except Exception:
                    pass
                server.should_exit = True
                time.sleep(4.0)
                os._exit(0)

    if args.parent_pid > 0:
        threading.Thread(target=watch_parent, name='parent-watchdog', daemon=True).start()

    try:
        server.run()
    finally:
        try:
            session.stop_pump()
            session.stop(True)
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
