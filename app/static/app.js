/* DrissionPage 元素捕获器 · 控制台前端
 * 画面来源：后端 WebSocket 推送的整视口 JPEG 帧（设备像素，按 cw/ch 映射坐标）
 * 坐标映射：css = (鼠标 - 图像矩形) / 图像显示宽 * 帧视口宽
 */
'use strict';

const S = {
  ws: null,
  connected: false,
  running: false,
  frame: null,          // 最新帧元数据 {cw, ch, scale, url, title}
  lastFrameAt: 0,       // 最近一次收到帧的时间（状态轮询抖动时避免遮罩闪一下）
  paintSeq: 0,
  ctx: null,
  pickMode: true,
  framePath: [],        // iframe 定位域
  desc: null,
  plan: null,
  spec: null,
  code: null,
  codeTab: 'script',
  actions: [],
  history: [],
  usages: [],
  usageInfo: null,
  usageOpts: {text: '', attrName: 'href'},
  usageTimer: null,
  lastPickToken: 0,
  pendingPick: null,
  wheel: {dx: 0, dy: 0, timer: null},
  fps: {count: 0, t0: Date.now(), value: 0},
  zoom: 1,
  fit: true
};

const $ = (id) => document.getElementById(id);
const el = (sel) => document.querySelector(sel);
const all = (sel) => Array.from(document.querySelectorAll(sel));

/* ------------------------------------------------------------------ 工具 */

function toast(msg, kind = '') {
  const box = document.createElement('div');
  box.className = 'toast ' + kind;
  box.textContent = msg;
  $('toasts').appendChild(box);
  setTimeout(() => {
    box.style.transition = 'opacity .3s';
    box.style.opacity = '0';
    setTimeout(() => box.remove(), 320);
  }, kind === 'danger' ? 5200 : 2800);
}

async function api(path, body) {
  try {
    const res = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body || {})
    });
    if (!res.ok) {
      // 非 2xx 必须显式暴露，否则「请求方法用错」这类问题会被静默吞掉
      let detail = '';
      try { detail = (await res.json()).detail || ''; } catch (e) { detail = ''; }
      const msg = `${path} 请求失败（HTTP ${res.status}）${detail ? '：' + detail : ''}`;
      toast(msg, 'danger');
      return {ok: false, error: msg, httpStatus: res.status};
    }
    return await res.json();
  } catch (err) {
    toast('请求失败：' + err, 'danger');
    return {ok: false, error: String(err)};
  }
}

function prettyUrl(u) {
  if (!u || !/^[a-z]+:\/\//i.test(u)) return u || '';
  try { return decodeURI(u); } catch (e) { return u; }
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const PY_KW = ['from', 'import', 'def', 'return', 'if', 'else', 'elif', 'for', 'while', 'in',
  'not', 'and', 'or', 'try', 'except', 'finally', 'with', 'as', 'class', 'lambda', 'None',
  'True', 'False', 'print', 'raise', 'break', 'continue', 'pass', 'global', 'is'];

function highlightPython(code) {
  const kw = PY_KW.join('|');
  const re = new RegExp(
    '("(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\')' +  // 字符串
    '|(#[^\\n]*)' +                                            // 注释
    '|\\b(\\d+\\.?\\d*)\\b' +                                  // 数字
    '|\\b(' + kw + ')\\b' +                                    // 关键字
    '|\\b([A-Za-z_]\\w*)(?=\\()' +                             // 函数名
    '|^(\\s*)(@\\w+)', 'gm');                                  // 装饰器
  return esc(code).replace(re, (m, str, com, num, word, fn, indent, dec) => {
    if (str !== undefined) return '<span class="tok-str">' + str + '</span>';
    if (com !== undefined) return '<span class="tok-com">' + com + '</span>';
    if (num !== undefined) return '<span class="tok-num">' + num + '</span>';
    if (word !== undefined) return '<span class="tok-kw">' + word + '</span>';
    if (fn !== undefined) return '<span class="tok-fn">' + fn + '</span>';
    if (dec !== undefined) return indent + '<span class="tok-dec">' + dec + '</span>';
    return m;
  });
}

/* ------------------------------------------------------------- WebSocket */

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  S.ws = ws;

  ws.onopen = () => {
    S.connected = true;
    setStatus('on', '已连接');
    refreshStatus();
    setTimeout(() => api('/api/refresh-element', {}).then(p => { if (p && p.ok) applyPick(p); }), 200);
  };
  ws.onclose = () => {
    S.connected = false;
    setStatus('off', '已断开，重连中…');
    setTimeout(connect, 1200);
  };
  ws.onerror = () => {};
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleMessage(msg);
  };
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'hello':
      setRunningFromStatus(msg.status);
      break;
    case 'frame':
      renderFrame(msg);
      break;
    case 'nav':
      if (msg.url) { $('url').value = prettyUrl(msg.url); }
      break;
    case 'page_event':
      handlePageEvent(msg.event);
      break;
    case 'picked':
      S.pendingPick = null;
      if (msg.payload && msg.payload.ok) { applyPick(msg.payload); }
      else if (msg.payload) { toast(msg.payload.error || '捕获失败', 'danger'); }
      break;
    case 'applied':
      if (msg.payload && msg.payload.ok) { applyPick(msg.payload); }
      else if (msg.payload) { toast(msg.payload.error || '校验失败', 'danger'); }
      break;
    case 'error':
      toast(msg.error || '服务端错误', 'danger');
      break;
    case 'preset':
      // 多开控制台时保持画质选择同步
      if (msg.name) { $('presetSel').value = msg.name; }
      break;
    case 'pickmode':
      S.pickMode = !!msg.on;
      $('pickToggle').checked = S.pickMode;
      break;
  }
}

