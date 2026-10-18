# -*- coding: utf-8 -*-
"""控制台前端冒烟检查（人工排查用，不参与 unittest 自动发现）。

做三件事：
  1. 起一个真实服务 + 受控浏览器；
  2. 用另一个浏览器打开控制台，捕获 JS 报错、检查镜像帧是否到达；
  3. 在镜像画面上模拟一次点击，验证「捕获 → 候选 → 代码」链路，并截图存到 snapshots/。

用法：python tests/smoke_dashboard.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DrissionPage import ChromiumOptions, ChromiumPage  # noqa: E402

from app.config import DEMO_PAGE, PROJECT_DIR, SNAPSHOT_DIR  # noqa: E402

PORT = int(os.environ.get('DPC_SMOKE_PORT', 8793))
DEBUG_PORT = int(os.environ.get('DPC_SMOKE_DEBUG_PORT', 9367))
VIEWER_PORT = int(os.environ.get('DPC_SMOKE_VIEWER_PORT', 9368))
BASE = 'http://127.0.0.1:%d' % PORT
DEMO_URL = 'file:///' + str(DEMO_PAGE).replace(os.sep, '/')

ERROR_HOOK = '''
window.__errors = [];
window.addEventListener('error', function (e) {
  window.__errors.push('error: ' + (e.message || e.error));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__errors.push('rejection: ' + (e.reason && e.reason.message ? e.reason.message : e.reason));
});
(function () {
  var ce = console.error;
  console.error = function () {
    window.__errors.push('console.error: ' + Array.prototype.join.call(arguments, ' '));
    ce.apply(console, arguments);
  };
})();
'''


def kill_browser_of_port(port: int) -> None:
    """只杀使用本工具临时 profile 的 Chromium，绝不碰用户自己的浏览器。"""
    marker = r'DrissionPage\userData\%d' % int(port)
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*" + marker + "*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', script],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def wait_ready(timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + '/api/status', timeout=8) as r:
                if json.loads(r.read().decode('utf-8')).get('running'):
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def main() -> int:
    env = dict(os.environ)
    env.update({'DPC_PORT': str(PORT), 'DPC_DEBUG_PORT': str(DEBUG_PORT),
                'DPC_START_URL': DEMO_URL, 'DPC_AUTO_START': '1',
                'PYTHONUTF8': '1'})
    kill_browser_of_port(DEBUG_PORT)   # 清掉上次残留，保证本次环境干净
    kill_browser_of_port(VIEWER_PORT)
    srv = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1',
         '--port', str(PORT), '--log-level', 'warning'],
        cwd=PROJECT_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    viewer = None
    try:
        if not wait_ready():
            print('[FAIL] 服务未就绪')
            print(srv.stdout.read().decode('utf-8', 'replace')[-2000:])
            return 1
        print('[OK] 服务与受控浏览器已就绪')

        co = ChromiumOptions()
        co.set_local_port(VIEWER_PORT)
        co.set_argument('--window-size=1560,1000')
        viewer = ChromiumPage(co)
        viewer.add_init_js(ERROR_HOOK)
        viewer.get(BASE + '/')
        time.sleep(6.0)

        errs = viewer.run_js('return window.__errors || [];') or []
        print('[%s] JS 报错数量: %d' % ('OK' if not errs else 'FAIL', len(errs)))
        for e in errs[:10]:
            print('    -', e)

        got_frame = viewer.run_js(
            "var s=document.getElementById('screen');"
            "return !!(s && s.src && s.src.indexOf('data:image/jpeg') === 0);")
        print('[%s] 镜像帧已到达' % ('OK' if got_frame else 'FAIL'))

        status_txt = viewer.run_js("return document.getElementById('statusText').textContent;")
        print('[INFO] 状态栏:', status_txt)

        # 在镜像画面上模拟点击（走前端真实事件处理链路）
        clicked = viewer.run_js('''
        var img = document.getElementById('screen');
        var r = img.getBoundingClientRect();
        // 演示页「用户名」输入框大致位置：表单区第一行
        var ev = new MouseEvent('click', {
          clientX: r.left + r.width * 0.22,
          clientY: r.top + r.height * 0.24,
          bubbles: true, cancelable: true, view: window
        });
        img.dispatchEvent(ev);
        return [Math.round(r.width), Math.round(r.height)];
        ''')
        print('[INFO] 镜像显示尺寸:', clicked)
        time.sleep(4.5)

        el_tag = viewer.run_js("return document.getElementById('elTag').textContent;")
        cand_count = viewer.run_js("return document.querySelectorAll('#candList .cand').length;")
        code_len = viewer.run_js("return (document.getElementById('codeBox').textContent||'').length;")
        spec = viewer.run_js("return document.getElementById('customSpec').value;")
        print('[%s] 捕获结果 elTag=%r 候选数=%s 代码长度=%s'
              % ('OK' if el_tag and el_tag not in ('', '-') else 'FAIL',
                 el_tag, cand_count, code_len))

        # 切换一次候选，验证「应用表达式」链路
        viewer.run_js("var c=document.querySelector('#candList .cand'); if(c) c.click();")
        time.sleep(2.5)
        code_len2 = viewer.run_js("return (document.getElementById('codeBox').textContent||'').length;")
        print('[%s] 点击候选后代码已刷新（%s → %s）'
              % ('OK' if code_len2 > 0 else 'FAIL', code_len, code_len2))

        # 缩放按钮
        viewer.run_js("document.getElementById('zoomInBtn').click();")
        time.sleep(0.6)
        z1 = viewer.run_js("return document.getElementById('zoomInfo').textContent;")
        viewer.run_js("document.getElementById('zoomFitBtn').click();")
        time.sleep(0.6)
        z2 = viewer.run_js("return document.getElementById('zoomInfo').textContent;")
        print('[OK] 缩放：放大→%s，适应→%s' % (z1, z2))

        # 跑一个测试动作
        viewer.run_js("var b=document.querySelector('.act[data-action=get_text]'); if(b) b.click();")
        time.sleep(2.5)
        result = viewer.run_js("return (document.getElementById('testResult').textContent||'').slice(0,120);")
        print('[INFO] 测试动作结果:', result)

        errs2 = viewer.run_js('return window.__errors || [];') or []
        if len(errs2) > len(errs):
            print('[FAIL] 交互过程中新增报错:')
            for e in errs2[len(errs):]:
                print('    -', e)

        shot = SNAPSHOT_DIR / 'dashboard_smoke.png'
        try:
            viewer.get_screenshot(path=str(SNAPSHOT_DIR), name=shot.name)
            print('[OK] 截图已保存:', shot)
        except Exception as exc:
            print('[WARN] 截图失败（页面动画繁忙时偶发，不影响结论）:', exc)

        ok = (not errs2) and got_frame and el_tag not in ('', '-') and cand_count > 0
        print('\n结论:', '前端链路正常' if ok else '存在问题，请查看上面输出')
        return 0 if ok else 1
    finally:
        try:
            if viewer is not None:
                viewer.quit()
        except Exception:
            pass
        try:
            srv.terminate()
            srv.wait(timeout=15)
        except Exception:
            try:
                srv.kill()
            except Exception:
                pass
        # 服务被强杀时受控浏览器可能变成孤儿进程，这里兜底清理
        kill_browser_of_port(DEBUG_PORT)
        kill_browser_of_port(VIEWER_PORT)


if __name__ == '__main__':
    sys.exit(main())
