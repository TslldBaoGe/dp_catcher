'use strict';

const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');

const SMOKE = process.env.DPC_SMOKE_TEST === '1';
const ICON = path.join(__dirname, 'icon.ico');

let mainWindow = null;
let splashWindow = null;
let backend = null;
let backendInfo = null;
let backendReady = false;
let quitting = false;
let starting = false;
let stderrTail = [];

function focusMain() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}

function pickPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

function resolveBackend() {
  if (process.env.DPC_BACKEND_EXE) {
    return { exe: process.env.DPC_BACKEND_EXE, args: [], python: false };
  }
  if (app.isPackaged) {
    return { exe: path.join(process.resourcesPath, 'backend', 'DPCBackend.exe'),
             args: [], python: false };
  }
  const built = path.join(__dirname, '..', '..', 'build', 'backend', 'DPCBackend', 'DPCBackend.exe');
  if (fs.existsSync(built)) {
    return { exe: built, args: [], python: false };
  }
  return {
    exe: process.env.DPC_PYTHON || 'python',
    args: [path.join(__dirname, '..', 'backend_main.py')],
    python: true,
  };
}

function appendLog(chunk) {
  stderrTail.push(chunk.toString('utf8'));
  if (stderrTail.length > 60) stderrTail.shift();
}

async function waitForBackend(base, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`后端进程提前退出（代码 ${backend.exitCode}）\n${stderrTail.join('').slice(-2000)}`);
    }
    try {
      const res = await fetch(base + '/api/settings', { signal: AbortSignal.timeout(1500) });
      if (res.ok) return await res.json();
    } catch {
      // 后端尚未监听，继续等待
    }
    await new Promise(r => setTimeout(r, 300));
  }
  throw new Error(`后端启动超时（${Math.round(timeoutMs / 1000)} 秒）\n${stderrTail.join('').slice(-2000)}`);
}

async function startBackend() {
  const port = await pickPort();
  const token = crypto.randomBytes(24).toString('hex');
  const target = resolveBackend();
  if (!target.python && !fs.existsSync(target.exe)) {
    throw new Error('未找到后端程序：' + target.exe +
      '\n\n请先运行 build_client.bat 构建，或设置环境变量 DPC_BACKEND_EXE 指向 DPCBackend.exe。');
  }

  const dataDir = path.join(app.getPath('userData'), 'data');
  const logDir = path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });
  const logFile = path.join(logDir, 'backend.log');

  const args = [
    ...(target.python ? ['-u'] : []),
    ...target.args,
    '--port', String(port),
    '--data-dir', dataDir,
    '--log-file', logFile,
    '--parent-pid', String(process.pid),
    '--token', token,
  ];

  stderrTail = [];
  const child = spawn(target.exe, args, {
    cwd: target.python ? path.join(__dirname, '..', '..') : path.dirname(target.exe),
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', appendLog);
  child.stderr.on('data', appendLog);
  child.on('exit', (code) => {
    backend = null;
    if (quitting || !backendReady) return;
    backendReady = false;
    const detail = stderrTail.join('').slice(-2000);
    try {
      dialog.showErrorBox('后端已停止',
        `后端进程意外退出（代码 ${code}）。\n\n${detail}\n\n日志：${logFile}`);
    } catch {}
    app.quit();
  });
  backend = child;
  backendInfo = { port, token, base: `http://127.0.0.1:${port}`, dataDir, logDir, logFile };

  const settings = await waitForBackend(backendInfo.base, 90000);
  backendReady = true;
  return settings;
}

function forceKill(child) {
  if (!child || child.exitCode !== null) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
    } else {
      child.kill('SIGKILL');
    }
  } catch {}
}