function handlePageEvent(ev) {
  if (!ev) return;
  if (ev.kind === 'pick' && ev.d) {
    // 用户在真实浏览器窗口里点的元素：交给后端复核定位方案
    adoptDescViaApi(ev.d, ev.url);
  } else if (ev.kind === 'pickmode') {
    S.pickMode = !!ev.on;
    $('pickToggle').checked = S.pickMode;
  }
}

async function adoptDescViaApi(desc, url) {
  // 真实窗口拾取到的描述：交给后端复核定位方案
  const res = await api('/api/pick-from-desc', {desc: desc, url: url});
  if (res && res.ok) applyPick(res);
  else if (res) toast(res.error || '复核失败', 'danger');
}

function send(obj) {
  if (S.ws && S.ws.readyState === 1) {
    S.ws.send(JSON.stringify(obj));
    return true;
  }
  toast('与后端的连接已断开', 'danger');
  return false;
}

/* ----------------------------------------------------------------- 画面 */

function renderFrame(msg) {
  const same = !!S.frame && S.frame.cw === msg.cw && S.frame.data === msg.data;
  if (!S.frame || S.frame.cw !== msg.cw) { applyZoom(msg); }
  S.frame = msg;
  S.lastFrameAt = Date.now();
  if (!same) paintFrame(msg);  // 空闲保活帧内容不变时不重绘，省掉无谓的整层重传
  $('placeholder').hidden = true;

  S.fps.count++;
  const now = Date.now();
  if (now - S.fps.t0 >= 1000) {
    S.fps.value = Math.round(S.fps.count * 1000 / (now - S.fps.t0));
    S.fps.count = 0;
    S.fps.t0 = now;
  }
  $('frameInfo').textContent =
    `视口 ${Math.round(msg.cw)}×${Math.round(msg.ch)} · 画面 ${S.fps.value}/秒 · 抓帧 ${msg.grab_ms}ms` +
    (msg.title ? ` · ${msg.title}` : '');
}

/* 画布双缓冲：先解码再整体绘制，避免换帧瞬间露出元素背景色（白闪）。 */
function paintFrame(msg) {
  const seq = ++S.paintSeq;
  const im = new Image();
  im.src = 'data:image/jpeg;base64,' + msg.data;
  im.decode().then(() => {
    if (seq !== S.paintSeq) return;  // 过期帧直接丢弃，避免旧帧覆盖新帧
    const canvas = $('screen');
    if (!canvas) return;
    if (canvas.width !== im.naturalWidth || canvas.height !== im.naturalHeight) {
      canvas.width = im.naturalWidth;
      canvas.height = im.naturalHeight;
    }
    const ctx = S.ctx || (S.ctx = canvas.getContext('2d', {alpha: false}));
    ctx.drawImage(im, 0, 0, canvas.width, canvas.height);
  }).catch(() => {});
}

/* 缩放：1 = 1 个页面 CSS 像素对应 1 个屏幕像素 */
function applyZoom(frame) {
  const img = $('screen');
  const f = frame || S.frame;
  if (!f) return;
  if (S.fit) {
    const avail = Math.max(240, $('viewport').clientWidth - 26);
    img.style.width = Math.round(avail) + 'px';
  } else {
    img.style.width = Math.round(f.cw * S.zoom) + 'px';
  }
  img.style.aspectRatio = f.cw + ' / ' + f.ch;
  img.classList.add('zoomed');
  const shown = img.getBoundingClientRect().width || 1;
  $('zoomInfo').textContent = Math.round(shown / f.cw * 100) + '%';
  if (S.plan) renderHighlight(S.plan.highlight);
}

function zoomBy(factor) {
  if (!S.frame) return;
  const img = $('screen');
  S.zoom = Math.max(0.15, Math.min(3, (img.getBoundingClientRect().width || S.frame.cw) / S.frame.cw * factor));
  S.fit = false;
  applyZoom();
}

function viewportRect() {
  const r = $('screen').getBoundingClientRect();
  return r;
}

function toCss(clientX, clientY) {
  if (!S.frame) return null;
  const r = viewportRect();
  const k = S.frame.cw / r.width;
  return [(clientX - r.left) * k, (clientY - r.top) * k];
}

function renderHighlight(highlight) {
  const box = $('overlay');
  const r = viewportRect();
  box.style.left = r.left + 'px';
  box.style.top = r.top + 'px';
  box.style.width = r.width + 'px';
  box.style.height = r.height + 'px';
  box.innerHTML = '';
  if (!highlight || !S.frame) return;

  const cw = S.frame.cw, ch = S.frame.ch;
  const groups = highlight.groups || [];
  const px = (v, total) => (v / total * 100) + '%';

  groups.forEach((g, gi) => {
    (g.rects || []).forEach((rect, i) => {
      const d = document.createElement('div');
      const primary = gi === 0 && i === 0;
      d.className = 'hbox ' + (primary ? 'primary' : (gi === 0 ? 'other' : 'frame'));
      d.style.left = px(rect[0], cw);
      d.style.top = px(rect[1], ch);
      d.style.width = px(rect[2], cw);
      d.style.height = px(rect[3], ch);
      if ((g.rects || []).length > 1) {
        const n = document.createElement('span');
        n.className = 'num';
        n.textContent = (gi === 0 ? (i + 1) : 'Δ');
        d.appendChild(n);
      }
      box.appendChild(d);
    });
  });
}

