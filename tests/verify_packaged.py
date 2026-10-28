# -*- coding: utf-8 -*-
"""打包产物端到端验证：直接驱动 dist_client 里的 DPCBackend.exe，
把「启动 → 拾取 → 校验 → 评级 → 用法 → 动作」整条链路走一遍。

用法：python tests/verify_packaged.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
EXE = PROJECT / 'dist_client' / 'win-unpacked' / 'resources' / 'backend' / 'DPCBackend.exe'
PORT = int(os.environ.get('DPC_PACK_TEST_PORT', '8795'))
DEBUG_PORT = int(os.environ.get('DPC_PACK_TEST_DEBUG_PORT', '9395'))
BASE = 'http://127.0.0.1:%d' % PORT

failed = []


def check(name: str, ok: bool, detail: str = '') -> None:
    print('  [%s] %s%s' % ('OK' if ok else 'FAIL', name, ('  → ' + detail) if detail else ''))
    if not ok:
        failed.append(name)


def http(path: str, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        method='POST' if data else 'GET',
        headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {'httpStatus': e.code, 'body': e.read().decode('utf-8')[:200]}


def kill_procs(names) -> None:
    for name in names:
        try:
            subprocess.run(['taskkill', '/F', '/IM', name], capture_output=True, timeout=20)
        except Exception:
            pass


def kill_port_browser(port: int) -> None:
    marker = 'DrissionPage' + os.sep + 'userData' + os.sep + str(port)
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*" + marker + "*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', script],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def main() -> int:
    if not EXE.exists():
        print('未找到打包后的后端：%s' % EXE)
        print('请先运行 build_client.bat 或 python tests/verify_packaged.py 前先构建。')
        return 2

    print('验证目标：%s' % EXE)
    print('体积：%.0f MB' % (EXE.stat().st_size / 1024 / 1024))
    print()

    kill_procs(['DPCBackend.exe'])
    kill_port_browser(DEBUG_PORT)
    time.sleep(1)

    data_dir = Path(tempfile.mkdtemp(prefix='dpc_pack_'))
    log_file = data_dir / 'backend.log'
    proc = subprocess.Popen(
        [str(EXE), '--port', str(PORT), '--data-dir', str(data_dir),
         '--log-file', str(log_file), '--debug-port', str(DEBUG_PORT)],
        cwd=str(EXE.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    try:
        # 1) 启动就绪
        ready = False
        for _ in range(60):
            try:
                st = http('/api/status', timeout=8)
                if st.get('running'):
                    ready = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                print('后端进程提前退出，代码 %s' % proc.returncode)
                if log_file.exists():
                    print(log_file.read_text(encoding='utf-8', errors='replace')[-2000:])
                return 1
            time.sleep(1)
        check('后端启动并接管浏览器', ready)
        if not ready:
            return 1

        # 2) 打包模式与路径
        cfg = http('/api/settings')
        check('识别为打包模式（frozen）', cfg.get('frozen') is True, 'frozen=%s' % cfg.get('frozen'))
        check('数据目录可写', os.path.isdir(cfg.get('data_dir') or ''), cfg.get('data_dir'))
        demo = cfg.get('demo_page') or ''
        check('内置测试页随包可用', os.path.exists(demo), demo[-60:])
        check('静态资源随包可用', os.path.exists(str(Path(demo).parent.parent / 'app' / 'static' / 'app.js')))

        # 3) 控制台页面
        html = None
        try:
            with urllib.request.urlopen(BASE + '/', timeout=15) as r:
                html = r.read().decode('utf-8')
        except Exception as exc:
            print('  读取首页失败:', exc)
        check('控制台页面可访问', bool(html and '元素捕获器' in html),
              '%d 字节' % len(html or ''))

        # 4) 导航到测试页
        nav = http('/api/navigate', {'url': demo})
        check('导航到测试页', nav.get('ok') is True, (nav.get('error') or '')[:60])

        # 5) 拾取 + 校验 + 评级
        hl = http('/api/highlight', {'spec': '#username'})
        rects = ((hl.get('data') or {}).get('groups') or [{}])[0].get('rects') or []
        check('高亮定位生效', len(rects) == 1, '匹配 %d 个框' % len(rects))
        if not rects:
            return 1
        x = rects[0][0] + rects[0][2] / 2
        y = rects[0][1] + rects[0][3] / 2
        pick = http('/api/pick', {'x': x, 'y': y})
        check('坐标拾取元素', pick.get('ok') is True and (pick.get('desc') or {}).get('id') == 'username',
              (pick.get('error') or '')[:60])

        plan = pick.get('plan') or {}
        overall = plan.get('overall') or {}
        check('定位评级 A 级', overall.get('grade') == 'A',
              '%s %s 分' % (overall.get('grade'), overall.get('score')))
        check('候选清单非空', len(plan.get('candidates') or []) >= 4,
              '%d 条' % len(plan.get('candidates') or []))

        # 6) 用法片段
        us = http('/api/usages', {'spec': pick.get('spec'), 'framePath': [],
                                  'opts': {'text': 'pack_test', 'attrName': 'placeholder'}})
        usages = us.get('usages') or []
        labels = [u.get('label') for u in usages]
        check('用法片段生成', len(usages) >= 8, '%d 条: %s' % (len(usages), ', '.join(labels[:4])))
        check('用法片段含可运行输入项', any(u.get('id') == 'input' and u.get('action') for u in usages))

        # 7) 真实动作
        ins = http('/api/action', {'action': 'input', 'spec': pick.get('spec'),
                                   'framePath': [],
                                   'opts': {'text': 'pack_test', 'clear_first': True}})
        check('输入动作执行', ins.get('ok') is True, (ins.get('error') or '')[:60])
        got = http('/api/action', {'action': 'get_property', 'spec': '#username',
                                   'framePath': [], 'opts': {'name': 'value'}})
        val = (got.get('data') or {}).get('value')
        check('读回输入值', val == 'pack_test', 'value=%r' % val)

        # 8) 捕获列表与导出
        add = http('/api/history/add', {'name': '用户名'})
        check('加入捕获列表', add.get('ok') is True)
        exp = http('/api/export', {'url': demo})
        script = exp.get('script') or ''
        ok_script = False
        try:
            compile(script, 'exported.py', 'exec')
            ok_script = 'pack_test' in script or '用户' in script
        except SyntaxError:
            ok_script = False
        check('导出脚本语法正确', ok_script, '%d 字符' % len(script))

        # 9) 桌面关停接口（未配置令牌时必须拒绝）
        sd = http('/api/desktop/shutdown', {'token': 'x'})
        check('无令牌关停被拒绝', sd.get('httpStatus') == 403, str(sd.get('body', ''))[:60])

        # 10) 状态轮询不阻塞（连续 10 次应很快返回）
        t0 = time.time()
        for _ in range(10):
            http('/api/status', timeout=10)
        dt = time.time() - t0
        check('状态轮询不阻塞', dt < 3.0, '10 次共 %.2fs' % dt)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        kill_port_browser(DEBUG_PORT)
        kill_procs(['DPCBackend.exe'])
        try:
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)
        except Exception:
            pass

    print()
    if failed:
        print('失败项 %d 个：%s' % (len(failed), ', '.join(failed)))
        return 1
    print('全部检查通过：打包产物可独立运行，捕获链路完整。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