async function shutdownBackend() {
  const info = backendInfo;
  const child = backend;
  backend = null;
  backendReady = false;
  if (!child) return;
  if (info) {
    try {
      await fetch(info.base + '/api/desktop/shutdown', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: info.token }),
        signal: AbortSignal.timeout(2500),
      });
    } catch {}
  }
  if (child.exitCode === null) {
    await new Promise(r => setTimeout(r, 600));
    if (child.exitCode === null) forceKill(child);
  }
}

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 460,
    height: 300,
    frame: false,
    resizable: false,
    show: false,
    backgroundColor: '#0f172a',
    icon: ICON,
    title: 'DrissionPage 元素捕获器',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.once('ready-to-show', () => splashWindow && splashWindow.show());
  splashWindow.on('closed', () => { splashWindow = null; });
}

function createMain() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 1040,
    minHeight: 680,
    show: false,
    backgroundColor: '#0a1020',
    icon: ICON,
    title: 'DrissionPage 元素捕获器',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadURL(backendInfo.base + '/');
  mainWindow.once('ready-to-show', () => {
    if (splashWindow) splashWindow.close();
    mainWindow.show();
    mainWindow.focus();
  });
  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (backendInfo && url.startsWith(backendInfo.base)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (backendInfo && !url.startsWith(backendInfo.base)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  if (SMOKE) {
    mainWindow.webContents.once('did-finish-load', () => {
      setTimeout(async () => {
        console.log('SMOKE_OK ' + backendInfo.base);
        await shutdownBackend();
        app.exit(0);
      }, 2500);
    });
  }
}

function showStartupError(message) {
  if (!splashWindow) createSplash();
  splashWindow.loadFile(path.join(__dirname, 'error.html'),
    { query: { message: String(message || '未知错误').slice(0, 4000) } });
}

function showAbout() {
  const info = backendInfo;
  dialog.showMessageBox({
    type: 'info',
    title: '关于',
    message: 'DrissionPage 元素捕获器',
    detail: [
      `版本 ${app.getVersion()}`,
      `后端 ${info ? info.base : '未启动'}`,
      `数据目录 ${info ? info.dataDir : app.getPath('userData')}`,
      '',
      'Electron + FastAPI + DrissionPage',
    ].join('\n'),
  });
  if (mainWindow) mainWindow.focus();
}

function buildMenu() {
  const template = [
    {
      label: '文件',
      submenu: [
        { label: '打开日志目录', click: () => shell.openPath(backendInfo ? backendInfo.logDir : app.getPath('userData')) },
        { label: '打开数据目录', click: () => shell.openPath(backendInfo ? backendInfo.dataDir : app.getPath('userData')) },
        { type: 'separator' },
        { label: '退出', role: 'quit' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload', label: '重新加载' },
        { role: 'forceReload', label: '强制刷新' },
        { role: 'toggleDevTools', label: '开发者工具' },
        { type: 'separator' },
        { role: 'resetZoom', label: '实际大小' },
        { role: 'zoomIn', label: '放大' },
        { role: 'zoomOut', label: '缩小' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: '全屏' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        { label: '关于', click: showAbout },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function boot() {
  if (starting) return;
  starting = true;
  try {
    await startBackend();
    if (!mainWindow) createMain();
  } catch (err) {
    const msg = (err && err.message) || String(err);
    if (backend) {
      forceKill(backend);
      backend = null;
    }
    backendReady = false;
    showStartupError(msg);
  } finally {
    starting = false;
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', focusMain);

  ipcMain.on('desktop:retry', () => {
    if (mainWindow || starting) return;
    if (splashWindow) splashWindow.loadFile(path.join(__dirname, 'splash.html'));
    boot();
  });

  ipcMain.on('desktop:open-logs', () => {
    const dir = backendInfo ? backendInfo.logDir : path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(dir, { recursive: true });
    shell.openPath(dir);
  });

  app.whenReady().then(() => {
    app.setAppUserModelId('com.tslld.drissionpage.catcher');
    Menu.setApplicationMenu(null);
    createSplash();
    boot();
  });

  app.on('activate', () => {
    if (!mainWindow && backendReady) createMain();
  });

  app.on('window-all-closed', () => {
    app.quit();
  });

  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    shutdownBackend().finally(() => {
      process.exitCode = 0;
      app.exit(0);
    });
  });
}