function drawDescBox() {
  const box = $('overlay');
  if (!S.desc || !S.frame) return;
  const r = viewportRect();
  box.style.left = r.left + 'px';
  box.style.top = r.top + 'px';
  box.style.width = r.width + 'px';
  box.style.height = r.height + 'px';
  const rect = S.desc.rect;
  if (!rect) return;
  const d = document.createElement('div');
  d.className = 'hbox primary';
  d.style.left = (rect[0] / S.frame.cw * 100) + '%';
  d.style.top = (rect[1] / S.frame.ch * 100) + '%';
  d.style.width = (rect[2] / S.frame.cw * 100) + '%';
  d.style.height = (rect[3] / S.frame.ch * 100) + '%';
  box.appendChild(d);
}

/* ------------------------------------------------------------- 交互事件 */

function bindViewport() {
  const view = $('viewport');
  const img = $('screen');
  img.addEventListener('dragstart', e => e.preventDefault());

  img.addEventListener('mousemove', (e) => {
    const p = toCss(e.clientX, e.clientY);
    if (!p) return;
    $('coordInfo').textContent = `x: ${Math.round(p[0])}  y: ${Math.round(p[1])}`;
    const ch = $('crosshair');
    ch.hidden = false;
    ch.style.left = e.clientX + 'px';
    ch.style.top = e.clientY + 'px';
  });

  img.addEventListener('mouseleave', () => { $('crosshair').hidden = true; });

  img.addEventListener('click', (e) => {
    const p = toCss(e.clientX, e.clientY);
    if (!p) return;
    if (S.pickMode) {
      pickXY(p[0], p[1]);
    } else {
      api('/api/click-at', {x: p[0], y: p[1], count: 1, button: 'left'})
        .then(r => { if (!r.ok) toast(r.error, 'danger'); });
    }
  });

  img.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const p = toCss(e.clientX, e.clientY);
    if (!p) return;
    if (S.pickMode) return;
    api('/api/click-at', {x: p[0], y: p[1], button: 'right'})
      .then(r => { if (!r.ok) toast(r.error, 'danger'); });
  });

  img.addEventListener('dblclick', (e) => {
    if (S.pickMode) return;
    const p = toCss(e.clientX, e.clientY);
    if (!p) return;
    api('/api/click-at', {x: p[0], y: p[1], count: 2});
  });

  view.addEventListener('wheel', (e) => {
    e.preventDefault();
    S.wheel.dx += e.deltaX;
    S.wheel.dy += e.deltaY;
    if (S.wheel.timer) return;
    S.wheel.timer = setTimeout(() => {
      const p = toCss(e.clientX, e.clientY) || [400, 300];
      api('/api/scroll', {dx: S.wheel.dx, dy: S.wheel.dy, x: p[0], y: p[1]});
      S.wheel = {dx: 0, dy: 0, timer: null};
    }, 60);
  }, {passive: false});

  window.addEventListener('resize', () => {
    applyZoom();
    if (S.plan) renderHighlight(S.plan.highlight);
  });
}

function pickXY(x, y) {
  S.lastPickToken++;
  send({cmd: 'pick', x: x, y: y, framePath: S.framePath, token: S.lastPickToken});
  S.pendingPick = S.lastPickToken;
}

function applyPick(payload) {
  S.desc = payload.desc;
  S.plan = payload.plan;
  S.spec = payload.spec;
  S.actions = payload.actions || [];
  S.code = payload.code;
  renderElement();
  renderCandidates();
  renderCode();
  renderRecorded();
  renderUsages();
  loadUsages();
  if (S.plan && S.plan.highlight) renderHighlight(S.plan.highlight);
  if (S.desc && S.desc.url) $('url').value = prettyUrl(S.desc.url);
  applyZoom();
  renderScopeBar();
}

/* ------------------------------------------------------------- 元素面板 */

const STATE_LABEL = {
  is_displayed: '可见', is_enabled: '可用', is_checked: '已勾选', is_selected: '已选中',
  is_alive: '在线', is_in_viewport: '在视口内', is_whole_in_viewport: '完整可见', is_covered: '被遮挡'
};

