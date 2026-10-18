"""浏览器会话：DrissionPage 生命周期、线程安全封装、镜像帧泵、元素操作。

稳定性要点：
  1. DrissionPage 的 driver 不是线程安全的，所有调用都串行化到同一把可重入锁；
  2. 帧泵异常绝不退出循环，失败后降级并限速重试；
  3. 元素操作一律「按定位表达式重新解析」，不缓存元素对象，避免 ElementLostError；
  4. 浏览器断开可自动重连（reconnect）。
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from DrissionPage import ChromiumOptions, ChromiumPage
from DrissionPage.errors import ElementLostError, PageDisconnectedError

from ..config import STATIC_DIR, settings

log = logging.getLogger('dpc.browser')

PICKER_JS_PATH = STATIC_DIR / 'picker.js'

# 链式定位里表示「进入 Shadow Root」的前缀
SHADOW_PREFIX = 'shadow:'

# 特殊按键 → CDP windowsVirtualKeyCode
KEYS: dict[str, tuple[int, str, str]] = {
    'Enter': (13, 'Enter', '\r'),
    'Tab': (9, 'Tab', '\t'),
    'Escape': (27, 'Escape', ''),
    'Backspace': (8, 'Backspace', ''),
    'Delete': (46, 'Delete', ''),
    'ArrowLeft': (37, 'ArrowLeft', ''),
    'ArrowUp': (38, 'ArrowUp', ''),
    'ArrowRight': (39, 'ArrowRight', ''),
    'ArrowDown': (40, 'ArrowDown', ''),
    'Home': (36, 'Home', ''),
    'End': (35, 'End', ''),
    'PageUp': (33, 'PageUp', ''),
    'PageDown': (34, 'PageDown', ''),
    'Space': (32, 'Space', ' '),
    'F5': (116, 'F5', ''),
}


def read_picker_js() -> str:
    return PICKER_JS_PATH.read_text(encoding='utf-8')


def _debug_port_busy(port: int) -> bool:
    """调试端口已被占用 → 说明将复用已有浏览器实例（可能是上次残留）。"""
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(('127.0.0.1', int(port))) == 0


class Subscriber:
    """一个 WebSocket 订阅者。帧只保留最新，事件按序保留。"""

    def __init__(self, loop) -> None:
        self.loop = loop
        self.frames = deque(maxlen=1)
        self.events: deque = deque(maxlen=200)
        self.alive = True
        self._wake = None
        self._lock = threading.Lock()

    def attach(self, wake) -> None:
        self._wake = wake

    def push_frame(self, msg: dict) -> None:
        with self._lock:
            self.frames.append(msg)
        self._wakeup()

    def push_event(self, msg: dict) -> None:
        with self._lock:
            self.events.append(msg)
        self._wakeup()

    def _wakeup(self) -> None:
        if self._wake and self.loop and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass

    def drain(self) -> tuple[Optional[dict], list]:
        with self._lock:
            frame = self.frames.pop() if self.frames else None
            self.frames.clear()
            events = list(self.events)
            self.events.clear()
        return frame, events


class BrowserSession:
    """封装一个受控的 Chromium 实例。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._page: Optional[ChromiumPage] = None
        self._picker_js = read_picker_js()
        self._init_js_id = None
        self._started_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._inject_fail = 0

        self._subs: list[Subscriber] = []
        self._subs_lock = threading.Lock()

        self._pump: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._dirty = 0
        self._busy = False  # 有长操作进行中（如导航），暂停抓帧

        # 统计
        self.stats = {'frames': 0, 'frame_errors': 0, 'picks': 0, 'actions': 0}
        self._seen_rev: dict[int, int] = {}
        # 进程正常退出时顺手关掉浏览器，避免留下孤儿 Chromium 进程
        atexit.register(self._atexit_cleanup)

    def _atexit_cleanup(self) -> None:
        try:
            if self._page is not None:
                self._page.quit()
        except Exception:
            pass

    # ------------------------------------------------------------------ 状态

    @property
    def page(self) -> ChromiumPage:
        page = self._page
        if page is None:
            raise PageDisconnectedError('浏览器未启动')
        return page

    def is_alive(self) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            return bool(page.driver.is_running)
        except Exception:
            return False

    def status(self) -> dict:
        info = {
            'running': self.is_alive(),
            'last_error': self._last_error,
            'debug_port': settings.debug_port,
            'headless': settings.headless,
            'uptime': round(time.time() - self._started_at, 1) if self._started_at else 0,
            'viewers': len(self._subs),
            'stats': dict(self.stats),
            'preset': settings.stream_preset,
            'stream_enabled': settings.stream_enabled,
        }
        if not self.is_alive():
            return info
        try:
            info.update(self._op(self._page_info_locked)['data'] or {})
        except Exception:
            pass
        return info

    def _page_info_locked(self) -> dict:
        page = self.page
        try:
            info = page.run_js(
                'return {u: location.href, t: document.title, sx: window.scrollX, sy: window.scrollY,'
                ' iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio,'
                ' rs: document.readyState, sh: document.documentElement.scrollHeight,'
                ' sw: document.documentElement.scrollWidth, ok: (typeof window.__DPC__ !== "undefined")};')
            metrics = dict(info or {})
        except Exception as exc:
            metrics = {'err': str(exc)}
        frames = []
        try:
            for fr in page.get_frames():
                try:
                    frames.append({'url': fr.url, 'name': getattr(fr, 'name', ''), 'id': fr.frame_id})
                except Exception:
                    continue
        except Exception:
            pass
        return {'metrics': metrics, 'frames': frames, 'url': page.url, 'title': page.title}

    # ------------------------------------------------------------------ 启停

    def start(self, url: Optional[str] = None) -> dict:
        with self._lock:
            if self.is_alive():
                return {'ok': True, 'data': {'already': True}}
        # 浏览器尚未启动时也要能执行，因此跳过存活检查
        return self._op(lambda: self._start_locked(url), require_alive=False)

    def _start_locked(self, url: Optional[str]) -> dict:
        if self.is_alive():
            return {'already': True}

        reused = _debug_port_busy(settings.debug_port)
        co = ChromiumOptions()
        co.set_local_port(settings.debug_port)
        co.set_argument('--no-first-run')
        co.set_argument('--no-default-browser-check')
        co.set_argument('--disable-features=Translate,OptimizationHints')
        co.set_argument('--disable-popup-blocking')
        co.set_argument(f'--window-size={settings.window_width},{settings.window_height}')
        if settings.headless:
            co.headless(True)
        if settings.browser_path:
            co.set_browser_path(settings.browser_path)
        if settings.user_data_dir:
            co.set_user_data_path(settings.user_data_dir)

        page = ChromiumPage(co)
        try:
            page.set.window.size(settings.window_width, settings.window_height)
        except Exception:
            pass
        try:
            page.set.timeouts(base=settings.timeout, page_load=settings.timeout * 3,
                              script=settings.timeout * 2)
        except Exception:
            pass
        try:
            page.set.load_mode = settings.load_mode
        except Exception:
            pass
        try:
            page.set.retry_times = settings.retry_times
            page.set.retry_interval = settings.retry_interval
        except Exception:
            pass
        try:
            page.set.auto_handle_alert = False
        except Exception:
            pass

        self._page = page
        self._started_at = time.time()
        self._last_error = None
        self._init_js_id = None
        self._install_picker()
        target = url or settings.start_url
        if target:
            try:
                page.get(self._as_url(target))
            except Exception as exc:
                log.warning('初始导航失败: %s', exc)
        self._inject_all()
        self._start_pump()
        self._dirty = settings.burst_frames
        if reused:
            log.warning('调试端口 %s 上已有浏览器，本次为接管复用；若行为异常请点「重启浏览器」',
                        settings.debug_port)
        return {'url': page.url, 'reused': reused}

    def _as_url(self, target: str) -> str:
        if target.startswith(('http://', 'https://', 'file://', 'about:', 'data:', 'chrome://')):
            return target
        path = Path(target)
        if path.exists():
            return path.resolve().as_uri()
        return 'https://' + target.lstrip('/')

    def stop(self) -> dict:
        with self._lock:
            page = self._page
            self._page = None
            self._init_js_id = None
            try:
                if page is not None:
                    page.quit()
            except Exception as exc:
                log.info('退出浏览器: %s', exc)
        return {'stopped': True}

    def restart(self) -> dict:
        self.stop()
        time.sleep(0.6)
        return self.start()

    def ensure_alive(self) -> bool:
        """连接断开时尝试重连一次。"""
        if self.is_alive():
            return True
        page = self._page
        if page is None:
            return False
        try:
            with self._lock:
                page.reconnect()
                time.sleep(0.3)
                return self.is_alive()
        except Exception as exc:
            self._last_error = f'重连失败: {exc}'
            return False

    # ------------------------------------------------------------------ 注入

    def _install_picker(self) -> None:
        try:
            self._init_js_id = self.page.add_init_js(self._picker_js)
        except Exception as exc:
            log.warning('add_init_js 失败: %s', exc)

    def _inject_all(self) -> None:
        """向主文档与所有 iframe 注入拾取器。"""
        try:
            self.page.run_js(self._picker_js)
        except Exception as exc:
            log.debug('主文档注入失败: %s', exc)
        try:
            for fr in self.page.get_frames():
                try:
                    fr.run_js(self._picker_js)
                except Exception:
                    continue
        except Exception:
            pass

    def _drain_locked(self) -> Optional[dict]:
        try:
            got = self.page.run_js('return window.__DPC__ ? window.__DPC__.drain() : null;')
        except Exception:
            return None
        if not got:
            return None
        if isinstance(got, str):
            try:
                got = json.loads(got)
            except Exception:
                return None
        return got

    # ------------------------------------------------------------------ 帧泵

    def subscribe(self, sub: Subscriber) -> None:
        with self._subs_lock:
            self._subs.append(sub)
        self._dirty = settings.burst_frames

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._subs_lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    def _broadcast_frame(self, msg: dict) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for sub in subs:
            sub.push_frame(msg)

    def _broadcast_event(self, msg: dict) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for sub in subs:
            sub.push_event(msg)

    def mark_dirty(self, n: Optional[int] = None) -> None:
        self._dirty = max(self._dirty, n or settings.burst_frames)

    def _start_pump(self) -> None:
        if self._pump and self._pump.is_alive():
            return
        self._stopping.clear()
        self._pump = threading.Thread(target=self._pump_loop, name='dpc-frame-pump', daemon=True)
        self._pump.start()

    def stop_pump(self) -> None:
        self._stopping.set()

    def _pump_loop(self) -> None:
        last_rev = -1
        last_url = None
        force_at = 0.0
        last_push = 0.0
        missed = 0
        while not self._stopping.is_set():
            interval = max(0.02, settings.poll_ms / 1000.0)
            t0 = time.perf_counter()
            try:
                with self._subs_lock:
                    viewers = len(self._subs)
                if not viewers or not is_view_stream_enabled():
                    time.sleep(0.25)
                    continue
                if not self.is_alive():
                    time.sleep(0.5)
                    continue

                with self._lock:
                    info = self._drain_locked()
                now = time.time()

                if info is None:
                    # 拾取器不存在（新文档 / 未注入）→ 补注入
                    missed += 1
                    if missed % 20 == 1:
                        with self._lock:
                            self._inject_all()
                    info = {}
                else:
                    missed = 0
                    if info.get('events'):
                        for ev in info['events']:
                            ev['url'] = info.get('url')
                            if ev.get('kind') == 'pick':
                                self.stats['picks'] += 1
                            self._broadcast_event({'type': 'page_event', 'event': ev})

                rev = int(info.get('rev', -1))
                changed = rev != last_rev
                force_due = now >= force_at
                busy = self._busy
                need = self._dirty > 0 or force_due or (changed and not busy)

                min_interval = 1.0 / max(1, settings.preset['max_fps'])
                if need and not busy and (now - last_push) >= min_interval:
                    shot = self._grab_frame(info, t0)
                    if shot:
                        self._broadcast_frame(shot)
                        self.stats['frames'] += 1
                        last_push = now
                        last_rev = rev
                        force_at = now + settings.preset['force_ms'] / 1000.0
                        if self._dirty > 0:
                            self._dirty -= 1
                    else:
                        self.stats['frame_errors'] += 1
                        force_at = now + 0.6

                url = info.get('url')
                if url and url != last_url:
                    last_url = url
                    self._broadcast_event({'type': 'nav', 'url': url, 'title': info.get('title', '')})
                    with self._lock:
                        self._inject_all()
                    self.mark_dirty(3)
            except Exception as exc:  # 帧泵绝不退出
                self.stats['frame_errors'] += 1
                log.debug('帧泵异常: %s', exc)
                time.sleep(0.4)
            spent = time.perf_counter() - t0
            time.sleep(max(0.0, interval - spent))

    def _grab_frame(self, info: dict, t0: float) -> Optional[dict]:
        sx = float(info.get('sx') or 0.0)
        sy = float(info.get('sy') or 0.0)
        iw = float(info.get('iw') or 0.0)
        ih = float(info.get('ih') or 0.0)
        if iw < 20 or ih < 20:
            return None
        preset = settings.preset
        # Chrome 的 clip.scale 会再乘上 devicePixelRatio，这里除回去，
        # 保证出图尺寸 ≈ 视口 × 预设倍率，带宽与清晰度可预期。
        dpr = max(1.0, float(info.get('dpr') or 1.0))
        scale = round(preset['scale'] / dpr, 4)
        clip = {'x': sx, 'y': sy, 'width': iw, 'height': ih, 'scale': scale}
        try:
            with self._lock:
                res = self.page.run_cdp('Page.captureScreenshot', format='jpeg',
                                        quality=preset['quality'], clip=clip,
                                        captureBeyondViewport=False)
        except Exception as exc:
            log.debug('抓帧失败: %s', exc)
            return None
        data = (res or {}).get('data')
        if not data:
            return None
        return {
            'type': 'frame',
            'data': data,
            # cw/ch 是 CSS 像素视口宽高：前端用它把鼠标位置换算成页面坐标
            'cw': iw, 'ch': ih,
            'scale': scale, 'dpr': dpr,
            'sx': sx, 'sy': sy,
            'url': info.get('url'), 'title': info.get('title'),
            'rev': info.get('rev'),
            'ts': int(time.time() * 1000),
            'grab_ms': round((time.perf_counter() - t0) * 1000, 1),
        }

    # ------------------------------------------------------------------ 工具

    def _op(self, fn: Callable, *args, require_alive: bool = True, **kwargs) -> dict:
        """统一封装：串联行化锁 + 把异常转成结构化结果，避免 500。"""
        t0 = time.perf_counter()
        try:
            if require_alive and not self.ensure_alive():
                raise PageDisconnectedError('浏览器未启动或连接已断开')
            with self._lock:
                data = fn(*args, **kwargs)
            return {'ok': True, 'data': data, 'error': None, 'error_type': None,
                    'ms': round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:
            self._last_error = f'{type(exc).__name__}: {exc}'.strip()
            log.info('操作失败 %s: %s', getattr(fn, '__name__', fn), exc)
            return {'ok': False, 'data': None, 'error': str(exc).strip() or repr(exc),
                    'error_type': type(exc).__name__,
                    'ms': round((time.perf_counter() - t0) * 1000, 1)}

    @staticmethod
    def _rect_of(node) -> Optional[list]:
        """元素的视口相对矩形（与 getBoundingClientRect 完全一致）。"""
        got = node.run_js('var r=this.getBoundingClientRect();'
                          'return [r.left, r.top, r.width, r.height];')
        if not got:
            return None
        return [round(float(v), 2) for v in got]

    @staticmethod
    def _border_offset(node) -> list:
        got = node.run_js('var c=getComputedStyle(this);'
                          'return [parseFloat(c.borderLeftWidth)||0, parseFloat(c.borderTopWidth)||0];')
        if not got:
            return [0.0, 0.0]
        return [float(got[0]), float(got[1])]

    @staticmethod
    def _as_parent_element(obj):
        """如果是 frame 对象，取回它在父文档里的 <iframe> 元素（其 run_js 才在父文档执行）。"""
        inner = getattr(obj, 'frame_ele', None)
        return inner if inner is not None else obj

    def _frame_offset_locked(self, frame_path: list) -> list:
        """累加各级 iframe 的视口偏移，得到框架内容坐标系在顶层视口中的原点。"""
        off = [0.0, 0.0]
        owner = self.page
        for loc in frame_path or []:
            ele = owner.ele(loc, timeout=0.5)
            if not ele:
                raise ElementLostError(f'找不到 iframe: {loc}')
            ele = self._as_parent_element(ele)
            rect = self._rect_of(ele)
            border = self._border_offset(ele)
            if rect:
                off[0] += rect[0] + border[0]
                off[1] += rect[1] + border[1]
            owner = owner.get_frame(loc)
        return off

    def _owner_locked(self, frame_path: list):
        owner = self.page
        for loc in frame_path or []:
            owner = owner.get_frame(loc)
        return owner

    # ------------------------------------------------------------------ 导航

    def navigate(self, url: str) -> dict:
        return self._op(self._navigate_locked, url)

    def _navigate_locked(self, url: str) -> dict:
        self._busy = True
        try:
            self.page.get(self._as_url(url))
        finally:
            self._busy = False
        self._inject_all()
        self.mark_dirty(6)
        return {'url': self.page.url, 'title': self.page.title}

    def back(self) -> dict:
        return self._op(self._hist_locked, 'back')

    def forward(self) -> dict:
        return self._op(self._hist_locked, 'forward')

    def _hist_locked(self, how: str) -> dict:
        self._busy = True
        try:
            getattr(self.page, how)()
        finally:
            self._busy = False
        self._inject_all()
        self.mark_dirty(6)
        return {'url': self.page.url, 'title': self.page.title}

    def refresh(self) -> dict:
        return self._op(self._refresh_locked)

    def _refresh_locked(self) -> dict:
        self._busy = True
        try:
            self.page.refresh()
        finally:
            self._busy = False
        self._inject_all()
        self.mark_dirty(6)
        return {'url': self.page.url}

    def resize(self, width: int, height: int) -> dict:
        def run():
            self.page.set.window.size(int(width), int(height))
            settings.window_width, settings.window_height = int(width), int(height)
            self.mark_dirty(3)
            return {'w': width, 'h': height}
        return self._op(run)

    # ------------------------------------------------------------------ 输入

    def scroll(self, dx: int = 0, dy: int = 0, x: float | None = None, y: float | None = None) -> dict:
        return self._op(self._scroll_locked, dx, dy, x, y)

    def _scroll_locked(self, dx, dy, x, y) -> dict:
        page = self.page
        if x is None or y is None:
            info = page.run_js('return [window.innerWidth/2, window.innerHeight/2];') or [400, 300]
            x, y = info[0], info[1]
        page.run_cdp('Input.dispatchMouseEvent', type='mouseWheel', x=float(x), y=float(y),
                     deltaX=float(dx), deltaY=float(dy), button='none')
        self.mark_dirty(3)
        return {'dx': dx, 'dy': dy}

    def scroll_to(self, top: bool = True) -> dict:
        def run():
            if top:
                self.page.scroll.to_top()
            else:
                self.page.scroll.to_bottom()
            self.mark_dirty(3)
            return {'top': top}
        return self._op(run)

    def click_at(self, x: float, y: float, button: str = 'left', count: int = 1) -> dict:
        return self._op(self._click_at_locked, x, y, button, count)

    def _click_at_locked(self, x, y, button, count) -> dict:
        page = self.page
        common = {'x': float(x), 'y': float(y), 'button': button,
                  'buttons': 1 if button == 'left' else 2, 'clickCount': int(count)}
        page.run_cdp('Input.dispatchMouseEvent', type='mouseMoved', x=float(x), y=float(y),
                     button='none')
        page.run_cdp('Input.dispatchMouseEvent', type='mousePressed', **common)
        page.run_cdp('Input.dispatchMouseEvent', type='mouseReleased', **common)
        self.mark_dirty(4)
        return {'x': x, 'y': y, 'button': button, 'count': count}

    def move_mouse(self, x: float, y: float) -> dict:
        return self._op(self._move_locked, x, y)

    def _move_locked(self, x, y) -> dict:
        self.page.run_cdp('Input.dispatchMouseEvent', type='mouseMoved', x=float(x), y=float(y),
                          button='none')
        return {'x': x, 'y': y}

    def insert_text(self, text: str, submit: bool = False) -> dict:
        return self._op(self._insert_text_locked, text, submit)

    def _insert_text_locked(self, text, submit) -> dict:
        page = self.page
        if text:
            page.run_cdp('Input.insertText', text=text)
        if submit:
            self._press_key_locked('Enter')
        self.mark_dirty(3)
        return {'inserted': len(text or ''), 'submit': submit}

    def press_key(self, key: str) -> dict:
        return self._op(self._press_key_locked, key)

    def _press_key_locked(self, key: str) -> dict:
        if key not in KEYS:
            raise ValueError(f'不支持的按键: {key}（可选 {", ".join(KEYS)}）')
        vk, name, text = KEYS[key]
        page = self.page
        page.run_cdp('Input.dispatchKeyEvent', type='rawKeyDown', windowsVirtualKeyCode=vk,
                     key=name, code=name, text=text, unmodifiedText=text)
        if text:
            page.run_cdp('Input.dispatchKeyEvent', type='char', text=text, key=name,
                         windowsVirtualKeyCode=vk)
        page.run_cdp('Input.dispatchKeyEvent', type='keyUp', windowsVirtualKeyCode=vk,
                     key=name, code=name)
        self.mark_dirty(3)
        return {'key': key}

    # ------------------------------------------------------------------ 拾取

    def set_pick_mode(self, on: bool) -> dict:
        return self._op(self._set_pick_locked, on)

    def _set_pick_locked(self, on: bool) -> dict:
        res = self.page.run_js('return window.__DPC__ ? window.__DPC__.setPick(%s) : null;'
                               % ('true' if on else 'false'))
        if res is None:
            self._inject_all()
            res = self.page.run_js('return window.__DPC__ ? window.__DPC__.setPick(%s) : null;'
                                   % ('true' if on else 'false'))
        self.mark_dirty(2)
        return {'pick': bool(res)}

    def pick_at(self, x: float, y: float, frame_path: list | None = None) -> dict:
        return self._op(self._pick_at_locked, x, y, frame_path or [])

    def _pick_at_locked(self, x, y, frame_path) -> dict:
        owner = self._owner_locked(frame_path)
        offset = self._frame_offset_locked(frame_path)
        lx = float(x) - offset[0]
        ly = float(y) - offset[1]
        owner.run_js(self._picker_js)
        desc = owner.run_js('return window.__DPC__.pickAt(%s, %s, false);' % (lx, ly))
        if not desc:
            raise ElementLostError('该坐标上没有可捕获的元素')
        # 统一到顶层文档的视口坐标系，前端才能直接画在镜像图上
        raw = list(desc.get('rect') or [0, 0, 0, 0])
        desc['frameRect'] = raw
        desc['rect'] = [round(raw[0] + offset[0], 2), round(raw[1] + offset[1], 2), raw[2], raw[3]]
        desc['framePath'] = list(frame_path)
        desc['offset'] = offset
        desc['frameXY'] = [lx, ly]
        desc['viewportXY'] = [x, y]
        self.stats['picks'] += 1
        self.mark_dirty(3)
        return desc

    def chain_of(self, depth: int, frame_path: list | None = None) -> dict:
        return self._op(self._chain_locked, depth, frame_path or [])

    def _chain_locked(self, depth, frame_path) -> dict:
        owner = self._owner_locked(frame_path)
        offset = self._frame_offset_locked(frame_path)
        desc = owner.run_js('return window.__DPC__ ? window.__DPC__.chainOf(%d) : null;' % int(depth))
        if not desc:
            raise ElementLostError('已到达可选取的最上层元素')
        desc['framePath'] = list(frame_path)
        self.mark_dirty(2)
        return desc

    def describe_js(self, frame_path: list | None = None) -> dict:
        return self._op(self._describe_js_locked, frame_path or [])

    def _describe_js_locked(self, frame_path) -> dict:
        owner = self._owner_locked(frame_path)
        desc = owner.run_js('var el = window.__DPC__ && window.__DPC__.target;'
                            'return el ? window.__DPC__.describe(el, true) : null;')
        if not desc:
            raise ElementLostError('当前没有已捕获的元素')
        desc['framePath'] = list(frame_path)
        return desc

    # ------------------------------------------------------------------ 高亮

    def highlight(self, locator: str, frame_path: list | None = None,
                  extra_locators: list | None = None) -> dict:
        return self._op(self._highlight_locked, locator, frame_path or [], extra_locators or [])

    def _highlight_locked(self, locator, frame_path, extra_locators) -> dict:
        owner = self._owner_locked(frame_path)
        offset = self._frame_offset_locked(frame_path)
        locators = [locator] + [x for x in extra_locators if x]
        out = []
        total = 0
        truncated = False
        for loc in locators:
            try:
                parent, last = self._resolve_parent_locked(owner, loc)
                eles = self._eles_of(parent, last, many=True)
            except Exception as exc:
                out.append({'locator': loc, 'error': str(exc), 'rects': [], 'count': -1})
                continue
            rects = []
            for ele in eles:
                if len(rects) >= settings.max_highlight:
                    truncated = True
                    break
                try:
                    rect = self._rect_of(ele)
                except Exception:
                    continue
                if rect and rect[2] > 0 and rect[3] > 0:
                    rects.append([round(rect[0] + offset[0], 2), round(rect[1] + offset[1], 2),
                                  rect[2], rect[3]])
            out.append({'locator': loc, 'count': len(eles), 'rects': rects, 'error': None})
            total += len(eles)
        viewport = self.page.run_js('return [window.innerWidth, window.innerHeight];') or [0, 0]
        primary = [r for r in out[0]['rects']] if out else []
        self._draw_in_page_locked(primary, viewport, locators)
        self.mark_dirty(3)
        return {'groups': out, 'total': total, 'truncated': truncated,
                'viewport': viewport, 'drew': bool(primary)}

    def _draw_in_page_locked(self, rects, viewport, locators) -> None:
        """同时在真实浏览器窗口内画出高亮，便于肉眼确认。"""
        try:
            owner = self.page
            payload = json.dumps(rects)
            owner.run_js('if (window.__DPC__) { window.__DPC__.clearDraw();'
                         'if (%s.length) { window.__DPC__.drawRects(%s, {color:"#f59e0b", primary:0}); } }'
                         % (payload, payload))
        except Exception:
            pass

    def clear_highlight(self) -> dict:
        return self._op(self._clear_highlight_locked)

    def _clear_highlight_locked(self) -> dict:
        try:
            self.page.run_js('if (window.__DPC__) { window.__DPC__.clearDraw(); }')
        except Exception:
            pass
        self.mark_dirty(2)
        return {'cleared': True}

    # ------------------------------------------------------------------ 校验

    def validate_specs(self, specs: list, frame_path: list | None = None,
                       target_rect: list | None = None, target_tag: str | None = None,
                       max_items: int | None = None) -> dict:
        """一次性用 DrissionPage 复核多条定位表达式（同一次加锁，避免多次往返）。"""
        return self._op(self._validate_specs_locked, specs, frame_path or [],
                        target_rect, target_tag, max_items)

    def _validate_specs_locked(self, specs, frame_path, target_rect, target_tag, max_items) -> dict:
        owner = self._owner_locked(frame_path)
        offset = self._frame_offset_locked(frame_path)
        cap = int(max_items or settings.max_highlight)
        items = []
        for spec in specs:
            item = {
                'spec': spec,
                'value': spec if isinstance(spec, str) else ' >> '.join(spec),
                'chain': spec if isinstance(spec, list) else None,
                'count': -1, 'identity': None, 'geometry': False,
                'rects': [], 'tags': [], 'first_rect': None,
                'tag': None, 'error': None,
                'hasTarget': bool(target_rect or target_tag),
            }
            try:
                parent, last = self._resolve_parent_locked(owner, spec)
                eles = self._eles_of(parent, last, many=True)
                item['count'] = len(eles)
                for idx, ele in enumerate(eles):
                    if idx == 0:
                        item['tag'] = self._tag_of(ele)
                        item['identity'] = self._identity_locked(ele)
                    if idx < 8:
                        item['tags'].append(self._tag_of(ele))
                    if len(item['rects']) < cap:
                        rect = self._rect_of(ele)
                        if rect and rect[2] > 0 and rect[3] > 0:
                            item['rects'].append([round(rect[0] + offset[0], 2),
                                                  round(rect[1] + offset[1], 2), rect[2], rect[3]])
                if item['count'] > cap:
                    item['truncated'] = True
                if item['rects']:
                    item['first_rect'] = item['rects'][0]
                    if target_rect and target_tag and item['tag'] == target_tag:
                        item['geometry'] = _rect_close(item['first_rect'], target_rect)
            except Exception as exc:
                item['error'] = str(exc).strip() or repr(exc)
            items.append(item)
        viewport = self.page.run_js('return [window.innerWidth, window.innerHeight];') or [0, 0]
        return {'items': items, 'viewport': viewport}

    def _resolve_parent_locked(self, owner, spec) -> tuple:
        """把定位表达式拆成「父级对象 + 最后一步」。

        链式表达式的每一步都可以带 `shadow:` 前缀，表示先进入上一级元素的
        Shadow Root 再查找，例如 ['#shadowHost', 'shadow:#shadowBtn']。
        """
        steps = [spec] if isinstance(spec, str) else list(spec)
        obj = owner
        for step in steps[:-1]:
            obj = self._eles_of(obj, step, many=False)
            if not obj:
                raise ElementLostError(f'相对定位失败：{step}')
        return obj, steps[-1]

    def _eles_of(self, obj, step, many: bool):
        if isinstance(step, str) and step.startswith(SHADOW_PREFIX):
            root = obj.shadow_root
            loc = step[len(SHADOW_PREFIX):]
            return root.eles(loc, timeout=0.8) if many else root.ele(loc, timeout=0.8)
        return obj.eles(step, timeout=0.8) if many else obj.ele(step, timeout=0.8)

    def _ele_of_locked(self, owner, spec):
        """解析出单个元素（支持相对定位链与 Shadow DOM）。"""
        parent, last = self._resolve_parent_locked(owner, spec)
        return self._eles_of(parent, last, many=False)

    @staticmethod
    def _tag_of(ele) -> Optional[str]:
        try:
            return ele.tag
        except Exception:
            return None

    @staticmethod
    def _identity_locked(ele) -> Optional[bool]:
        """精确身份比对：DrissionPage 解析出的首元素是否就是用户拾取的那个。"""
        try:
            got = ele.run_js('return (window.__DPC__ && window.__DPC__.target)'
                             ' ? (this === window.__DPC__.target) : null;')
            return None if got is None else bool(got)
        except Exception:
            return None

    def element_paths(self, locator, frame_path: list | None = None) -> dict:
        """取 DrissionPage 自己生成的 css_path / xpath，作为兜底候选。"""
        return self._op(self._element_paths_locked, locator, frame_path or [])

    def _element_paths_locked(self, locator, frame_path) -> list:
        owner = self._owner_locked(frame_path)
        ele = self._ele_of_locked(owner, locator)
        if not ele:
            return []
        out = {}
        try:
            out['css'] = ele.css_path
        except Exception:
            pass
        try:
            out['xpath'] = ele.xpath
        except Exception:
            pass
        return [out] if out else []

    # ------------------------------------------------------------------ 元素动作

    def element_action(self, action: str, locator: str, frame_path: list | None = None,
                       **kwargs) -> dict:
        return self._op(self._element_action_locked, action, locator, frame_path or [], kwargs)

    def _element_action_locked(self, action, locator, frame_path, opts) -> dict:
        owner = self._owner_locked(frame_path)
        # 每次都按定位表达式重新解析，绝不缓存元素对象
        ele = self._ele_of_locked(owner, locator)
        if not ele:
            raise ElementLostError(f'未找到元素：{locator}')
        self.stats['actions'] += 1
        self.mark_dirty(4)

        if action == 'click':
            ele.click(by_js=False)
        elif action == 'click_js':
            ele.click(by_js=True)
        elif action == 'right_click':
            ele.click.right()
        elif action == 'double_click':
            ele.click.multi(times=2)
        elif action == 'hover':
            ele.hover()
        elif action == 'focus':
            ele.focus()
        elif action == 'blur':
            ele.run_js('this.blur();')
        elif action == 'input':
            text = opts.get('text', '')
            if opts.get('clear_first'):
                try:
                    ele.clear()
                except Exception:
                    ele.input('\ue009\ue003')
            ele.input(text, clear=False)
        elif action == 'clear':
            ele.clear()
        elif action == 'check':
            ele.check(uncheck=bool(opts.get('uncheck')))
        elif action == 'select':
            self._select_locked(ele, opts)
        elif action == 'scroll_to':
            ele.scroll.to_see()
        elif action == 'get_text':
            return {'text': ele.text}
        elif action == 'get_raw_text':
            return {'text': ele.raw_text}
        elif action == 'get_html':
            return {'html': ele.html if not opts.get('inner') else ele.inner_html}
        elif action == 'get_attr':
            name = opts.get('name') or 'href'
            return {'name': name, 'value': ele.attr(name)}
        elif action == 'get_attrs':
            return {'attrs': dict(ele.attrs)}
        elif action == 'get_property':
            name = opts.get('name') or 'value'
            return {'name': name, 'value': ele.property(name)}
        elif action == 'get_value':
            return {'value': ele.value}
        elif action == 'get_states':
            return {'states': self._states_of(ele)}
        elif action == 'get_rect':
            return {'rect': self._rect_of(ele), 'location': list(ele.rect.location),
                    'size': list(ele.rect.size)}
        elif action == 'get_screenshot':
            return self._element_shot_locked(ele, opts)
        elif action == 'get_css':
            name = opts.get('name') or 'color'
            return {'name': name, 'value': ele.style(name)}
        else:
            raise ValueError(f'未知动作: {action}')

        result = {'action': action, 'locator': locator}
        try:
            result['text'] = ele.text
            if ele.tag in ('input', 'textarea', 'select'):
                result['value'] = ele.value
            result['states'] = self._states_of(ele)
        except Exception:
            pass
        return result

    @staticmethod
    def _states_of(ele) -> dict:
        out = {}
        st = ele.states
        for name in ('is_displayed', 'is_enabled', 'is_checked', 'is_selected',
                     'is_alive', 'is_in_viewport', 'is_whole_in_viewport', 'is_covered'):
            try:
                out[name] = bool(getattr(st, name))
            except Exception:
                out[name] = None
        return out

    def _select_locked(self, ele, opts) -> None:
        by = opts.get('by') or 'value'
        value = opts.get('value', '')
        if by == 'value':
            ele.select.by_value(value)
        elif by == 'text':
            ele.select.by_text(value)
        elif by == 'index':
            ele.select.by_index(int(value))
        else:
            ele.select.by_value(value)

    def _element_shot_locked(self, ele, opts) -> dict:
        from ..config import SNAPSHOT_DIR
        name = opts.get('name') or f'ele_{int(time.time() * 1000)}.png'
        if not name.lower().endswith('.png'):
            name += '.png'
        path = SNAPSHOT_DIR / name
        ele.get_screenshot(path=str(SNAPSHOT_DIR), name=name)
        return {'file': str(path), 'name': name}

    # ------------------------------------------------------------------ 页面数据

    def page_data(self, what: str, frame_path: list | None = None) -> dict:
        return self._op(self._page_data_locked, what, frame_path or [])

    def _page_data_locked(self, what, frame_path) -> dict:
        owner = self._owner_locked(frame_path)
        if what == 'html':
            return {'html': owner.html}
        if what == 'title':
            return {'title': self.page.title}
        if what == 'url':
            return {'url': self.page.url}
        if what == 'cookies':
            return {'cookies': dict(self.page.cookies.as_dict())}
        if what == 'screenshot':
            return self.page_screenshot_locked()
        raise ValueError(f'未知数据: {what}')

    def page_screenshot_locked(self, full: bool = False) -> dict:
        from ..config import SNAPSHOT_DIR
        name = f'page_{int(time.time() * 1000)}.png'
        self.page.get_screenshot(path=str(SNAPSHOT_DIR), name=name, full_page=bool(full))
        return {'file': str(SNAPSHOT_DIR / name), 'name': name, 'full': bool(full)}


def is_view_stream_enabled() -> bool:
    return bool(settings.stream_enabled)


def _rect_close(a: list, b: list, tol: float = 1.5) -> bool:
    """两个视口矩形是否几乎重合（用于几何一致性判定）。"""
    if not a or not b:
        return False
    return all(abs(float(a[i]) - float(b[i])) <= tol for i in range(min(4, len(a), len(b))))



session = BrowserSession()
