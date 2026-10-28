# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成 build/backend/DPCBackend/DPCBackend.exe（文件夹版）。"""
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJECT = os.path.abspath(os.getcwd())

dp_datas, dp_binaries, dp_hidden = collect_all('DrissionPage')

# Miniconda 把 ctypes/压缩/OpenSSL 依赖放在 Library/bin，PyInstaller 默认搜不到
_conda_bin = os.path.join(sys.base_prefix, 'Library', 'bin')
for _pattern in ('ffi*.dll', 'liblzma.dll', 'libbz2.dll', 'zlib.dll',
                 'libssl-*.dll', 'libcrypto-*.dll', 'sqlite3.dll'):
    for _dll in glob.glob(os.path.join(_conda_bin, _pattern)):
        dp_binaries.append((_dll, '.'))

datas = [
    (os.path.join(PROJECT, 'app', 'static'), os.path.join('app', 'static')),
    (os.path.join(PROJECT, 'demo'), 'demo'),
] + dp_datas

hiddenimports = dp_hidden + collect_submodules('uvicorn') + [
    'fastapi',
    'starlette',
    'anyio',
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
]

a = Analysis(
    [os.path.join(PROJECT, 'desktop', 'backend_main.py')],
    pathex=[PROJECT],
    binaries=dp_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'pytest', 'IPython'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DPCBackend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(PROJECT, 'desktop', 'electron', 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DPCBackend',
)