function renderElement() {
  const d = S.desc;
  $('elementEmpty').hidden = !!d;
  $('elementBody').hidden = !d;
  if (!d) return;

  const name = d.tag + (d.id ? '#' + d.id : '') +
    ((d.cls || []).length ? '.' + d.cls.slice(0, 3).join('.') : '');
  $('elTag').textContent = name;

  const best = (S.plan && S.plan.recommended) || null;
  const overall = (S.plan && S.plan.overall) || null;
  const v = $('elVerdict');
  if (best) {
    v.textContent = best.verdictLabel + '（' + best.count + ' 个匹配）';
    v.className = 'badge ' + best.level;
  } else {
    v.textContent = '无可用定位';
    v.className = 'badge danger';
  }
  const grade = $('elGrade');
  if (overall) {
    grade.hidden = false;
    grade.textContent = overall.grade + ' · ' + overall.score;
    grade.className = 'grade-chip grade-' + overall.grade;
    grade.title = overall.gradeLabel + '：' + overall.gradeNote + '（点击查看用法与评分明细）';
    grade.onclick = () => {
      const tab = document.querySelector('.tab[data-tab="usage"]');
      if (tab) tab.click();
    };
  } else {
    grade.hidden = true;
    grade.onclick = null;
  }

  const rows = [
    ['标签', d.tag],
    ['id', d.id || '—'],
    ['class', (d.cls || []).join(' ') || '—'],
    ['文本', d.text || '—'],
    ['value', d.value === '' ? '—' : d.value],
    ['位置', d.rect ? `${Math.round(d.rect[0])}, ${Math.round(d.rect[1])}  ${Math.round(d.rect[2])}×${Math.round(d.rect[3])}` : '—'],
    ['稳定性', overall ? `${overall.grade}（${overall.score}/100）· ${overall.gradeLabel}` : '—'],
    ['可输入', d.editable ? '是' : '否'],
    ['所在域', (S.framePath.length ? S.framePath.join(' › ') : '主文档') + (d.inShadow ? ' · Shadow DOM' : '')],
    ['css_path', d.cssPath || '—'],
    ['xpath', d.xpath || '—'],
    ['页面', (d.title || '') + '  ' + (d.url || '')]
  ];
  $('elTable').innerHTML = rows.map(([k, val]) =>
    `<tr><td>${esc(k)}</td><td>${esc(val)}</td></tr>`).join('');

  const attrs = d.attrs || {};
  const keys = Object.keys(attrs);
  $('attrCount').textContent = keys.length ? `(${keys.length})` : '';
  $('attrTable').innerHTML = keys.length
    ? keys.map(k => `<tr><td>${esc(k)}</td><td>${esc(attrs[k])}</td></tr>`).join('')
    : '<tr><td colspan="2" class="muted">无属性</td></tr>';

  const chain = d.chain || [];
  $('chainBox').innerHTML = chain.length
    ? chain.map((c, i) => {
        const label = c.tag + (c.id ? '#' + c.id : '') +
          ((c.cls || []).length ? '.' + c.cls.slice(0, 3).join('.') : '');
        const cand = (c.cands || [])[0];
        return `<div class="link" data-depth="${i + 1}">
          <span class="t">${esc(label)}</span>
          <span class="muted">${esc(c.text || '')}</span>
          <span class="spacer"></span>
          <span class="muted">${esc(cand ? cand.loc : '')}</span></div>`;
      }).join('')
    : '<div class="muted">已是顶层元素</div>';

  $('chainBox').querySelectorAll('.link').forEach(node => {
    node.addEventListener('click', () => chainTo(parseInt(node.dataset.depth, 10)));
  });

  $('intoFrameBtn').hidden = !d.iframe;
  $('intoFrameBtn').onclick = () => {
    if (!S.spec) { toast('该框架还没有可用定位表达式', 'warn'); return; }
    let loc = S.spec;
    if (Array.isArray(loc)) loc = loc[0];
    S.framePath = S.framePath.concat([loc]);
    renderScopeBar();
    toast('已进入框架：' + loc + '，现在捕获的就是框架内元素');
  };
}

function renderScopeBar() {
  const bar = $('scopeBar');
  const items = ['主文档'].concat(S.framePath);
  bar.innerHTML = '<span class="scope-label">定位域</span>' + items.map((t, i) =>
    `<span class="scope-item ${i === items.length - 1 ? 'active' : ''}" data-depth="${i}">${esc(t)}</span>`
  ).join('<span class="muted">›</span>');
  bar.querySelectorAll('.scope-item').forEach(node => {
    node.addEventListener('click', () => {
      const depth = parseInt(node.dataset.depth, 10);
      S.framePath = S.framePath.slice(0, depth);
      renderScopeBar();
      api('/api/clear-highlight', {});
      $('overlay').innerHTML = '';
    });
  });
}

async function chainTo(depth) {
  const res = await api('/api/chain', {depth: depth, framePath: S.framePath});
  if (res && res.ok) applyPick(res);
  else if (res) toast(res.error || '切换失败', 'danger');
}

/* ------------------------------------------------------------- 候选面板 */

function renderCandidates() {
  const list = $('candList');
  const plan = S.plan;
  if (!plan || !plan.ok || !plan.candidates || !plan.candidates.length) {
    list.innerHTML = '<div class="empty">没有可用的定位方案</div>';
    return;
  }
  const current = S.spec;
  list.innerHTML = plan.candidates.map((c, i) => {
    const active = sameSpec(c.value, current);
    const disp = Array.isArray(c.value) ? c.value.join('  ›  ') : c.value;
    const grade = c.grade || 'D';
    const score = c.stabilityScore != null ? c.stabilityScore : Math.round(c.stability || 0);
    const factors = (c.factors || []).map(f => {
      const delta = Number(f.delta || 0);
      return `<span class="${delta > 0 ? 'plus' : (delta < 0 ? 'minus' : '')}">` +
        `${esc(f.label)} ${delta > 0 ? '+' : ''}${delta}</span>`;
    }).join('');
    return `<div class="cand ${c.level} ${active ? 'active' : ''}" data-idx="${i}">
      <div class="cand-top">
        <span class="cand-kind">${esc(kindLabel(c.kind))}</span>
        <span class="cand-val">${esc(disp)}</span>
        <span class="cand-verdict ${c.level}">${esc(c.verdictLabel)}</span>
      </div>
      <div class="cand-sub">
        <span>匹配 <b>${c.count < 0 ? '?' : c.count}</b></span>
        <span class="grade-chip grade-${grade}" title="${esc(c.gradeNote || '')}">${grade} · ${score}</span>
        <span class="scorebar"><i class="g-${grade}" style="width:${Math.max(0, Math.min(100, score))}%"></i></span>
        <span>${esc(c.why || '')}</span>
        ${c.identity === true ? '<span style="color:var(--ok)">身份命中</span>' : ''}
        ${c.identity === false ? '<span style="color:var(--danger)">非目标元素</span>' : ''}
        ${c.geometry ? '<span style="color:var(--ok)">几何一致</span>' : ''}
        ${c.truncated ? '<span>（已截断显示）</span>' : ''}
      </div>
      ${factors ? `<div class="cand-factors">${factors}</div>` : ''}
      ${c.error ? `<div class="cand-err">${esc(c.error)}</div>` : ''}
    </div>`;
  }).join('');

  list.querySelectorAll('.cand').forEach(node => {
    node.addEventListener('click', () => {
      const c = plan.candidates[parseInt(node.dataset.idx, 10)];
      applySpec(c.value);
    });
  });
}

