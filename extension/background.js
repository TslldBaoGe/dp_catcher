'use strict';

/* 后台 service worker。
 *
 * 重要发现（Chrome 137+）：通过 CDP Extensions.loadUnpacked / 命令行加载的
 * 未打包扩展，manifest 里声明的 content_scripts 可能不会自动注入。
 * 因此这里改为「点击工具栏图标时主动用 chrome.scripting 注入」，
 * 既不依赖注入时机，也能覆盖扩展安装前就已打开的标签页。
 */

async function injectInto(tabId) {
  // 1) picker：所有 frame 都要有，这样 iframe 内也能拾取
  try {
    // world: MAIN 必须指定 —— 默认的 isolated world 里 window 与页面隔离，
    // 页面主世界中看不到 __DPC__，拾取器的高亮/拦截事件也就无从生效。
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ['picker.js'],
      world: 'MAIN',
    });
  } catch (e) {
    console.warn('[DPC] 注入 picker 失败', e);
    return false;
  }
  return true;
}

async function ensurePanel(tabId) {
  // 2) 面板脚本只注入顶层框架（面板本身是 UI，不需要进 iframe）
  try {
    const probe = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => !!(window.__DPC_PANEL__),
    });
    if (probe && probe[0] && probe[0].result) return true;
    // panel.js 依赖 chrome.runtime（仅 isolated world 可用）；
    // 它监听 window 上的 __dpc_pick__ 事件拿到 MAIN world 里的拾取结果。
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['panel.js'],
    });
    return true;
  } catch (e) {
    console.warn('[DPC] 注入面板失败', e);
    return false;
  }
}

async function openPanel(tab) {
  if (!tab || !tab.id) return;
  const url = tab.url || '';
  if (/^(chrome|edge|about|devtools|chrome-extension):/i.test(url)) {
    // 浏览器内部页面不允许注入，给用户一个明确提示
    await setBadge('!', '#ef4444');
    console.warn('[DPC] 该页面不支持注入:', url);
    return;
  }
  const okPicker = await injectInto(tab.id);
  if (!okPicker) {
    await setBadge('!', '#ef4444');
    return;
  }
  const okPanel = await ensurePanel(tab.id);
  if (!okPanel) {
    await setBadge('!', '#ef4444');
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { cmd: 'toggle-panel' });
    await setBadge('', null);
  } catch (e) {
    console.warn('[DPC] 打开面板失败', e);
  }
}

async function setBadge(text, color) {
  try {
    await chrome.action.setBadgeText({ text: text || '' });
    if (color) await chrome.action.setBadgeBackgroundColor({ color });
  } catch (e) { /* ignore */ }
}

chrome.action.onClicked.addListener(openPanel);

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== 'toggle-pick') return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;
  if (!(await injectInto(tab.id))) {
    await setBadge('!', '#ef4444');
    return;
  }
  if (!(await ensurePanel(tab.id))) {
    await setBadge('!', '#ef4444');
    return;
  }
  try {
    // 面板侧的 toggle-pick 会：面板未显示则先显示，再切换拾取模式。
    // 这样「第一次按 Alt+P」也能立刻看到界面。
    const res = await chrome.tabs.sendMessage(tab.id, { cmd: 'toggle-pick' });
    await chrome.storage.local.set({ pickMode: !!(res && res.on) });
    await setBadge('', null);
  } catch (e) {
    console.warn('[DPC] 切换拾取失败', e);
  }
});

/* 面板请求持久化（输入文本、捕获列表等） */
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.cmd) return;

  if (msg.cmd === 'panel:save') {
    chrome.storage.local.set(msg.data || {}).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.cmd === 'panel:load') {
    chrome.storage.local.get(null).then((all) => sendResponse({ ok: true, data: all }));
    return true;
  }
  if (msg.cmd === 'notify') {
    // 面板里 toast 走页面 DOM，这里只做日志
    console.log('[DPC]', msg.text || '');
    sendResponse({ ok: true });
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(['installed']).then((v) => {
    if (!v.installed) {
      chrome.storage.local.set({ installed: Date.now() });
    }
  });
});
