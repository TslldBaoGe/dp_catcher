"""FastAPI 应用：REST 接口 + WebSocket 镜像推送。"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, settings
from .core import workflow
from .core.browser import KEYS, Subscriber, session

log = logging.getLogger('dpc.api')


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s | %(message)s')
    if settings.auto_start:
        try:
            res = await asyncio.to_thread(session.start)
            log.info('浏览器启动: %s', res.get('ok') and res.get('data') or res.get('error'))
        except Exception as exc:
            log.warning('浏览器自动启动失败: %s', exc)
    yield
    session.stop_pump()
    try:
        await asyncio.to_thread(session.stop)
    except Exception:
        pass


app = FastAPI(title='DrissionPage 元素捕获器', version='1.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')


async def _call(fn, *args, **kwargs):
    """所有 DrissionPage 调用都是阻塞的 → 丢到线程池，避免卡住事件循环。"""
    return await asyncio.to_thread(fn, *args, **kwargs)


_shutdown_hook = None


def set_shutdown_hook(fn) -> None:
    """桌面壳启动时注入：收到关停请求后让 uvicorn 优雅退出。"""
    global _shutdown_hook
    _shutdown_hook = fn


def _resp(payload: dict):
    return JSONResponse(status_code=200, content=payload)


# --------------------------------------------------------------------- 页面

@app.get('/')
async def index():
    return FileResponse(str(STATIC_DIR / 'index.html'))


@app.get('/api/settings')
async def get_settings():
    data = settings.as_dict()
    data['keys'] = list(KEYS)
    data['stats'] = dict(session.stats)
    return data


@app.get('/demo')
async def demo_page():
    """内置测试页（真实浏览器与镜像都可用它演练捕获）。"""
    return FileResponse(str(settings.demo_page_path))


@app.post('/api/pick-from-desc')
async def pick_from_desc(payload: dict = Body(default={})):
    """真实浏览器窗口里拾取到的元素描述 → 复核定位方案 + 高亮 + 生成代码。"""
    desc = payload.get('desc')
    if not desc:
        return {'ok': False, 'error': '缺少元素描述', 'error_type': 'BadRequest'}
    return _resp(await _call(workflow.adopt_desc, desc))


@app.get('/api/status')
async def get_status():
    return await _call(session.status)


@app.get('/api/frames')
async def get_frames():
    res = await _call(session.frames)
    data = (res.get('data') or {}) if res.get('ok') else {}
    return {'ok': bool(res.get('ok')), 'url': data.get('url'),
            'frames': data.get('frames') or [], 'error': res.get('error')}


# ------------------------------------------------------------------- 浏览器

@app.post('/api/browser/start')
async def browser_start(payload: dict = Body(default={})):
    return _resp(await _call(session.start, payload.get('url')))


@app.post('/api/browser/stop')
async def browser_stop():
    return _resp(await _call(session.stop))


@app.post('/api/desktop/shutdown')
async def desktop_shutdown(payload: dict = Body(default={})):
    """仅桌面壳可调用：校验一次性令牌后关停浏览器与后端进程。"""
    expected = os.environ.get('DPC_DESKTOP_TOKEN') or ''
    if not expected:
        return JSONResponse(status_code=403,
                            content={'ok': False, 'error': '仅桌面模式可用'})
    token = str(payload.get('token') or '')
    if not hmac.compare_digest(token, expected):
        return JSONResponse(status_code=403,
                            content={'ok': False, 'error': '桌面令牌无效'})
    try:
        session.stop_pump()
        await asyncio.to_thread(session.stop, True)
    finally:
        if _shutdown_hook is not None:
            _shutdown_hook()
    return {'ok': True}


@app.post('/api/browser/restart')
async def browser_restart():
    return _resp(await _call(session.restart))


@app.post('/api/browser/resize')
async def browser_resize(payload: dict = Body(default={})):
    return _resp(await _call(session.resize, int(payload.get('w', 1600)), int(payload.get('h', 1000))))


@app.post('/api/browser/preset')
async def browser_preset(payload: dict = Body(default={})):
    name = payload.get('name') or 'standard'
    preset = settings.set_preset(name)
    session.mark_dirty(3)
    return {'ok': True, 'preset': settings.stream_preset, 'config': preset}


@app.post('/api/browser/stream')
async def browser_stream(payload: dict = Body(default={})):
    settings.stream_enabled = bool(payload.get('enabled', True))
    session.mark_dirty(2)
    return {'ok': True, 'enabled': settings.stream_enabled}


# ---------------------------------------------------------- 端口等可配置项

def _port_ok(value) -> tuple:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False, 0, '端口必须是数字'
    if not (1024 <= port <= 65535):
        return False, 0, '端口需在 1024–65535 之间'
    return True, port, ''


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(('127.0.0.1', int(port))) == 0


@app.get('/api/ports')
async def get_ports():
    """当前端口配置，供界面显示。"""
    from .config import SETTINGS_FILE
    return {
        'ok': True,
        'debug_port': settings.debug_port,
        'debug_port_default': 9333,
        'web_port': settings.port,
        'web_port_default': 8777,
        'settings_file': str(SETTINGS_FILE),
        'desktop': bool(os.environ.get('DPC_DESKTOP')),
    }


@app.post('/api/ports/debug')
async def set_debug_port(payload: dict = Body(default={})):
    """修改浏览器调试端口；保存后需重启浏览器生效。"""
    from .config import save_user_settings
    ok, port, err = _port_ok(payload.get('port'))
    if not ok:
        return {'ok': False, 'error': err, 'error_type': 'BadPort'}
    old = settings.debug_port
    if port == old:
        return {'ok': True, 'debug_port': port, 'changed': False}
    busy = _port_in_use(port)
    settings.debug_port = port
    save_user_settings({'debug_port': port})
    return {'ok': True, 'debug_port': port, 'old': old, 'changed': True,
            'port_busy': busy, 'note': '已保存，点「重启」后生效'}


@app.post('/api/ports/web')
async def set_web_port(payload: dict = Body(default={})):
    """修改控制台 Web 端口；下次启动生效。"""
    from .config import save_user_settings
    ok, port, err = _port_ok(payload.get('port'))
    if not ok:
        return {'ok': False, 'error': err, 'error_type': 'BadPort'}
    old = settings.port
    busy = _port_in_use(port)
    save_user_settings({'port': port})
    return {'ok': True, 'port': port, 'old': old, 'changed': port != old,
            'port_busy': busy, 'note': '已保存，下次启动生效'}


# --------------------------------------------------------------------- 导航

@app.post('/api/navigate')
async def navigate(payload: dict = Body(default={})):
    url = (payload.get('url') or '').strip()
    if not url:
        return {'ok': False, 'error': '请输入网址', 'error_type': 'BadRequest'}
    return _resp(await _call(session.navigate, url))


@app.post('/api/history-nav')
async def history_nav(payload: dict = Body(default={})):
    how = payload.get('how') or 'refresh'
    fn = {'back': session.back, 'forward': session.forward, 'refresh': session.refresh}.get(how)
    if not fn:
        return {'ok': False, 'error': f'不支持的操作: {how}', 'error_type': 'BadRequest'}
    return _resp(await _call(fn))


# --------------------------------------------------------------------- 输入

@app.post('/api/scroll')
async def scroll(payload: dict = Body(default={})):
    return _resp(await _call(session.scroll, payload.get('dx', 0), payload.get('dy', 0),
                            payload.get('x'), payload.get('y')))


@app.post('/api/scroll-to')
async def scroll_to(payload: dict = Body(default={})):
    return _resp(await _call(session.scroll_to, bool(payload.get('top', True))))


@app.post('/api/click-at')
async def click_at(payload: dict = Body(default={})):
    return _resp(await _call(session.click_at, payload.get('x', 0), payload.get('y', 0),
                            payload.get('button', 'left'), int(payload.get('count', 1))))


@app.post('/api/move')
async def move(payload: dict = Body(default={})):
    return _resp(await _call(session.move_mouse, payload.get('x', 0), payload.get('y', 0)))


@app.post('/api/type')
async def type_text(payload: dict = Body(default={})):
    return _resp(await _call(session.insert_text, payload.get('text', ''),
                            bool(payload.get('submit'))))


@app.post('/api/key')
async def press_key(payload: dict = Body(default={})):
    return _resp(await _call(session.press_key, payload.get('key', 'Enter')))


# --------------------------------------------------------------------- 拾取

@app.post('/api/pick')
async def pick(payload: dict = Body(default={})):
    return await _call(workflow.pick_workflow, payload.get('x', 0), payload.get('y', 0),
                       payload.get('framePath') or [])


@app.post('/api/pick-mode')
async def pick_mode(payload: dict = Body(default={})):
    return _resp(await _call(session.set_pick_mode, bool(payload.get('on'))))


@app.post('/api/chain')
async def chain(payload: dict = Body(default={})):
    """向父级切换选取目标（默认深度 1 = 直接父元素）。"""
    res = await _call(session.chain_of, int(payload.get('depth', 1)),
                      payload.get('framePath') or [])
    if not res.get('ok'):
        return _resp(res)
    return await _call(workflow.adopt_desc, res['data'])


@app.get('/api/describe')
async def describe():
    res = await _call(session.describe_js)
    if not res.get('ok'):
        return _resp(res)
    return _resp(await _call(workflow.adopt_desc, res['data']))


@app.post('/api/apply')
async def apply_spec(payload: dict = Body(default={})):
    return _resp(await _call(workflow.apply_workflow, payload.get('spec'),
                            payload.get('framePath')))


@app.post('/api/usages')
async def usages(payload: dict = Body(default={})):
    """当前定位表达式 → 常用 DrissionPage 使用片段。"""
    return _resp(await _call(workflow.usage_workflow, payload.get('spec'),
                            payload.get('framePath'), payload.get('opts') or {}))


@app.post('/api/refresh-element')
async def refresh_element():
    return _resp(await _call(workflow.refresh_workflow))


@app.post('/api/highlight')
async def highlight(payload: dict = Body(default={})):
    res = await _call(session.highlight, payload.get('spec'),
                      payload.get('framePath') or [], payload.get('extra') or [])
    return _resp(res)


@app.post('/api/clear-highlight')
async def clear_highlight():
    return _resp(await _call(session.clear_highlight))


# --------------------------------------------------------------------- 动作

@app.post('/api/action')
async def run_action(payload: dict = Body(default={})):
    action = payload.get('action')
    spec = payload.get('spec')
    frame_path = payload.get('framePath') or []
    opts = payload.get('opts') or {}
    if not action:
        return {'ok': False, 'error': '缺少 action', 'error_type': 'BadRequest'}
    if spec is None:
        return {'ok': False, 'error': '请先捕获元素或选择定位表达式', 'error_type': 'NoElement'}

    res = await _call(session.element_action, action, spec, frame_path, **opts)
    if res.get('ok'):
        workflow.history_record_action(action, opts, True)
    body = dict(res)
    body['code'] = workflow.action_snippet(action, opts)
    body['actions'] = workflow.state.actions if res.get('ok') else None
    return _resp(body)


@app.post('/api/actions/clear')
async def clear_actions():
    return await _call(workflow.actions_clear)


# ----------------------------------------------------------------- 页面数据

@app.post('/api/page-data')
async def page_data(payload: dict = Body(default={})):
    return _resp(await _call(session.page_data, payload.get('what', 'html'),
                            payload.get('framePath') or []))


# --------------------------------------------------------------- 捕获历史

@app.get('/api/history')
async def history():
    return await _call(workflow.history_list)


@app.post('/api/history/add')
async def history_add(payload: dict = Body(default={})):
    return await _call(workflow.history_add, payload.get('name'))


@app.post('/api/history/update')
async def history_update(payload: dict = Body(default={})):
    return await _call(workflow.history_update, int(payload.get('id', 0)), payload)


@app.post('/api/history/remove')
async def history_remove(payload: dict = Body(default={})):
    return await _call(workflow.history_remove, int(payload.get('id', 0)))


@app.post('/api/history/clear')
async def history_clear():
    return await _call(workflow.history_clear)


@app.post('/api/export')
async def export(payload: dict = Body(default={})):
    return await _call(workflow.export_script, payload.get('url') or '',
                       bool(payload.get('headless')))


# --------------------------------------------------------------- WebSocket

@app.websocket('/ws')
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    sub = Subscriber(loop)
    wake = asyncio.Event()
    sub.attach(wake)
    send_lock = asyncio.Lock()
    outbox: asyncio.Queue = asyncio.Queue()
    session.subscribe(sub)

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def sender():
        while True:
            frame, events = sub.drain()
            for ev in events:
                await send(ev)
            while not outbox.empty():
                await send(outbox.get_nowait())
            if frame:
                await send(frame)
            try:
                await asyncio.wait_for(wake.wait(), timeout=0.4)
            except asyncio.TimeoutError:
                pass
            wake.clear()

    sender_task = asyncio.create_task(sender())
    try:
        await send({'type': 'hello', 'status': await asyncio.to_thread(session.status)})
        while True:
            msg = await ws.receive_json()
            cmd = msg.get('cmd')
            try:
                if cmd == 'ping':
                    outbox.put_nowait({'type': 'pong', 'ts': msg.get('ts')})
                elif cmd == 'pick':
                    payload = await _call(workflow.pick_workflow, msg.get('x', 0), msg.get('y', 0),
                                          msg.get('framePath') or [])
                    outbox.put_nowait({'type': 'picked', 'token': msg.get('token'), 'payload': payload})
                elif cmd == 'apply':
                    payload = await _call(workflow.apply_workflow, msg.get('spec'),
                                          msg.get('framePath'))
                    outbox.put_nowait({'type': 'applied', 'token': msg.get('token'), 'payload': payload})
                elif cmd == 'pickmode':
                    res = await _call(session.set_pick_mode, bool(msg.get('on')))
                    outbox.put_nowait({'type': 'pickmode', 'ok': res.get('ok'),
                                       'on': bool(msg.get('on'))})
                elif cmd == 'preset':
                    settings.set_preset(msg.get('name') or 'standard')
                    session.mark_dirty(3)
                    outbox.put_nowait({'type': 'preset', 'name': settings.stream_preset})
                else:
                    outbox.put_nowait({'type': 'error', 'error': f'未知命令: {cmd}'})
            except Exception as exc:
                outbox.put_nowait({'type': 'error', 'error': str(exc)})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug('WebSocket 异常: %s', exc)
    finally:
        sender_task.cancel()
        session.unsubscribe(sub)
