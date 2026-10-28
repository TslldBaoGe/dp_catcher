# -*- coding: utf-8 -*-
"""高难度真实网站压力测试。

挑选在爬虫/自动化圈子里公认难缠的类型：
  * 强反爬：Cloudflare / 验证码 / 指纹检测（Boss直聘、知乎、天眼查）
  * 重前端：React/Vue SPA、无限滚动、虚拟列表（淘宝、京东、B站、掘金）
  * 大量 Shadow DOM / Web Components（Salesforce、部分文档站）
  * iframe 套娃、复杂表单（支付、登录页）
  * 中文字体渲染、超长页面、Canvas 图表

每个站点记录：导航耗时、首帧延迟、能否拾取元素、定位是否唯一、有无报错。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.core import workflow
from app.core.browser import Subscriber, session

settings.debug_port = int(os.environ.get('DPC_HARD_PORT', '9470'))
settings.auto_start = False
settings.window_width, settings.window_height = 1500, 950
settings.load_mode = 'none'

# (名称, URL, 难度标签)
SITES = [
    ('百度', 'https://www.baidu.com', '常规搜索页'),
    ('知乎', 'https://www.zhihu.com', '强反爬/登录墙'),
    ('B站', 'https://www.bilibili.com', 'SPA/大量图片/懒加载'),
    ('掘金', 'https://juejin.cn', 'SPA/无限滚动'),
    ('豆瓣', 'https://www.douban.com', '老式页面/反爬'),
    ('淘宝', 'https://www.taobao.com', '登录墙/反爬'),
    ('京东', 'https://www.jd.com', '重前端/大量图片'),
    ('GitHub', 'https://github.com', 'Shadow DOM 较少但结构复杂'),
    ('StackOverflow', 'https://stackoverflow.com', '长页面/代码块'),
    ('Wikipedia', 'https://zh.wikipedia.org/wiki/DrissionPage', '超长内容页'),
    ('MDN', 'https://developer.mozilla.org/zh-CN/', '现代前端/多语言'),
    ('Salesforce', 'https://www.salesforce.com', 'Web Components 重度使用'),
    ('Angular', 'https://angular.dev', '原生 Shadow DOM 自定义元素'),
    ('TechDocs', 'https://web.dev', 'Web Components/Lighthouse 站点'),
    ('Amazon', 'https://www.amazon.com', '反爬/验证码'),
    ('Example', 'https://example.com', '极简基准'),
]


class Collector(Subscriber):
    def __init__(self):
        super().__init__(None)
        self.lock = threading.Lock()
        self.frames = []

    def push_frame(self, msg):
        with self.lock:
            self.frames.append((time.perf_counter(), msg))

    def take(self):
        with self.lock:
            out = list(self.frames)
            self.frames.clear()
            return out


def probe_site(name, url, tag, col):
    row = {'name': name, 'url': url, 'tag': tag, 'ok': False,
           'nav_ms': None, 'first_frame_ms': None, 'frames_3s': 0,
           'pick': None, 'unique': None, 'error': None}

    col.take()
    t0 = time.perf_counter()
    res = session.navigate(url)
    row['nav_ms'] = round((time.perf_counter() - t0) * 1000)
    if not res.get('ok'):
        row['error'] = (res.get('error') or '')[:90]
        return row

    # 等首帧
    deadline = time.perf_counter() + 12
    first = None
    while time.perf_counter() < deadline:
        got = col.take()
        if got:
            first = got[0][0] - t0
            break
        time.sleep(0.05)
    row['first_frame_ms'] = round(first * 1000) if first else None

    # 再观察 3 秒帧数
    n0 = len(col.take())
    t1 = time.perf_counter()
    time.sleep(3.0)
    row['frames_3s'] = len(col.take())

    # 拾取页面中心偏上位置的元素（多数站点这里是主内容）
    info = session._drain_light() or {}
    iw = float(info.get('iw') or 1400)
    ih = float(info.get('ih') or 800)
    picked = None
    for fx, fy in ((0.5, 0.35), (0.5, 0.5), (0.3, 0.25), (0.7, 0.6), (0.5, 0.7)):
        try:
            p = workflow.pick_workflow(iw * fx, ih * fy)
        except Exception as exc:
            p = {'ok': False, 'error': str(exc)}
        if p.get('ok'):
            picked = p
            break
    if picked:
        d = picked['desc']
        best = (picked.get('plan') or {}).get('recommended') or {}
        row['pick'] = '<%s> %s' % (d.get('tag'), (d.get('text') or d.get('id') or '')[:24])
        row['unique'] = best.get('verdict') in ('unique', 'ok') and best.get('count') == 1
        row['verdict'] = best.get('verdict')
        row['grade'] = best.get('grade')
        row['n_cands'] = len((picked.get('plan') or {}).get('candidates') or [])
    else:
        row['error'] = '拾取失败'
    row['ok'] = picked is not None and row['first_frame_ms'] is not None
    return row


def main():
    print('启动浏览器…')
    r = session.start('about:blank')
    if not r.get('ok'):
        print('启动失败:', r.get('error'))
        return 1
    time.sleep(1.2)

    col = Collector()
    session.subscribe(col)
    time.sleep(0.8)

    rows = []
    for name, url, tag in SITES:
        print('  测试 %-14s %s …' % (name, url), end='', flush=True)
        try:
            row = probe_site(name, url, tag, col)
        except Exception as exc:
            row = {'name': name, 'url': url, 'tag': tag, 'ok': False,
                   'error': '%s: %s' % (type(exc).__name__, str(exc)[:70])}
        rows.append(row)
        status = 'OK ' if row.get('ok') else 'ERR'
        print(' %s nav=%sms 首帧=%sms 帧=%d' % (
            status, row.get('nav_ms'), row.get('first_frame_ms'), row.get('frames_3s', 0)))
        time.sleep(0.5)

    session.unsubscribe(col)
    session.stop(True)

    print()
    print('=' * 108)
    print('%-14s %-9s %-11s %-8s %-8s %-6s %s' %
          ('站点', '导航ms', '首帧ms', '3s帧数', '定位', '等级', '拾取到的元素'))
    print('-' * 108)
    for r in rows:
        nav = str(r.get('nav_ms') or '-')
        ff = str(r.get('first_frame_ms') or '-')
        fr = str(r.get('frames_3s', 0))
        verdict = r.get('verdict') or ('-' if r.get('ok') else '失败')
        grade = r.get('grade') or '-'
        pick = r.get('pick') or (r.get('error') or '')[:40]
        print('%-14s %-9s %-11s %-8s %-8s %-6s %s' %
              (r['name'], nav, ff, fr, verdict, grade, pick))

    ok = [r for r in rows if r.get('ok')]
    navs = [r['nav_ms'] for r in ok if r.get('nav_ms')]
    ffs = [r['first_frame_ms'] for r in ok if r.get('first_frame_ms')]
    print('-' * 108)
    print('成功 %d/%d' % (len(ok), len(rows)))
    if navs:
        print('导航耗时  中位 %d ms | 最慢 %d ms' % (statistics.median(navs), max(navs)))
    if ffs:
        print('首帧延迟  中位 %d ms | 最慢 %d ms' % (statistics.median(ffs), max(ffs)))
    unique_n = sum(1 for r in ok if r.get('unique'))
    print('定位唯一命中 %d/%d' % (unique_n, len(ok)))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'snapshots',
                       'hard_sites_report.json')
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print('报告已写入:', os.path.abspath(out))
    except Exception as exc:
        print('写报告失败:', exc)
    return 0


if __name__ == '__main__':
    sys.exit(main())
