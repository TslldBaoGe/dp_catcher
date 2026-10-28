'use strict';

/* 面板逻辑：注入到页面里，用 Shadow DOM 隔离样式。
 *
 * 与桌面版的关键差异：这里没有 Python 后端，所有「校验 / 评级 / 代码生成」
 * 都在浏览器内完成。校验用和 DrissionPage 一致的解析语义（css / xpath /
 * tag:xx@attr=yy / text= / tx=），并对「首个匹配是否真的是目标元素」做身份比对。
 */

(function () {
  if (window.__DPC_PANEL__) {
    window.__DPC_PANEL__.toggle();
    return;
  }

  const STATE = {
    visible: false,
    pickMode: false,
    desc: null,
    plan: null,
    spec: null,
    code: null,
    usages: [],
    codeTab: 'script',
    store: {},
    actions: [],
    history: [],
    framePath: [],
  };

  /* ------------------------------------------------------------ 工具函数 */

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const PY_KW = ['from', 'import', 'def', 'return', 'if', 'else', 'elif', 'for', 'while',
    'in', 'not', 'and', 'or', 'try', 'except', 'finally', 'with', 'as', 'class', 'lambda',
    'None', 'True', 'False', 'print', 'raise', 'break', 'continue', 'pass', 'global', 'is'];

  function hlPython(code) {
    const kw = PY_KW.join('|');
    const re = new RegExp(
      '("(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\')' +
      '|(#[^\\n]*)' +
      '|\\b(\\d+\\.?\\d*)\\b' +
      '|\\b(' + kw + ')\\b' +
      '|\\b([A-Za-z_]\\w*)(?=\\()', 'g');
    return esc(code).replace(re, (m, str, com, num, word, fn) => {
      if (str !== undefined) return '<span style="color:#a5e075">' + str + '</span>';
      if (com !== undefined) return '<span style="color:#5b6b8c;font-style:italic">' + com + '</span>';
      if (num !== undefined) return '<span style="color:#f7a26b">' + num + '</span>';
      if (word !== undefined) return '<span style="color:#c792ea">' + word + '</span>';
      if (fn !== undefined) return '<span style="color:#6fd3ff">' + fn + '</span>';
      return m;
    });
  }

  function q(text) {
    return String(text == null ? '' : text)
      .replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '\\r');
  }

  function toast(msg, kind) {
    const box = document.createElement('div');
    box.className = 'toast ' + (kind || '');
    box.textContent = msg;
    host.appendChild(box);
    setTimeout(() => {
      box.style.transition = 'opacity .3s';
      box.style.opacity = '0';
      setTimeout(() => box.remove(), 320);
    }, kind === 'danger' ? 4200 : 2400);
  }

  async function copyText(text, msg) {
    try {
      await navigator.clipboard.writeText(text);
      toast(msg || '已复制', 'ok');
    } catch (e) {
      // 某些页面禁用 clipboard API，退回 execCommand
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;opacity:0;left:-9999px';
      host.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); toast(msg || '已复制', 'ok'); }
      catch (err) { toast('复制失败，请手动选择', 'warn'); }
      ta.remove();
    }
  }

  /* --------------------------------------------- DrissionPage 定位解析/校验 */

  function cssEscape(v) {
    if (window.CSS && CSS.escape) return CSS.escape(v);
    return String(v).replace(/[^a-zA-Z0-9_\u00a0-\uffff-]/g, '\\$&');
  }

  function splitLoc(loc) {
    // 返回 {kind, ...}：'css:xxx' / 'xpath:xxx' / 'tag:div@a=b' / 'tag:div@@class=x' / 'text=..' / 'tx=..' / '@a=b' / '#id' / '.cls'
    if (typeof loc !== 'string') return null;
    if (loc.startsWith('xpath:')) return { kind: 'xpath', expr: loc.slice(6) };
    if (loc.startsWith('css:')) return { kind: 'css', expr: loc.slice(4) };
    if (loc.startsWith('tag:')) {
      const body = loc.slice(4);
      let tag = body;
      let attr = null;
      let value = null;
      let double = false;
      const m = body.match(/^([A-Za-z0-9_-]+)(@@?)([^=]+)=(.*)$/);
      if (m) {
        tag = m[1];
        double = m[2] === '@@';
        attr = m[3];
        value = m[4];
      }
      return { kind: 'tag', tag, attr, value, double };
    }
    if (loc.startsWith('tx:') || loc.startsWith('tx=')) {
      return { kind: 'tx', expr: loc.slice(3) };
    }
    if (loc.startsWith('text:') || loc.startsWith('text=')) {
      return { kind: 'text', expr: loc.slice(5) };
    }
    if (loc.startsWith('@')) {
      const m = loc.slice(1).match(/^([^=]+)=(.*)$/);
      if (m) return { kind: 'attr', attr: m[1], value: m[2] };
      return null;
    }
    if (loc.startsWith('#') || loc.startsWith('.')) {
      return { kind: 'css', expr: loc };
    }
    return { kind: 'css', expr: loc };
  }

  function collectAll(root) {
    // 含 Shadow DOM 的全量元素
    const out = [];
    const walk = (node) => {
      const nodes = node.querySelectorAll('*');
      for (let i = 0; i < nodes.length; i++) {
        out.push(nodes[i]);
        if (nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
      }
    };
    walk(root);
    return out;
  }

  function queryLoc(parsed, root) {
    const scope = root || document;
    if (!parsed) return { list: [], error: '无法解析表达式' };
    try {
      switch (parsed.kind) {
        case 'css':
          return { list: Array.from(scope.querySelectorAll(parsed.expr)) };
        case 'xpath': {
          const doc = scope.ownerDocument || scope;
          const iter = doc.evaluate(parsed.expr, scope, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
          const list = [];
          for (let i = 0; i < iter.snapshotLength; i++) list.push(iter.snapshotItem(i));
          return { list };
        }
        case 'tag': {
          const all = collectAll(scope);
          const list = all.filter((el) => {
            if (el.tagName.toLowerCase() !== parsed.tag.toLowerCase()) return false;
            if (!parsed.attr) return true;
            const actual = el.getAttribute(parsed.attr);
            if (actual === null) return false;
            // @@ 是整串精确匹配（DrissionPage 语义），@ 也是精确匹配
            return actual === parsed.value;
          });
          return { list };
        }
        case 'attr': {
          const all = collectAll(scope);
          return { list: all.filter((el) => el.getAttribute(parsed.attr) === parsed.value) };
        }
        case 'text': {
          const all = collectAll(scope);
          const want = parsed.expr;
          return {
            list: all.filter((el) => {
              const t = (el.innerText != null && el.innerText !== '')
                ? el.innerText : el.textContent;
              return (t || '').trim() === want.trim();
            }),
          };
        }
        case 'tx': {
          const all = collectAll(scope);
          const want = parsed.expr;
          return {
            list: all.filter((el) => {
              const t = (el.innerText != null && el.innerText !== '')
                ? el.innerText : el.textContent;
              return (t || '').includes(want);
            }),
          };
        }
        default:
          return { list: [], error: '不支持的表达式类型' };
      }
    } catch (e) {
      return { list: [], error: String(e && e.message || e) };
    }
  }

  const KIND_STABILITY = {
    id: 96, attr: 84, tagattr: 74, class: 66, css: 54, xpath: 44, text: 36, relative: 88,
  };

  const DYNAMIC_STRONG = [
    /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/,
    /^[0-9a-fA-F]{16,}$/,
    /.*\d{10,}.*/,
  ];
  const DYNAMIC_WEAK = [/^\d{3,}$/, /^(ember|react|vue|radix|headlessui|mui|el|ext)-?\d+$/i];
  const CSS_IN_JS = /^(css|sc|jsx|emotion|styled)-[a-z0-9]{4,}$/i;

  function structurePenalty(kind, value) {
    const text = Array.isArray(value) ? value.join(' >> ') : String(value || '');
    if (kind === 'xpath') {
      return Math.min(25, Math.max(0, (text.split('/').length - 3) * 1.5)
        + (text.split('[').length - 1) * 2);
    }
    if (kind === 'css') {
      return Math.min(25, (text.split('>').length - 1) * 3
        + (text.split(':nth-of-type').length - 1) * 4);
    }
    if (kind === 'relative' && Array.isArray(value) && value.length > 2) {
      return Math.min(12, (value.length - 2) * 4);
    }
    return 0;
  }

  function dynamicPenalty(kind, value) {
    if (!['id', 'attr', 'tagattr', 'class'].includes(kind)) return { p: 0, label: '' };
    const text = String(value || '');
    for (const token of text.split(/[=\s]+/)) {
      const t = token.replace(/^["'#.@]+|["']+$/g, '');
      if (!t || t === 'tag' || t === 'class') continue;
      if (CSS_IN_JS.test(t)) return { p: 8, label: '类名像 CSS-in-JS 自动生成' };
      if (DYNAMIC_STRONG.some((re) => re.test(t))) return { p: 12, label: '属性值像 UUID / 哈希 / 时间戳' };
      if (DYNAMIC_WEAK.some((re) => re.test(t))) return { p: 6, label: '属性值像自增数字或框架生成 id' };
    }
    return { p: 0, label: '' };
  }

  function gradeOf(score) {
    if (score >= 85) return 'A';
    if (score >= 70) return 'B';
    if (score >= 50) return 'C';
    return 'D';
  }

  function gradeLabel(g) {
    return { A: '稳定', B: '较稳', C: '一般', D: '不稳' }[g] || '';
  }

  function scoreCandidate(cand, parsed, result, target) {
    const kind = cand.k;
    const value = cand.loc;
    const count = result.error ? -1 : result.list.length;
    let identity = null;
    if (count > 0 && target) identity = result.list[0] === target;
    let geometry = false;
    if (count > 0 && target) {
      const a = result.list[0].getBoundingClientRect();
      const b = target.getBoundingClientRect();
      geometry = Math.abs(a.left - b.left) < 1.5 && Math.abs(a.top - b.top) < 1.5
        && Math.abs(a.width - b.width) < 1.5 && Math.abs(a.height - b.height) < 1.5;
    }
    const hit = identity === true || geometry;

    let verdict;
    if (result.error) verdict = 'invalid';
    else if (count <= 0) verdict = 'none';
    else if (identity === false && !geometry) verdict = 'wrong';
    else if (count === 1) verdict = hit ? 'unique' : 'ok';
    else verdict = (hit || !target) ? 'multi' : 'wrong';

    let score = cand.st || KIND_STABILITY[kind] || 50;
    const cap = 100;
    let ceiling = 100;
    const factors = [{ label: '基础分', delta: Math.round(score) }];

    const sp = structurePenalty(kind, value);
    if (sp) {
      score -= sp;
      if (sp >= 12) ceiling = Math.min(ceiling, 79);
      factors.push({ label: '结构路径较深', delta: -Math.round(sp) });
    }
    const dp = dynamicPenalty(kind, value);
    if (dp.p) {
      score -= dp.p;
      if (dp.label.includes('CSS-in-JS')) ceiling = Math.min(ceiling, 69);
      else if (dp.label.includes('UUID')) ceiling = Math.min(ceiling, 79);
      else ceiling = Math.min(ceiling, 84);
      factors.push({ label: dp.label, delta: -dp.p });
    }
    if (verdict === 'unique' || verdict === 'ok') {
      score += 6;
      factors.push({ label: '唯一命中目标', delta: 6 });
    } else if (verdict === 'multi') {
      score -= 12;
      factors.push({ label: '匹配多个元素', delta: -12 });
    } else if (verdict === 'wrong') {
      score = Math.min(score, 20);
      factors.push({ label: '首个匹配不是目标（错位）', delta: 0 });
    } else {
      score = 0;
      factors.push({ label: '未匹配或语法错误', delta: 0 });
    }
    score = Math.max(0, Math.min(100, Math.min(score, ceiling)));
    const grade = gradeOf(score);

    const meta = {
      unique: ['推荐', 'ok'], ok: ['可用', 'ok'], multi: ['不唯一', 'warn'],
      wrong: ['错位', 'danger'], none: ['未匹配', 'danger'], invalid: ['语法错误', 'danger'],
    }[verdict];

    return {
      kind, value, count, identity, geometry,
      verdict, verdictLabel: meta[0], level: meta[1],
      score: Math.round(score), grade, gradeLabel: gradeLabel(grade),
      factors, why: cand.why || '', error: result.error || null,
    };
  }

  /* -------------------------------------------------- 定位链（iframe / shadow）*/

  function resolveScope(framePath) {
    // 原面板运行在顶层；framePath 为空时直接操作 document
    if (!framePath || !framePath.length) return document;
    let win = window;
    let doc = document;
    for (const sel of framePath) {
      const parsed = splitLoc(sel);
      const res = queryLoc(parsed, doc);
      const frame = res.list[0];
      if (!frame) return null;
      try {
        doc = frame.contentDocument;
        win = frame.contentWindow;
      } catch (e) {
        return null;
      }
      if (!doc) return null;
    }
    return doc;
  }

  /* -------------------------------------------------------- 代码生成 */

  function locateExpr(spec, shadowChain) {
    let expr = 'page';
    if (shadowChain && shadowChain.length && !Array.isArray(spec)) {
      shadowChain.forEach((sel) => {
        expr += ".ele('" + q(sel) + "').shadow_root";
      });
      return expr + ".ele('" + q(spec) + "')";
    }
    const parts = Array.isArray(spec) ? spec : [spec];
    parts.forEach((p, i) => {
      if (i === 0) expr += ".ele('" + q(p) + "')";
      else if (String(p).startsWith('shadow:')) {
        expr += ".shadow_root.ele('" + q(String(p).slice(7)) + "')";
      } else expr += ".ele('" + q(p) + "')";
    });
    return expr;
  }

  function buildUsages(spec, shadowChain) {
    const loc = 'ele = ' + locateExpr(spec, shadowChain);
    const many = locateExpr(spec, shadowChain).replace(/\.ele\('([^']*)'\)$/, ".eles('$1')");
    return [
      { id: 'wait', label: '等待出现', desc: 'DrissionPage 会按超时等待元素',
        code: loc + "\nif not ele:\n    raise RuntimeError('元素未出现')" },
      { id: 'click', label: '点击', desc: '真实鼠标点击',
        code: loc + '\nele.click()', action: 'click' },
      { id: 'input', label: '输入文本', desc: '先清空再写入',
        code: loc + "\nele.clear()\nele.input('" + q(STATE.store.text || '要输入的文本') + "')",
        action: 'input' },
      { id: 'text', label: '读取文本', desc: '元素可见文本',
        code: loc + '\nprint(ele.text)', action: 'get_text' },
      { id: 'attr', label: '读取属性', desc: '读取 HTML 属性',
        code: loc + "\nprint(ele.attr('" + q(STATE.store.attr || 'href') + "'))", action: 'get_attr' },
      { id: 'assert', label: '存在断言', desc: '自动化脚本检查点',
        code: loc + "\nassert ele, '元素不存在'\nassert ele.states.is_displayed, '元素不可见'" },
      { id: 'scroll', label: '滚动到可见', desc: '滚动进视口',
        code: loc + '\nele.scroll.to_see()', action: 'scroll_to' },
      { id: 'batch', label: '批量遍历', desc: '同一表达式匹配的全部元素',
        code: 'eles = ' + many + "\nprint('匹配数量:', len(eles))\nfor ele in eles:\n    print(ele.text)" },
    ];
  }

  function buildScript(spec, shadowChain) {
    const header = [
      '# -*- coding: utf-8 -*-',
      '"""由「DrissionPage 元素捕获器」Chrome 扩展生成。',
      '',
      '依赖：pip install DrissionPage',
      '"""',
      'import time',
      '',
      'from DrissionPage import ChromiumPage, ChromiumOptions',
      '',
      '',
      'def main():',
      '    co = ChromiumOptions()',
      '    page = ChromiumPage(co)',
      "    page.get('" + q(location.href) + "')",
      '',
    ];
    const lines = STATE.history.length
      ? STATE.history.map((h, i) => [
          '    # ---------- ' + (i + 1) + '. ' + h.name + ' ----------',
          '    ' + h.locate,
          ...(h.actions || []).map((line) => '    ' + line),
          '',
        ]).flat()
      : [
          '    # ---------- ' + (STATE.desc ? STATE.desc.tag : '元素') + ' ----------',
          '    ele = ' + locateExpr(spec, shadowChain),
          '    print(ele.text)',
          '',
        ];
    return header.concat(lines, ['', 'if __name__ == "__main__":', '    main()']).join('\n');
  }

  /* ------------------------------------------------------------ 面板构建 */

  // panel.js 会被注入到任意页面，页面里通常没有 panel.html 里的挂载点，
  // 所以这里必须自己创建 host 元素（绝不能 assume 它存在）。
  let host = document.getElementById('__dpc_panel_host__');
  if (!host) {
    host = document.createElement('div');
    host.id = '__dpc_panel_host__';
    // z-index 拉满，且自身不参与布局
    host.style.cssText = 'all:initial;position:fixed;top:0;left:0;width:0;height:0;'
      + 'z-index:2147483647;';
    (document.body || document.documentElement).appendChild(host);
  }
  const shadow = host.shadowRoot || host.attachShadow({ mode: 'open' });

  function cssText() {
    // panel.css 由后台注入时已通过 <link> 引入，这里从页面读取
    return '';
  }

  async function loadCss() {
    try {
      const url = chrome.runtime.getURL('panel.css');
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = url;
      shadow.appendChild(link);
      return;
    } catch (e) { /* ignore */ }
    // 无扩展 API（例如被当成普通脚本注入）时，直接内联样式
    const style = document.createElement('style');
    style.textContent = STATE.inlineCss || '';
    shadow.appendChild(style);
  }

  function buildPanel() {
    const wrap = document.createElement('div');
    wrap.className = 'panel hidden';
    wrap.innerHTML = `
      <div class="head">
        <div class="logo">DP</div>
        <div>
          <div class="title">元素捕获器</div>
          <div class="sub">DrissionPage · 浏览器内校验</div>
        </div>
        <div class="spacer"></div>
        <button class="icon-btn" id="closeBtn" title="关闭面板">×</button>
      </div>
      <div class="bar">
        <button class="pick-btn" id="pickBtn"><span>◎</span><span id="pickText">开始拾取</span></button>
        <button class="btn ghost" id="clearHlBtn">清除高亮</button>
        <span class="hint" id="hintText">Alt+P 开关拾取</span>
      </div>
      <div class="tabs">
        <button class="tab active" data-tab="element">元素</button>
        <button class="tab" data-tab="locator">定位</button>
        <button class="tab" data-tab="code">代码</button>
        <button class="tab" data-tab="test">测试</button>
        <button class="tab" data-tab="list">列表 <b id="listCount">0</b></button>
      </div>
      <div class="body">
        <div class="pane active" id="pane-element">
          <div class="empty" id="elEmpty">点「开始拾取」，再点页面上的元素</div>
          <div id="elBody" hidden>
            <div class="el-head">
              <code class="el-tag" id="elTag">-</code>
              <span class="chip" id="elVerdict"></span>
              <span class="chip" id="elGrade" hidden></span>
              <div class="spacer"></div>
              <button class="btn" id="parentBtn">选父级</button>
              <button class="btn primary" id="addBtn">加入列表</button>
            </div>
            <table class="kv" id="elTable"></table>
            <h4>属性</h4>
            <table class="kv" id="attrTable"></table>
            <h4>父级链</h4>
            <div id="chainBox"></div>
          </div>
        </div>

        <div class="pane" id="pane-locator">
          <div class="row">
            <input type="text" class="grow" id="customSpec" placeholder="自定义表达式，如 #id / @name=wd / tx=登录">
            <button class="btn primary" id="verifyBtn">校验</button>
          </div>
          <div id="candList"></div>
        </div>

        <div class="pane" id="pane-code">
          <div class="row">
            <button class="btn" data-code="script">完整脚本</button>
            <button class="btn" data-code="locate">定位片段</button>
            <button class="btn" data-code="usages">用法</button>
            <div class="spacer"></div>
            <button class="btn" id="copyCode">复制</button>
            <button class="btn" id="dlCode">下载</button>
          </div>
          <pre class="code" id="codeBox"><span style="color:#8b9ac0">拾取元素后自动生成</span></pre>
        </div>

        <div class="pane" id="pane-test">
          <div class="groups">
            <div class="group">
              <div class="group-title">交互</div>
              <div class="group-body">
                <button class="btn act" data-action="click">点击</button>
                <button class="btn act" data-action="dblclick">双击</button>
                <button class="btn act" data-action="contextmenu">右键</button>
                <button class="btn act" data-action="click_js">JS 点击</button>
                <button class="btn act" data-action="hover">悬停</button>
                <button class="btn act" data-action="focus">聚焦</button>
                <button class="btn act" data-action="blur">失焦</button>
                <button class="btn act" data-action="scroll_to">滚动到可见</button>
                <button class="btn act" data-action="check">勾选</button>
              </div>
            </div>
            <div class="group">
              <div class="group-title">输入</div>
              <div class="group-body">
                <input type="text" class="grow" id="inputText" placeholder="要输入的文本">
                <button class="btn act" data-action="input">写入</button>
                <button class="btn act" data-action="clear">清空</button>
              </div>
            </div>
            <div class="group">
              <div class="group-title">读取</div>
              <div class="group-body">
                <button class="btn act" data-action="get_text">文本</button>
                <button class="btn act" data-action="get_html">HTML</button>
                <button class="btn act" data-action="get_attr">取属性</button>
                <button class="btn act" data-action="get_attrs">全部属性</button>
                <button class="btn act" data-action="get_value">value</button>
                <button class="btn act" data-action="get_states">状态</button>
                <button class="btn act" data-action="get_rect">位置尺寸</button>
                <button class="btn act" data-action="get_css">样式</button>
              </div>
            </div>
          </div>
          <pre class="result" id="testResult">选择一个动作开始测试</pre>
        </div>

        <div class="pane" id="pane-list">
          <div class="row">
            <button class="btn primary" id="exportBtn">导出完整脚本</button>
            <button class="btn ghost" id="clearListBtn">清空</button>
            <div class="spacer"></div>
            <span class="hint" id="listMeta"></span>
          </div>
          <div id="historyList"></div>
          <div class="empty" id="listEmpty">还没有记录</div>
        </div>
      </div>
    `;
    shadow.appendChild(wrap);
    return wrap;
  }

  async function init() {
    await loadCss();
    const panel = buildPanel();
    STATE.panel = panel;
    bindUI(panel);
    try {
      const res = await chrome.runtime.sendMessage({ cmd: 'panel:load' });
      if (res && res.ok) STATE.store = res.data || {};
      if (STATE.store.text) panel.querySelector('#inputText').value = STATE.store.text;
      if (STATE.store.attr) panel.querySelector('#customSpec').placeholder =
        '自定义表达式，如 #id / @name=wd / tx=登录';
      STATE.history = STATE.store.history || [];
      renderHistory();
    } catch (e) { /* 独立运行时没有 storage */ }
    api('show');
  }

  /* ------------------------------------------------------------- UI 绑定 */

  function bindUI(panel) {
    panel.querySelector('#closeBtn').onclick = () => api('hide');
    panel.querySelector('#pickBtn').onclick = () => api('togglePick');
    panel.querySelector('#clearHlBtn').onclick = () => api('clearHighlight');

    panel.querySelectorAll('.tab').forEach((t) => {
      t.onclick = () => {
        panel.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
        panel.querySelectorAll('.pane').forEach((x) => x.classList.remove('active'));
        t.classList.add('active');
        panel.querySelector('#pane-' + t.dataset.tab).classList.add('active');
      };
    });

    panel.querySelectorAll('[data-code]').forEach((b) => {
      b.onclick = () => { STATE.codeTab = b.dataset.code; renderCode(); };
    });

    panel.querySelector('#verifyBtn').onclick = () => {
      const raw = panel.querySelector('#customSpec').value.trim();
      if (!raw) { toast('请输入表达式', 'warn'); return; }
      applySpec(raw);
    };
    panel.querySelector('#customSpec').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') panel.querySelector('#verifyBtn').click();
    });

    panel.querySelector('#copyCode').onclick = () =>
      copyText(panel.querySelector('#codeBox').textContent, '代码已复制');
    panel.querySelector('#dlCode').onclick = () => {
      const text = panel.querySelector('#codeBox').textContent;
      const blob = new Blob([text], { type: 'text/x-python;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'drission_capture.py';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    };

    panel.querySelector('#parentBtn').onclick = () => api('parent');
    panel.querySelector('#addBtn').onclick = () => api('addHistory');

    panel.querySelectorAll('.act').forEach((b) => {
      b.onclick = () => runAction(b.dataset.action);
    });
    panel.querySelector('#inputText').addEventListener('input', (e) => {
      STATE.store.text = e.target.value;
      saveStore();
    });

    panel.querySelector('#exportBtn').onclick = () => {
      if (!STATE.history.length) { toast('列表为空', 'warn'); return; }
      STATE.codeTab = 'script';
      const lines = ['# -*- coding: utf-8 -*-', 'import time',
        'from DrissionPage import ChromiumPage, ChromiumOptions', '', '',
        'def main():', '    co = ChromiumOptions()', '    page = ChromiumPage(co)',
        "    page.get('" + q(location.href) + "')", ''];
      STATE.history.forEach((h, i) => {
        lines.push('    # ---------- ' + (i + 1) + '. ' + h.name + ' ----------');
        lines.push('    ' + h.locate);
        (h.actions || []).forEach((a) => lines.push('    ' + a));
        lines.push('');
      });
      lines.push('', 'if __name__ == "__main__":', '    main()');
      const script = lines.join('\n');
      panel.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
      panel.querySelector('.tab[data-tab="code"]').classList.add('active');
      panel.querySelectorAll('.pane').forEach((x) => x.classList.remove('active'));
      panel.querySelector('#pane-code').classList.add('active');
      panel.querySelector('#codeBox').innerHTML = hlPython(script);
      copyText(script, '已生成并复制脚本');
    };
    panel.querySelector('#clearListBtn').onclick = () => {
      STATE.history = [];
      saveStore();
      renderHistory();
    };
  }

  async function saveStore() {
    try {
      await chrome.runtime.sendMessage({ cmd: 'panel:save', data: { text: STATE.store.text || '' } });
    } catch (e) { /* ignore */ }
  }

  /* ------------------------------------------------------- 与 content script 通信 */

  // 面板自身就是 content script 的一部分，直接调用 __DPC__ 更可靠
  function picker() {
    return window.__DPC__ || null;
  }

  function api(action, payload) {
    switch (action) {
      case 'show':
        STATE.visible = true;
        STATE.panel.classList.remove('hidden');
        return;
      case 'hide':
        STATE.visible = false;
        STATE.panel.classList.add('hidden');
        if (STATE.pickMode) setPick(false);
        return;
      case 'togglePick':
        setPick(!STATE.pickMode);
        return;
      case 'clearHighlight':
        clearHighlight();
        return;
      case 'parent':
        chainToParent();
        return;
      case 'addHistory':
        addHistory();
        return;
      default:
        return;
    }
  }

  function setPick(on) {
    STATE.pickMode = !!on;
    const btn = STATE.panel.querySelector('#pickBtn');
    const txt = STATE.panel.querySelector('#pickText');
    btn.classList.toggle('on', STATE.pickMode);
    txt.textContent = STATE.pickMode ? '拾取中…点页面元素' : '开始拾取';
    const p = picker();
    if (p) p.setPick(STATE.pickMode);
    if (!on) clearHover();
  }

  function clearHover() {
    const p = picker();
    if (p && p.hideHover) p.hideHover();
  }

  /* 监听页内点击（picker.js 在拾取模式下会拦截并记录 pick 事件） */
  function onPicked(desc) {
    if (!desc) return;
    STATE.desc = desc;
    STATE.desc.framePath = [];
    buildPlan(desc);
    setPick(false);
  }

  /* MAIN world 与 isolated world 的 window 是隔离的，自定义事件传不过来。
     桥接方式：picker 把最近的拾取结果 JSON 写进 <html> 的 data 属性，
     这里用 MutationObserver 监听属性变化 —— DOM 是两边共享的。 */
  const BRIDGE_ATTR = 'data-dpc-pick';
  let lastBridge = null;

  function readBridge() {
    const raw = document.documentElement.getAttribute(BRIDGE_ATTR);
    if (!raw || raw === lastBridge) return;
    lastBridge = raw;
    try {
      const desc = JSON.parse(raw);
      if (desc && desc.tag) onPicked(desc);
    } catch (e) { /* ignore */ }
  }

  try {
    const obs = new MutationObserver(() => readBridge());
    obs.observe(document.documentElement, { attributes: true, attributeFilter: [BRIDGE_ATTR] });
    readBridge();   // 处理注入前已有的结果
  } catch (e) { /* ignore */ }

  // 兼容：若 panel 与 picker 恰好在同一 world（例如手动一起注入），事件仍可用
  window.addEventListener('__dpc_pick__', (e) => {
    const desc = e.detail;
    if (desc) onPicked(desc);
  });

  /* --------------------------------------------------------- 定位方案构建 */

  function buildPlan(desc) {
    const target = picker() && picker().target ? picker().target : null;
    const cands = (desc.cands || []).slice();
    const outcomes = [];

    for (const cand of cands.slice(0, 14)) {
      const parsed = splitLoc(cand.loc);
      const res = queryLoc(parsed, document);
      outcomes.push(scoreCandidate(cand, parsed, res, target));
    }

    // DrissionPage 风格的相对定位：父级锚点 + 子级表达式
    const rel = buildRelative(desc, outcomes, target);
    if (rel) outcomes.push(rel);

    outcomes.sort((a, b) => {
      const rank = (c) => ({ unique: 0, ok: 1, multi: 2, wrong: 3, none: 4, invalid: 5 }[c.verdict] ?? 6);
      return rank(a) - rank(b) || b.score - a.score;
    });

    const best = outcomes.find((c) => c.verdict === 'unique') || outcomes.find((c) => c.verdict === 'ok')
      || outcomes.find((c) => c.verdict === 'multi') || outcomes[0] || null;

    STATE.plan = { candidates: outcomes, recommended: best };
    STATE.spec = best ? best.value : null;
    highlight(best ? best.value : null);
    renderElement();
    renderCandidates();
    renderCode();
    renderUsages();
  }

  function buildRelative(desc, outcomes, target) {
    // 尾表达式：只用语义化定位（tag/attr/class/text），css/xpath 在元素内查找不可靠
    const tails = [];
    for (const c of (desc.cands || [])) {
      if (['id', 'attr', 'tagattr', 'class', 'text'].includes(c.k) && !tails.includes(c.loc)) {
        tails.push(c.loc);
      }
    }
    if (!tails.length) return null;
    // 锚点：父级链里有语义化定位的那一层
    for (const anc of (desc.chain || []).slice(0, 3)) {
      for (const ac of (anc.cands || [])) {
        if (!['id', 'attr', 'tagattr', 'class'].includes(ac.k)) continue;
        const anchorRes = queryLoc(splitLoc(ac.loc), document);
        if (anchorRes.list.length !== 1) continue;
        const anchorEl = anchorRes.list[0];
        if (['HTML', 'BODY'].includes(anchorEl.tagName)) continue;
        for (const tail of tails.slice(0, 3)) {
          const parsed = splitLoc(tail);
          const res = queryLoc(parsed, anchorEl);
          if (res.list.length !== 1) continue;
          if (target && res.list[0] !== target) continue;
          const spec = [ac.loc, tail];
          const a = res.list[0].getBoundingClientRect();
          const b = target ? target.getBoundingClientRect() : a;
          const geometry = Math.abs(a.left - b.left) < 1.5 && Math.abs(a.top - b.top) < 1.5;
          let score = 88 + 6 - 4;   // 相对定位基础分 + 唯一命中 - 链长
          if (Math.abs(a.width - b.width) > 1.5 || Math.abs(a.height - b.height) > 1.5) score -= 5;
          score = Math.max(0, Math.min(100, score));
          return {
            kind: 'relative', value: spec, count: 1,
            identity: target ? res.list[0] === target : null, geometry,
            verdict: 'unique', verdictLabel: '推荐', level: 'ok',
            score: Math.round(score), grade: gradeOf(score), gradeLabel: gradeLabel(gradeOf(score)),
            factors: [{ label: '基础分（相对定位）', delta: 88 },
                      { label: '唯一命中目标', delta: 6 },
                      { label: '链长扣分', delta: -4 }],
            why: '相对定位：先锁定稳定祖先，再在其内部定位', error: null,
          };
        }
        break;
      }
    }
    return null;
  }

  /* ------------------------------------------------------------- 渲染 */

  function renderElement() {
    const p = STATE.panel;
    const d = STATE.desc;
    p.querySelector('#elEmpty').hidden = !!d;
    p.querySelector('#elBody').hidden = !d;
    if (!d) return;

    const name = d.tag + (d.id ? '#' + d.id : '')
      + ((d.cls || []).length ? '.' + d.cls.slice(0, 3).join('.') : '');
    p.querySelector('#elTag').textContent = name;

    const best = STATE.plan && STATE.plan.recommended;
    const v = p.querySelector('#elVerdict');
    const g = p.querySelector('#elGrade');
    if (best) {
      v.textContent = best.verdictLabel + ' · ' + best.count + ' 个';
      v.className = 'chip ' + best.level;
      g.hidden = false;
      g.textContent = best.grade + ' · ' + best.score;
      g.className = 'chip grade-' + best.grade;
      g.title = best.gradeLabel;
    } else {
      v.textContent = '无可用定位';
      v.className = 'chip danger';
      g.hidden = true;
    }

    const rows = [
      ['标签', d.tag],
      ['id', d.id || '—'],
      ['class', (d.cls || []).join(' ') || '—'],
      ['文本', (d.text || '').slice(0, 60) || '—'],
      ['value', d.value || '—'],
      ['位置', d.rect ? Math.round(d.rect[0]) + ', ' + Math.round(d.rect[1])
        + '  ' + Math.round(d.rect[2]) + '×' + Math.round(d.rect[3]) : '—'],
      ['评级', best ? best.grade + '（' + best.score + '/100）· ' + best.gradeLabel : '—'],
      ['可输入', d.editable ? '是' : '否'],
    ];
    p.querySelector('#elTable').innerHTML = rows
      .map(([k, val]) => '<tr><td>' + esc(k) + '</td><td>' + esc(val) + '</td></tr>').join('');

    const attrs = d.attrs || {};
    p.querySelector('#attrTable').innerHTML = Object.keys(attrs).length
      ? Object.keys(attrs).map((k) => '<tr><td>' + esc(k) + '</td><td>' + esc(attrs[k]) + '</td></tr>').join('')
      : '<tr><td colspan="2" style="color:#8b9ac0">无属性</td></tr>';

    const chain = d.chain || [];
    p.querySelector('#chainBox').innerHTML = chain.length
      ? chain.map((c, i) => {
          const label = c.tag + (c.id ? '#' + c.id : '')
            + ((c.cls || []).length ? '.' + c.cls.slice(0, 2).join('.') : '');
          return '<div class="row" style="margin:0 0 4px"><button class="btn" data-depth="' + (i + 1)
            + '">' + esc(label) + '</button><span class="hint">'
            + esc((c.text || '').slice(0, 26)) + '</span></div>';
        }).join('')
      : '<div class="hint">已是顶层</div>';
    p.querySelectorAll('#chainBox [data-depth]').forEach((b) => {
      b.onclick = () => chainToParent(parseInt(b.dataset.depth, 10));
    });
  }

  function kindLabel(k) {
    return { id: 'ID', attr: '属性', tagattr: '标签+属性', class: 'class', css: 'CSS',
      xpath: 'XPath', text: '文本', relative: '相对' }[k] || k;
  }

  function sameSpec(a, b) {
    if (Array.isArray(a) && Array.isArray(b)) return a.join('|') === b.join('|');
    return a === b;
  }

  function renderCandidates() {
    const box = STATE.panel.querySelector('#candList');
    const plan = STATE.plan;
    if (!plan || !plan.candidates.length) {
      box.innerHTML = '<div class="empty">没有可用方案</div>';
      return;
    }
    box.innerHTML = plan.candidates.map((c, i) => {
      const disp = Array.isArray(c.value) ? c.value.join('  ›  ') : c.value;
      const active = sameSpec(c.value, STATE.spec);
      return '<div class="cand ' + c.level + (active ? ' active' : '') + '" data-idx="' + i + '">'
        + '<div class="cand-top"><span class="kind">' + esc(kindLabel(c.kind)) + '</span>'
        + '<span class="cand-val">' + esc(disp) + '</span>'
        + '<span class="chip grade-' + c.grade + '">' + c.grade + ' ' + c.score + '</span></div>'
        + '<div class="cand-sub"><span>' + esc(c.verdictLabel) + '</span>'
        + '<span>匹配 <b>' + (c.count < 0 ? '?' : c.count) + '</b></span>'
        + '<span>' + esc(c.why) + '</span>'
        + (c.identity === true ? '<span style="color:#22c55e">身份命中</span>' : '')
        + (c.identity === false ? '<span style="color:#ef4444">非目标</span>' : '')
        + '</div></div>';
    }).join('');

    box.querySelectorAll('.cand').forEach((node) => {
      node.onclick = () => {
        const c = plan.candidates[parseInt(node.dataset.idx, 10)];
        STATE.spec = c.value;
        highlight(c.value);
        renderCandidates();
        renderCode();
        renderUsages();
      };
    });
  }

  function renderCode() {
    const box = STATE.panel.querySelector('#codeBox');
    if (!STATE.spec) {
      box.innerHTML = '<span style="color:#8b9ac0">拾取元素后自动生成</span>';
      return;
    }
    const shadow = STATE.desc && STATE.desc.shadow;
    let text = '';
    if (STATE.codeTab === 'script') text = buildScript(STATE.spec, shadow);
    else if (STATE.codeTab === 'locate') text = locateExpr(STATE.spec, shadow);
    else text = buildUsages(STATE.spec, shadow)
      .map((u) => '# ' + u.label + ' —— ' + u.desc + '\n' + u.code).join('\n\n');
    box.innerHTML = hlPython(text);
  }

  function renderUsages() {
    // 用法列表通过「代码 → 用法」页呈现；这里保持调用点稳定
  }

  /* --------------------------------------------------------- 高亮与动作 */

  function highlight(spec) {
    const p = picker();
    if (!p) return;
    p.clearDraw();
    if (!spec) return;
    const parts = Array.isArray(spec) ? spec : [spec];
    let scope = document;
    for (let i = 0; i < parts.length - 1; i++) {
      const res = queryLoc(splitLoc(parts[i]), scope);
      if (!res.list.length) return;
      scope = res.list[0];
    }
    const res = queryLoc(splitLoc(parts[parts.length - 1]), scope);
    const rects = res.list.slice(0, 120).map((el) => {
      const r = el.getBoundingClientRect();
      return [r.left, r.top, r.width, r.height];
    }).filter((r) => r[2] > 0 && r[3] > 0);
    if (rects.length) p.drawRects(rects, { color: '#f59e0b', primary: 0 });
  }

  function clearHighlight() {
    const p = picker();
    if (p) p.clearDraw();
  }

  function findTarget(spec) {
    if (!spec) return null;
    const parts = Array.isArray(spec) ? spec : [spec];
    let scope = document;
    for (let i = 0; i < parts.length - 1; i++) {
      const res = queryLoc(splitLoc(parts[i]), scope);
      if (!res.list.length) return null;
      scope = res.list[0];
    }
    const res = queryLoc(splitLoc(parts[parts.length - 1]), scope);
    return res.list[0] || null;
  }

  function chainToParent(depth) {
    const p = picker();
    if (!p || !p.target) { toast('先拾取一个元素', 'warn'); return; }
    const d = p.chainOf(depth || 1);
    if (!d) { toast('已到顶层', 'warn'); return; }
    onPicked(d);
  }

  function applySpec(raw) {
    if (!STATE.desc) { toast('先拾取一个元素（用于身份比对）', 'warn'); return; }
    const parsed = splitLoc(raw);
    if (!parsed) { toast('无法解析该表达式', 'danger'); return; }
    const target = picker() && picker().target ? picker().target : null;
    const res = queryLoc(parsed, document);
    const scored = scoreCandidate({ k: parsed.kind, loc: raw, st: KIND_STABILITY[parsed.kind] || 50 },
                                  parsed, res, target);
    scored.why = '手动指定';
    const list = (STATE.plan.candidates || []).slice();
    const idx = list.findIndex((c) => sameSpec(c.value, raw));
    if (idx >= 0) list[idx] = scored; else list.unshift(scored);
    STATE.plan.candidates = list;
    if (scored.verdict === 'unique' || scored.verdict === 'ok') STATE.plan.recommended = scored;
    STATE.spec = raw;
    highlight(raw);
    renderElement();
    renderCandidates();
    renderCode();
  }

  function runAction(action) {
    const el = findTarget(STATE.spec);
    if (!el) { toast('找不到目标元素', 'danger'); return; }
    const box = STATE.panel.querySelector('#testResult');
    let out = null;
    try {
      switch (action) {
        case 'click':
          el.click();
          out = '已点击';
          break;
        case 'dblclick':
          el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
          out = '已双击';
          break;
        case 'contextmenu':
          el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, view: window }));
          out = '已发送右键事件';
          break;
        case 'click_js':
          el.click();
          out = '已 JS 点击';
          break;
        case 'hover':
          ['mouseover', 'mouseenter', 'mousemove'].forEach((t) =>
            el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })));
          out = '已悬停';
          break;
        case 'focus':
          el.focus();
          out = '已聚焦';
          break;
        case 'blur':
          el.blur();
          out = '已失焦';
          break;
        case 'scroll_to':
          el.scrollIntoView({ block: 'center', behavior: 'smooth' });
          out = '已滚动到可见';
          break;
        case 'check':
          if ('checked' in el) {
            el.checked = !el.checked;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            out = '勾选状态: ' + el.checked;
          } else out = '该元素不支持勾选';
          break;
        case 'input': {
          const text = STATE.panel.querySelector('#inputText').value;
          el.focus();
          if ('value' in el) {
            el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.value = text;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            out = '已写入: ' + text;
          } else if (el.isContentEditable) {
            el.textContent = text;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            out = '已写入: ' + text;
          } else out = '该元素不可输入';
          break;
        }
        case 'clear':
          if ('value' in el) {
            el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            out = '已清空';
          } else out = '该元素无 value';
          break;
        case 'get_text':
          out = el.innerText != null ? el.innerText : el.textContent;
          break;
        case 'get_html':
          out = el.outerHTML.slice(0, 1200);
          break;
        case 'get_attr': {
          const name = prompt('属性名', 'href');
          if (!name) return;
          out = name + ' = ' + el.getAttribute(name);
          break;
        }
        case 'get_attrs': {
          const attrs = {};
          for (const a of el.attributes) attrs[a.name] = a.value;
          out = JSON.stringify(attrs, null, 2);
          break;
        }
        case 'get_value':
          out = el.value !== undefined ? el.value : '(无 value)';
          break;
        case 'get_states': {
          const cs = getComputedStyle(el);
          out = JSON.stringify({
            displayed: cs.display !== 'none' && cs.visibility !== 'hidden',
            disabled: !!el.disabled, checked: !!el.checked,
            readOnly: !!el.readOnly, hidden: !!el.hidden,
            inViewport: (() => {
              const r = el.getBoundingClientRect();
              return r.top < innerHeight && r.bottom > 0 && r.left < innerWidth && r.right > 0;
            })(),
          }, null, 2);
          break;
        }
        case 'get_rect': {
          const r = el.getBoundingClientRect();
          out = JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top),
            width: Math.round(r.width), height: Math.round(r.height),
            scrollY: Math.round(scrollY) }, null, 2);
          break;
        }
        case 'get_css': {
          const name = prompt('CSS 属性名', 'color');
          if (!name) return;
          out = name + ' = ' + getComputedStyle(el).getPropertyValue(name);
          break;
        }
        default:
          out = '未知动作';
      }
      box.className = 'result';
      box.textContent = typeof out === 'string' ? out : JSON.stringify(out, null, 2);
      const code = actionToCode(action);
      if (code && !STATE.actions.includes(code)) STATE.actions.push(code);
      toast('已执行: ' + action, 'ok');
    } catch (e) {
      box.className = 'result err';
      box.textContent = '执行失败: ' + (e && e.message || e);
      toast('执行失败', 'danger');
    }
  }

  /* ------------------------------------------------------------ 捕获列表 */

  function actionToCode(action) {
    const map = {
      click: 'ele.click()', dblclick: 'ele.click.multi(times=2)',
      contextmenu: 'ele.click.right()', click_js: 'ele.click(by_js=True)',
      hover: 'ele.hover()', focus: 'ele.focus()', blur: "ele.run_js('this.blur();')",
      scroll_to: 'ele.scroll.to_see()', check: 'ele.check()',
      clear: 'ele.clear()',
    };
    if (action === 'input') {
      const text = STATE.panel.querySelector('#inputText').value;
      return "ele.clear()\nele.input('" + q(text) + "')";
    }
    return map[action] || null;
  }

  function addHistory() {
    if (!STATE.spec) { toast('还没有可加入的定位', 'warn'); return; }
    const d = STATE.desc || {};
    const best = STATE.plan && STATE.plan.recommended;
    const entry = {
      name: d.tag + (d.id ? '#' + d.id : (d.cls && d.cls.length ? '.' + d.cls[0] : '')),
      locate: locateExpr(STATE.spec, d.shadow),
      spec: STATE.spec,
      grade: best ? best.grade : '-',
      score: best ? best.score : 0,
      url: location.href,
      actions: (STATE.actions || []).slice(),
    };
    STATE.history.push(entry);
    STATE.actions = [];
    renderHistory();
    toast('已加入列表', 'ok');
  }

  function renderHistory() {
    const p = STATE.panel;
    p.querySelector('#listCount').textContent = STATE.history.length;
    p.querySelector('#listEmpty').hidden = STATE.history.length > 0;
    p.querySelector('#listMeta').textContent = STATE.history.length
      ? '共 ' + STATE.history.length + ' 项' : '';
    p.querySelector('#historyList').innerHTML = STATE.history.map((h, i) => `
      <div class="cand ok" data-idx="${i}">
        <div class="cand-top">
          <span class="cand-val">${esc(h.name)}</span>
          <span class="chip grade-${esc(h.grade || 'D')}">${esc(h.grade)} ${h.score}</span>
          <button class="btn ghost" data-del="${i}">删除</button>
        </div>
        <div class="cand-sub"><span style="font-family:Consolas,monospace">${esc(h.locate)}</span></div>
        ${(h.actions || []).length ? `<div class="cand-sub">${(h.actions || []).length} 个动作</div>` : ''}
      </div>`).join('');
    p.querySelectorAll('[data-del]').forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation();
        STATE.history.splice(parseInt(b.dataset.del, 10), 1);
        renderHistory();
      };
    });
    p.querySelectorAll('#historyList .cand').forEach((node) => {
      node.onclick = () => {
        const h = STATE.history[parseInt(node.dataset.idx, 10)];
        if (h) { STATE.spec = h.spec; highlight(h.spec); renderCode(); renderCandidates(); }
      };
    });
  }

  /* ------------------------------------------------------- 拾取事件接入 */

  /* ---------------------------------------------------------- 生命周期 */

  window.__DPC_PANEL__ = {
    toggle() {
      if (STATE.visible) api('hide'); else api('show');
    },
    show: () => api('show'),
    hide: () => api('hide'),
    state: STATE,
  };

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !msg.cmd) return;
    if (msg.cmd === 'toggle-panel') {
      window.__DPC_PANEL__.toggle();
      sendResponse({ ok: true, visible: STATE.visible });
    } else if (msg.cmd === 'toggle-pick') {
      setPick(!STATE.pickMode);
      sendResponse({ ok: true, on: STATE.pickMode });
    } else if (msg.cmd === 'ping') {
      sendResponse({ ok: true });
    }
  });

  init();
})();