function kindLabel(k) {
  return {id: 'ID', attr: '属性', tagattr: '标签+属性', class: 'class', css: 'CSS',
    xpath: 'XPath', text: '文本', relative: '相对定位'}[k] || k;
}

function sameSpec(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) return a.join('|') === b.join('|');
  return a === b;
}

async function applySpec(spec) {
  const res = await api('/api/apply', {spec: spec, framePath: S.framePath});
  if (res && res.ok) { applyPick(res); toast('已切换定位表达式', 'ok'); }
  else if (res) toast(res.error || '校验失败', 'danger');
}

/* --------------------------------------------------------------- 用法面板 */

function specLabel(spec) {
  return Array.isArray(spec) ? spec.join('  ›  ') : String(spec || '');
}

async function copyText(text, okMsg) {
  try {
    await navigator.clipboard.writeText(text);
    toast(okMsg || '已复制', 'ok');
  } catch (e) {
    toast('复制失败，请手动选择', 'warn');
  }
}

async function loadUsages() {
  if (!S.spec) {
    S.usages = [];
    S.usageInfo = null;
    renderUsages();
    return;
  }
  const res = await api('/api/usages', {
    spec: S.spec,
    framePath: S.framePath,
    opts: {text: S.usageOpts.text, attrName: S.usageOpts.attrName}
  });
  if (res && res.ok) {
    S.usages = res.usages || [];
    S.usageInfo = res;
  } else {
    S.usages = [];
    S.usageInfo = res || null;
  }
  renderUsages();
}

function renderUsages() {
  const list = $('usageList');
  const empty = $('usageEmpty');
  const cand = S.usageInfo && S.usageInfo.candidate;
  const overall = (S.plan && S.plan.overall) || null;
  const info = cand || overall;
  const grade = info && info.grade;
  const score = info ? (info.stabilityScore != null ? info.stabilityScore : info.score) : null;
  if (grade && score != null) {
    $('usageGrade').textContent = grade;
    $('usageGrade').className = 'uo-grade grade-' + grade;
    $('usageTitle').textContent = specLabel(S.spec) || '当前元素';
    const why = (cand && cand.why) || (overall && overall.why) || '';
    const verdict = (cand && cand.verdictLabel) || (overall && overall.verdictLabel) || '';
    $('usageSub').textContent = `${verdict ? verdict + ' · ' : ''}${grade} 级 · ${score}/100 · ${why}`;
    $('usageBar').style.width = Math.max(0, Math.min(100, score)) + '%';
    $('usageBar').className = 'g-' + grade;
  } else {
    $('usageGrade').textContent = '—';
    $('usageGrade').className = 'uo-grade';
    $('usageTitle').textContent = '捕获元素后显示稳定性评级';
    $('usageSub').textContent = 'A / B / C / D 等级 + 0-100 分';
    $('usageBar').style.width = '0%';
  }
  if (!S.usages.length) {
    empty.hidden = false;
    list.innerHTML = '';
    return;
  }
  empty.hidden = true;
  list.innerHTML = S.usages.map((u, i) => `
    <div class="usage-card" data-idx="${i}">
      <div class="usage-top">
        <strong>${esc(u.label)}</strong>
        <span class="muted">${esc(u.desc || '')}</span>
        <div class="spacer"></div>
        <button class="btn small ghost" data-role="copy">复制</button>
        ${u.action ? '<button class="btn small" data-role="run">运行</button>' : ''}
      </div>
      <pre class="code usage-code">${highlightPython(u.code || '')}</pre>
    </div>`).join('');

  list.querySelectorAll('.usage-card').forEach(node => {
    const u = S.usages[parseInt(node.dataset.idx, 10)];
    node.querySelector('[data-role=copy]').onclick = () => copyText(u.code, '已复制「' + u.label + '」');
    const run = node.querySelector('[data-role=run]');
    if (run) run.onclick = () => runUsage(u);
  });
}

async function runUsage(u) {
  if (!S.spec || !u.action) return;
  const res = await api('/api/action', {
    action: u.action,
    spec: S.spec,
    framePath: S.framePath,
    opts: u.opts || {}
  });
  const box = $('usageResult');
  box.hidden = false;
  box.innerHTML = highlightResult(res);
  if (res && res.ok) {
    toast('已执行：' + u.label, 'ok');
    if (res.actions) { S.actions = res.actions; renderRecorded(); }
    setTimeout(() => api('/api/refresh-element', {}).then(p => { if (p && p.ok) applyPick(p); }), 260);
  } else {
    toast((res && res.error) || '执行失败', 'danger');
  }
}

function scheduleUsages() {
  clearTimeout(S.usageTimer);
  S.usageTimer = setTimeout(loadUsages, 300);
}

/* --------------------------------------------------------------- 代码面板 */

