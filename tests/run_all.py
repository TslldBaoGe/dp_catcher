# -*- coding: utf-8 -*-
"""一键跑全部测试。

用法：
    python tests/run_all.py            # 全部（需要能启动 Chromium）
    python tests/run_all.py --quick    # 只跑不需要浏览器的单元测试
    python tests/run_all.py --only e2e # 只跑指定模块（e2e / server / locators / codegen）
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MODULES = {
    'frontend': 'tests.test_frontend',
    'codegen': 'tests.test_codegen',
    'locators': 'tests.test_locators',
    'e2e': 'tests.test_e2e',
    'server': 'tests.test_server_e2e',
}
QUICK_MODULES = ['frontend', 'codegen', 'locators']
BROWSER_MODULES = ['e2e', 'server']


def main() -> int:
    parser = argparse.ArgumentParser(description='运行元素捕获器测试')
    parser.add_argument('--quick', action='store_true', help='只跑纯单元测试（不需要浏览器）')
    parser.add_argument('--only', action='append', choices=sorted(MODULES),
                        help='只跑指定模块，可重复')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    if args.only:
        picked = list(dict.fromkeys(args.only))
    elif args.quick:
        picked = QUICK_MODULES
    else:
        picked = QUICK_MODULES + BROWSER_MODULES

    names = [MODULES[k] for k in picked]
    print('=' * 70)
    print('将运行：%s' % ', '.join(picked))
    if any(k in BROWSER_MODULES for k in picked):
        print('注意：浏览器相关用例会真实启动 Chromium 并占用调试端口 9351 / 9365')
    print('=' * 70)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in names:
        suite.addTests(loader.loadTestsFromName(name))

    t0 = time.time()
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    print('-' * 70)
    print('耗时 %.1fs｜用例 %d｜失败 %d｜错误 %d｜跳过 %d'
          % (time.time() - t0, result.testsRun, len(result.failures),
             len(result.errors), len(result.skipped)))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