function renderCode() {
  const box = $('codeBox');
  if (!S.code) { box.innerHTML = '<span class="muted">捕获元素后自动生成可运行的 DrissionPage 代码</span>'; return; }
  const text = S.codeTab === 'script' ? S.code.script
    : S.codeTab === 'report' ? S.code.report : S.code.locate;
  box.innerHTML = text ? highlightPython(text)
    : '<span class="muted">暂无内容，请先在「定位」面板选择一条可用表达式</span>';
  $('codeNote').textContent = S.spec
    ? '当前定位：' + (Array.isArray(S.spec) ? S.spec.join(' → ') : S.spec)
    : '尚未选定定位表达式';
}

/* --------------------------------------------------------------- 测试面板 */

async function runAction(action, opts) {
  if (!S.spec) { toast('请先捕获元素或选择定位表达式', 'warn'); return; }
  const btn = event && event.target ? event.target : null;
  if (btn) btn.disabled = true;
  const res = await api('/api/action', {
    action: action, spec: S.spec, framePath: S.framePath, opts: opts || {}
  });
  if (btn) btn.disabled = false;

  $('testResult').innerHTML = highlightResult(res);
  $('testMeta').textContent = `${res.ms != null ? res.ms + ' ms' : ''} ${res.code ? '· ' + res.code : ''}`;
  if (res.ok) {
    toast('动作执行成功', 'ok');
    if (res.actions) { S.actions = res.actions; renderRecorded(); }
    // 动作可能改变页面，稍后刷新定位校验
    setTimeout(() => api('/api/refresh-element', {}).then(p => { if (p && p.ok) applyPick(p); }), 260);
  } else {
    toast(res.error || '动作执行失败', 'danger');
  }
}

function highlightResult(res) {
  if (!res.ok) {
    return highlightPython('# 执行失败\n# ' + (res.error_type || 'Error') + ': ' + (res.error || ''));
  }
  const data = res.data;
  let text;
  if (data === null || data === undefined) text = 'NoReturn';
  else if (typeof data === 'object') text = JSON.stringify(data, null, 2);
  else text = String(data);
  return esc(text);
}

function renderRecorded() {
  const box = $('recordedBox');
  const list = $('recordedList');
  if (!S.actions || !S.actions.length) { box.hidden = true; return; }
  box.hidden = false;
  list.innerHTML = S.actions.map(a =>
    `<span>${esc(a.action)}${a.opts && Object.keys(a.opts).length ? ' ' + esc(JSON.stringify(a.opts)) : ''}</span>`
  ).join('');
}

/* --------------------------------------------------------------- 历史面板 */

async function loadHistory() {
  const res = await api('/api/history');
  S.history = (res.history || []);
  renderHistory();
}

function renderHistory() {
  $('histCount').textContent = S.history.length;
  $('histEmpty').hidden = S.history.length > 0;
  $('histMeta').textContent = S.history.length ? `共 ${S.history.length} 项` : '';
  $('histList').innerHTML = S.history.map(h => `
    <div class="hist" data-id="${h.id}">
      <div class="hist-top">
        <span class="hist-name" data-role="rename" title="双击重命名">${esc(h.name)}</span>
        ${h.grade ? `<span class="grade-chip grade-${h.grade}" title="${esc(h.gradeLabel || '')}">${h.grade}${h.stabilityScore != null ? ' · ' + h.stabilityScore : ''}</span>` : ''}
        <span class="badge ${h.verdict === 'unique' || h.verdict === 'ok' ? 'ok' : (h.verdict === 'multi' ? 'warn' : 'danger')}">${esc(h.verdict || '')}</span>
        <button class="btn small ghost" data-role="del">删除</button>
      </div>
      <div class="hist-spec">${esc(h.specLabel)}</div>
      <div class="hist-sub">
        <span>&lt;${esc(h.tag || '')}&gt;</span>
        <span>${esc(h.text || '')}</span>
        <span>匹配 ${h.count}</span>
        ${h.stabilityScore != null ? `<span>稳定性 ${h.stabilityScore}/100</span>` : ''}
        ${h.framePath && h.framePath.length ? `<span>框架 ${esc(h.framePath.join(' › '))}</span>` : ''}
        <span>${(h.actions || []).length} 个动作</span>
      </div>
      ${(h.actions || []).length ? `<div class="hist-acts">${h.actions.map(a =>
        `<span class="muted" style="font-size:11px;font-family:var(--mono)">${esc(a.action)}</span>`).join('')}</div>` : ''}
    </div>`).join('');

  $('histList').querySelectorAll('.hist').forEach(node => {
    const id = parseInt(node.dataset.id, 10);
    node.querySelector('[data-role=del]').onclick = async () => {
      const res = await api('/api/history/remove', {id: id});
      S.history = res.history || [];
      renderHistory();
      toast('已删除', 'ok');
    };
    const nameNode = node.querySelector('[data-role=rename]');
    nameNode.ondblclick = () => {
      nameNode.contentEditable = 'true';
      nameNode.focus();
      document.execCommand && document.execCommand('selectAll', false, null);
    };
    nameNode.onblur = async () => {
      nameNode.contentEditable = 'false';
      const res = await api('/api/history/update', {id: id, name: nameNode.textContent.trim()});
      if (res && res.ok) {
        S.history = S.history.map(h => h.id === id ? res.entry : h);
        renderHistory();
      }
    };
    nameNode.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); nameNode.blur(); } };
  });
}

/* ------------------------------------------------------------------ 状态 */

function setStatus(kind, text) {
  $('statusDot').className = 'dot ' + kind;
  $('statusText').textContent = text;
}

function setRunningFromStatus(status) {
  if (!status) return;
  S.running = !!status.running;
  $('browserBtn').textContent = S.running ? '关闭浏览器' : '启动浏览器';
  // is_alive() 在导航/CDP 抖动时可能瞬间为 false；只要帧还在持续到达，就不要盖遮罩
  const live = !!S.frame && (Date.now() - (S.lastFrameAt || 0) < 2500);
  $('placeholder').hidden = live || (S.running && !!S.frame);
  if (!S.running && !live) {
    $('phText').textContent = '浏览器未启动，或最近一次连接已断开。';
    $('placeholder').hidden = false;
  } else if (S.running) {
    setStatus('on', '浏览器运行中');
    const u = (status.metrics || {}).u;
    if (u && !$('url').value) $('url').value = prettyUrl(u);
  }
}

async function refreshStatus() {
  const st = await api('/api/status');
  setRunningFromStatus({
    running: st.running,
    metrics: st.metrics || {}
  });
  if (st.running) {
    setStatus('on', `运行中 · 观看者 ${st.viewers || 0} · 帧 ${(st.stats || {}).frames || 0}`);
  }
  if (st.last_error) { $('phText').textContent = st.last_error; }
}

/* ------------------------------------------------------------------ 绑定 */

/* --------------------------------------------------------------- 设置面板 */

let S_PORTS = null;

function setHint(id, text, level) {
  const node = $(id);
  node.textContent = text || '';
  node.className = 'set-hint' + (level ? ' ' + level : '');
}

async function openSettings() {
  $('settingsModal').hidden = false;
  const res = await api('/api/ports');
  if (!res || !res.ok) {
    setHint('debugPortHint', '读取配置失败', 'danger');
    return;
  }
  S_PORTS = res;
  $('debugPortInput').value = res.debug_port;
  $('webPortInput').value = res.web_port;
  $('settingsPath').textContent = res.settings_file || '—';
  setHint('debugPortHint', res.debug_port === res.debug_port_default ? '当前为默认值' : '已自定义');
  setHint('webPortHint',
    res.web_port === res.web_port_default ? '当前为默认值' : '已自定义，下次启动生效');
}

async function applyPort(kind) {
  const isDebug = kind === 'debug';
  const inputId = isDebug ? 'debugPortInput' : 'webPortInput';
  const hintId = isDebug ? 'debugPortHint' : 'webPortHint';
  const raw = $(inputId).value.trim();
  if (!/^\d+$/.test(raw)) { setHint(hintId, '请输入数字端口', 'danger'); return; }
  const port = parseInt(raw, 10);
  if (port < 1024 || port > 65535) {
    setHint(hintId, '端口需在 1024–65535 之间', 'danger');
    return;
  }
  const res = await api(isDebug ? '/api/ports/debug' : '/api/ports/web', {port});
  if (!res || !res.ok) {
    setHint(hintId, (res && res.error) || '保存失败', 'danger');
    return;
  }
  if (!res.changed) {
    setHint(hintId, '与当前一致，未改动', 'ok');
    return;
  }
  let msg = res.port_busy
    ? `已保存为 ${port}（注意：该端口当前被占用）`
    : `已保存为 ${port} · ${res.note || ''}`;
  setHint(hintId, msg, res.port_busy ? 'warn' : 'ok');
  if (isDebug) {
    toast(`调试端口改为 ${port}，点「重启」后生效`, 'ok');
  } else {
    toast(`控制台端口改为 ${port}，下次启动生效`, 'ok');
  }
  openSettings();
}

async function resetPort(kind) {
  const isDebug = kind === 'debug';
  const target = isDebug
    ? (S_PORTS && S_PORTS.debug_port_default) || 9333
    : (S_PORTS && S_PORTS.web_port_default) || 8777;
  $(isDebug ? 'debugPortInput' : 'webPortInput').value = target;
  await applyPort(kind);
}

function bindSettings() {
  $('settingsClose').onclick = () => { $('settingsModal').hidden = true; };
  $('applyDebugPort').onclick = () => applyPort('debug');
  $('applyWebPort').onclick = () => applyPort('web');
  $('resetDebugPort').onclick = () => resetPort('debug');
  $('resetWebPort').onclick = () => resetPort('web');
}

function bindUI() {
  all('.tab').forEach(t => t.addEventListener('click', () => {
    all('.tab').forEach(x => x.classList.remove('active'));
    all('.tabpane').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('pane-' + t.dataset.tab).classList.add('active');
  }));

  all('.subtab').forEach(t => t.addEventListener('click', () => {
    all('.subtab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    S.codeTab = t.dataset.code;
    renderCode();
  }));

  $('goBtn').onclick = () => navigate($('url').value.trim());
  $('url').addEventListener('keydown', e => { if (e.key === 'Enter') navigate($('url').value.trim()); });
  $('backBtn').onclick = () => api('/api/history-nav', {how: 'back'}).then(afterNav);
  $('fwdBtn').onclick = () => api('/api/history-nav', {how: 'forward'}).then(afterNav);
  $('reloadBtn').onclick = () => api('/api/history-nav', {how: 'refresh'}).then(afterNav);
  $('demoBtn').onclick = () => navigate(location.origin + '/demo');

  $('browserBtn').onclick = async () => {
    const btn = $('browserBtn');
    btn.disabled = true;
    const res = S.running ? await api('/api/browser/stop', {}) : await api('/api/browser/start', {});
    btn.disabled = false;
    if (res && res.ok) {
      toast(S.running ? '已关闭浏览器' : '浏览器已启动', 'ok');
      setTimeout(refreshStatus, 600);
    } else {
      toast((res && res.error) || '操作失败', 'danger');
      setTimeout(refreshStatus, 400);
    }
  };

  $('settingsBtn').onclick = openSettings;

  $('restartBtn').onclick = async () => {
    const btn = $('restartBtn');
    btn.disabled = true;
    toast('正在重启浏览器…');
    const res = await api('/api/browser/restart', {});
    btn.disabled = false;
    if (res && res.ok) {
      S.frame = null;
      $('overlay').innerHTML = '';
      toast('浏览器已重启', 'ok');
      setTimeout(refreshStatus, 800);
    } else {
      toast((res && res.error) || '重启失败', 'danger');
    }
  };

  $('phStartBtn').onclick = () => $('browserBtn').click();

  $('pickToggle').onchange = (e) => {
    S.pickMode = e.target.checked;
    send({cmd: 'pickmode', on: S.pickMode});
  };

  $('presetSel').onchange = (e) => {
    send({cmd: 'preset', name: e.target.value});
    api('/api/browser/preset', {name: e.target.value});
  };

  $('usageText').addEventListener('input', (e) => {
    S.usageOpts.text = e.target.value;
    scheduleUsages();
  });
  $('usageAttr').addEventListener('input', (e) => {
    S.usageOpts.attrName = e.target.value.trim() || 'href';
    scheduleUsages();
  });

  $('clearHlBtn').onclick = async () => {
    $('overlay').innerHTML = '';
    await api('/api/clear-highlight', {});
    toast('已清除高亮', 'ok');
  };

  $('zoomFitBtn').onclick = () => { S.fit = true; applyZoom(); };
  $('zoomInBtn').onclick = () => zoomBy(1.25);
  $('zoomOutBtn').onclick = () => zoomBy(0.8);

  $('parentBtn').onclick = () => chainTo(1);
  $('addHistBtn').onclick = async () => {
    const res = await api('/api/history/add', {});
    if (res && res.ok) {
      S.history = res.history;
      renderHistory();
      toast('已加入捕获列表', 'ok');
    } else {
      toast((res && res.error) || '加入失败', 'danger');
    }
  };

  $('verifyBtn').onclick = () => {
    const raw = $('customSpec').value.trim();
    if (!raw) { toast('请输入定位表达式', 'warn'); return; }
    applySpec(raw);
  };
  $('copyCodeBtn').onclick = () => {
    const text = $('codeBox').textContent;
    navigator.clipboard.writeText(text).then(
      () => toast('代码已复制', 'ok'),
      () => toast('复制失败，请手动选择', 'warn'));
  };
  $('downloadCodeBtn').onclick = () => {
    const text = $('codeBox').textContent;
    const blob = new Blob([text], {type: 'text/x-python;charset=utf-8'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'drission_capture.py';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  all('.act').forEach(btn => btn.addEventListener('click', () => {
    const action = btn.dataset.action;
    const opts = btn.dataset.opts ? JSON.parse(btn.dataset.opts) : {};
    const from = btn.dataset.from;
    if (from === 'inputText') {
      opts.text = $('inputText').value;
      opts.clear_first = $('clearFirst').checked;
    } else if (from === 'attr') {
      opts.name = $('attrName').value.trim() || 'href';
    } else if (from === 'prop') {
      opts.name = $('propName').value.trim() || 'value';
    } else if (from === 'sel') {
      opts.by = $('selBy').value;
      opts.value = $('selValue').value;
    } else if (action === 'input') {
      opts.text = $('inputText').value;
      opts.clear_first = $('clearFirst').checked;
    }
    runAction(action, opts);
  }));

  $('clearActionsBtn').onclick = async () => {
    await api('/api/actions/clear', {});
    S.actions = [];
    renderRecorded();
    toast('已清空动作记录', 'ok');
  };

  $('exportBtn').onclick = async () => {
    const res = await api('/api/export', {url: $('url').value.trim()});
    $('modalTitle').textContent = '导出的完整脚本';
    $('modalBody').innerHTML = highlightPython(res.script || '');
    $('modal').hidden = false;
  };
  $('clearHistBtn').onclick = async () => {
    const res = await api('/api/history/clear', {});
    S.history = res.history || [];
    renderHistory();
  };
  $('modalClose').onclick = () => { $('modal').hidden = true; };
  $('modalCopy').onclick = () => navigator.clipboard.writeText($('modalBody').textContent)
    .then(() => toast('已复制', 'ok'));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      $('modal').hidden = true;
      $('settingsModal').hidden = true;
    }
  });
}

async function navigate(url) {
  if (!url) return;
  const res = await api('/api/navigate', {url: url});
  afterNav(res);
}

function afterNav(res) {
  if (res && !res.ok) { toast(res.error || '导航失败', 'danger'); return; }
  if (res && res.data && res.data.url) $('url').value = prettyUrl(res.data.url);
  S.desc = null; S.plan = null; S.spec = null; S.code = null;
  S.usages = []; S.usageInfo = null;
  $('overlay').innerHTML = '';
  $('usageResult').hidden = true;
  $('usageResult').innerHTML = '';
  renderElement(); renderCandidates(); renderCode(); renderUsages();
}

/* ------------------------------------------------------------------ 启动 */

bindUI();
bindSettings();
bindViewport();
renderScopeBar();
connect();
loadHistory();
setInterval(refreshStatus, 5000);
setInterval(() => send({cmd: 'ping'}), 20000);
