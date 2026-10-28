/*
 * 注入到目标页面的元素拾取器。
 * 职责：
 *   1. 悬停高亮 + 信息徽标（真实浏览器窗口内拾取）
 *   2. 生成元素描述符与候选定位表达式（供 Python 侧用 DrissionPage 复核）
 *   3. 变更计数器（rev）—— 后端据此决定是否抓帧，避免无脑轮询截图
 *   4. 绘制高亮矩形（几何坐标由 Python 侧计算，本脚本只负责画）
 * 约束：自身绝不改变页面布局，overlay 一律 pointer-events:none。
 */
(function () {
  'use strict';

  var VER = 13;
  if (window.__DPC__ && window.__DPC__.ver === VER) {
    return;
  }

  var LAYER_ID = '__dpc_layer__';
  var MAX_EVENTS = 40;
  var TEXT_CAP = 600;

  var S = {
    ver: VER,
    rev: 1,
    pick: false,
    events: [],
    target: null,
    allCache: null,
    layer: null,
    drawn: [],
    obs: null
  };
  window.__DPC__ = S;

  /* ------------------------------------------------------------------ 工具 */

  function bump(n) {
    S.rev += (n || 1);
  }

  function cap(str, n) {
    str = String(str == null ? '' : str);
    return str.length > n ? str.slice(0, n) + '…' : str;
  }

  function textOf(el) {
    var t = (el.innerText != null && el.innerText !== '') ? el.innerText : el.textContent;
    return cap((t || '').replace(/\s+/g, ' ').trim(), TEXT_CAP);
  }

  function ownTextOf(el) {
    var out = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3) {
        out += n.nodeValue;
      }
    }
    return cap(out.replace(/\s+/g, ' ').trim(), 200);
  }

  function rawTextOf(el) {
    // 保留换行：<br> / 块级子元素造成的分行是文本定位能否命中的关键
    var t = (el.innerText != null && el.innerText !== '') ? el.innerText : el.textContent;
    return cap((t || ''), TEXT_CAP);
  }

  function attrsOf(el) {
    var out = {};
    var a = el.attributes || [];
    for (var i = 0; i < a.length; i++) {
      if (a[i].name === 'style' && a[i].value.length > 200) {
        out[a[i].name] = cap(a[i].value, 200);
      } else {
        out[a[i].name] = cap(a[i].value, 300);
      }
    }
    return out;
  }

  function rectOf(el) {
    var r = el.getBoundingClientRect();
    return [round2(r.left), round2(r.top), round2(r.width), round2(r.height)];
  }

  function round2(v) {
    return Math.round(v * 100) / 100;
  }

  function cssEscape(v) {
    if (window.CSS && CSS.escape) { return CSS.escape(v); }
    return String(v).replace(/[^a-zA-Z0-9_\u00a0-\uffff-]/g, '\\$&');
  }

  function allElements() {
    if (S.allCache && S.allCache.length) { return S.allCache; }
    var list = Array.prototype.slice.call(document.querySelectorAll('*'));
    // 页面过大时限制扫描范围，避免候选校验卡顿
    if (list.length > 8000) { list = list.slice(0, 8000); }
    S.allCache = list;
    return list;
  }

  function walk(root, fn) {
    // 遍历含 shadow root 的整棵可见树
    var nodes = root.querySelectorAll('*');
    for (var i = 0; i < nodes.length; i++) {
      fn(nodes[i]);
      if (nodes[i].shadowRoot) { walk(nodes[i].shadowRoot, fn); }
    }
  }

  function isVisible(el) {
    var cs = window.getComputedStyle(el);
    if (!cs) { return false; }
    if (cs.display === 'none' || cs.visibility === 'hidden') { return false; }
    if (parseFloat(cs.opacity) === 0) { return false; }
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function isEditable(el) {
    var t = el.tagName.toLowerCase();
    if (t === 'textarea' || t === 'select') { return true; }
    if (t === 'input') {
      var ty = (el.getAttribute('type') || 'text').toLowerCase();
      return ['text', 'password', 'email', 'search', 'tel', 'url', 'number'].indexOf(ty) >= 0;
    }
    return el.isContentEditable === true;
  }

  function htmlTagOf(el) {
    var t = el.tagName.toLowerCase();
    if (t === 'a') { return 'a'; }
    if (t === 'input') { return 'input'; }
    if (t === 'iframe' || t === 'frame') { return 'frame'; }
    return t;
  }

  /* --------------------------------------------------------------- overlay */

  function layer() {
    var l = document.getElementById(LAYER_ID);
    if (l && l.isConnected) { S.layer = l; return l; }
    l = document.createElement('div');
    l.id = LAYER_ID;
    l.setAttribute('data-dpc', '1');
    l.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;' +
      'pointer-events:none;margin:0;padding:0;border:0;background:transparent;';
    (document.body || document.documentElement).appendChild(l);
    S.layer = l;
    return l;
  }

  var HOVER_STYLE = {
    box: 'position:fixed;pointer-events:none;box-sizing:border-box;border-radius:2px;',
    badge: 'position:fixed;pointer-events:none;box-sizing:border-box;max-width:520px;overflow:hidden;' +
      'white-space:nowrap;text-overflow:ellipsis;font:11px/16px Consolas,Menlo,monospace;' +
      'color:#e5f2ff;background:rgba(12,20,32,.93);border:1px solid #2196f3;border-radius:3px;' +
      'padding:2px 6px;box-shadow:0 2px 10px rgba(0,0,0,.4);'
  };

  function makeDiv(kind) {
    var hasLayer = !!layer();
    var el = document.getElementById('__dpc_' + kind + '__');
    if (el && el.isConnected) { return el; }
    el = document.createElement('div');
    el.id = '__dpc_' + kind + '__';
    el.setAttribute('data-dpc', '1');
    el.style.cssText = HOVER_STYLE[kind];
    if (!hasLayer && !document.body) {
      // 极端早期注入：等 body 出现
      setTimeout(function () { layer().appendChild(el); }, 0);
    } else {
      layer().appendChild(el);
    }
    return el;
  }

  function drawHover(rect, label, color) {
    var box = makeDiv('box');
    var color2 = color || '#2196f3';
    box.style.display = 'block';
    box.style.border = '2px solid ' + color2;
    box.style.background = hexA(color2, 0.16);
    box.style.left = rect[0] + 'px';
    box.style.top = rect[1] + 'px';
    box.style.width = Math.max(rect[2], 1) + 'px';
    box.style.height = Math.max(rect[3], 1) + 'px';
    var badge = makeDiv('badge');
    badge.style.display = 'block';
    badge.textContent = label;
    var bh = 20;
    var by = rect[1] - bh - 3;
    if (by < 2) { by = rect[1] + rect[3] + 3; }
    if (by + bh > window.innerHeight) { by = Math.max(2, window.innerHeight - bh - 2); }
    badge.style.left = Math.max(2, Math.min(rect[0], window.innerWidth - 60)) + 'px';
    badge.style.top = by + 'px';
  }

  function hexA(hex, a) {
    var h = hex.replace('#', '');
    if (h.length === 3) { h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]; }
    var n = parseInt(h, 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  function hideHover() {
    ['box', 'badge'].forEach(function (k) {
      var el = document.getElementById('__dpc_' + k + '__');
      if (el) { el.style.display = 'none'; }
    });
  }

  /* ------------------------------------------------------------- 描述符 */

  function shadowChain(el) {
    var chain = [];
    var node = el;
    var depth = 0;
    while (node && depth < 8) {
      var root = node.getRootNode ? node.getRootNode() : null;
      if (root && root.host) {
        chain.unshift({
          host: root.host.tagName.toLowerCase(),
          id: root.host.id || '',
          cls: (root.host.getAttribute('class') || '').split(/\s+/).filter(Boolean),
          selector: simplePath(root.host)
        });
        node = root.host;
      } else {
        break;
      }
      depth++;
    }
    return chain;
  }

  function simplePath(el) {
    // 尽量短的 CSS 路径（仅供展示 / 兜底，权威校验仍在 Python 侧）
    if (el.nodeType !== 1) { return null; }
    if (el === document.documentElement) { return 'html'; }
    var parts = [];
    var cur = el;
    var guard = 0;
    while (cur && cur.nodeType === 1 && guard++ < 40) {
      var seg = cur.tagName.toLowerCase();
      if (cur.id) {
        var byId = null;
        try { byId = document.querySelectorAll('#' + cssEscape(cur.id)); } catch (e) { byId = null; }
        if (byId && byId.length === 1 && byId[0] === cur) {
          parts.unshift('#' + cur.id);
          return parts.join('>');
        }
      }
      var parent = cur.parentElement;
      if (parent) {
        var same = 0;
        var idx = 0;
        for (var i = 0; i < parent.children.length; i++) {
          if (parent.children[i].tagName === cur.tagName) {
            same++;
            if (parent.children[i] === cur) { idx = same; }
          }
        }
        if (same > 1) { seg += ':nth-of-type(' + idx + ')'; }
      }
      parts.unshift(seg);
      if (parent === document.body || !parent) {
        if (parent === document.body) { parts.unshift('body'); }
        break;
      }
      cur = parent;
    }
    return parts.join('>');
  }

  function xpathOf(el) {
    if (el.nodeType !== 1) { return null; }
    if (el.id && !/[\s'"]/.test(el.id)) {
      return '//*[@id="' + el.id + '"]';
    }
    var parts = [];
    var cur = el;
    var guard = 0;
    while (cur && cur.nodeType === 1 && guard++ < 30) {
      var tag = cur.tagName.toLowerCase();
      var idx = 1;
      var sib = cur.previousElementSibling;
      while (sib) {
        if (sib.tagName === cur.tagName) { idx++; }
        sib = sib.previousElementSibling;
      }
      parts.unshift(tag + '[' + idx + ']');
      var root = cur.getRootNode ? cur.getRootNode() : null;
      if (root && root.host) {
        if (root.host.id && !/[\s'"]/.test(root.host.id)) {
          parts.unshift('//*[@id="' + root.host.id + '"]/');
          return '/' + parts.join('/').replace('///', '//');
        }
        cur = root.host;
        continue;
      }
      if (cur.parentElement === document.documentElement) {
        break;
      }
      cur = cur.parentElement;
    }
    return '/html/' + parts.join('/');
  }

  var ATTR_PRIORITY = [
    'data-testid', 'data-test', 'data-id', 'data-uid', 'data-key', 'data-name',
    'name', 'id', 'aria-label', 'placeholder', 'title', 'alt', 'href', 'value', 'for', 'type'
  ];

  function candidateList(el) {
    var tag = el.tagName.toLowerCase();
    var cands = [];
    var attrs = attrsOf(el);
    var doc = el.ownerDocument || document;
    var seen = {};

    function push(kind, loc, probe, why, stability) {
      if (!loc || seen[loc]) { return; }
      seen[loc] = 1;
      cands.push({ k: kind, loc: loc, probe: probe, why: why, st: stability });
    }

    function uniqueCss(sel) {
      try {
        var got = el.getRootNode().querySelectorAll(sel);
        return got.length === 1 && got[0] === el;
      } catch (e) {
        return false;
      }
    }

    // 1) id
    if (attrs.id && uniqueCss('#' + cssEscape(attrs.id))) {
      push('id', '#' + attrs.id, { t: 'css', v: '#' + cssEscape(attrs.id) }, '唯一 id（最稳定）', 100);
    }

    // 2) 稳定的自定义属性 / 表单属性
    var attrKeys = Object.keys(attrs).filter(function (k) {
      if (k === 'style' || k === 'class' || k === 'id') { return false; }
      var v = attrs[k];
      if (v === '' || v.length > 60 || /[\r\n\t]/.test(v)) { return false; }
      return k.indexOf('data-') === 0 || ATTR_PRIORITY.indexOf(k) >= 0;
    });
    attrKeys.sort(function (a, b) {
      var ia = ATTR_PRIORITY.indexOf(a);
      var ib = ATTR_PRIORITY.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    attrKeys.slice(0, 3).forEach(function (k) {
      var v = attrs[k];
      var st = k.indexOf('data-') === 0 ? 88 : 72;
      push('attr', '@' + k + '=' + v, { t: 'attr', v: [k, v] }, '属性 ' + k + '=' + cap(v, 24), st);
      if (k === 'name' || k === 'value') {
        push('tagattr', 'tag:' + tag + '@' + k + '=' + v, { t: 'tagattr', v: [tag, k, v] },
          '标签+属性 ' + tag + '@' + k, st - 6);
      }
    });

    // 3) class（DrissionPage 的 @@class 是整串精确匹配，多 class 时用 css）
    var cls = (attrs['class'] || '').split(/\s+/).filter(Boolean);
    var excluded = ['active', 'on', 'hover', 'focus', 'selected', 'open', 'show', 'hidden', 'disabled'];
    var stableCls = cls.filter(function (c) { return excluded.indexOf(c) < 0 && !/^\d/.test(c); });
    if (stableCls.length) {
      if (stableCls.length === 1 && uniqueCss('.' + cssEscape(stableCls[0]))) {
        push('class', '.' + stableCls[0], { t: 'css', v: '.' + cssEscape(stableCls[0]) }, '唯一 class', 70);
      }
      var cssGrab = tag + stableCls.map(function (c) { return '.' + cssEscape(c); }).join('');
      if (uniqueCss(cssGrab)) {
        push('css', 'css:' + cssGrab, { t: 'css', v: cssGrab }, '标签+class 组合（唯一）', 66);
      }
      // 整串 class 精确匹配（DrissionPage 语法）
      push('tagattr', 'tag:' + tag + '@@class=' + attrs['class'],
        { t: 'tagattr', v: [tag, 'class', attrs['class']] }, '整串 class 精确匹配', 55);
    }

    // 4) 纯文本
    //    叶子优先；短容器也纳入。重前端站点（Angular / B站 / 淘宝）的 class 多为
    //    自动生成，文本往往是这类元素唯一稳定的锚点。
    var own = ownTextOf(el);
    var leafLike = el.children.length === 0;
    if ((own || textOf(el)) && (own || textOf(el)).length <= 40) {
      // 优先用完整可见文本（innerText 会在块级子元素之间保留空格，
      // 比拼接直接文本节点的 ownText 更接近用户看到的文字）
      var fullText = rawTextOf(el);
      var textForLoc = leafLike ? (own || fullText) : fullText;
      if (leafLike) {
        if (textForLoc) {
          push('text', 'text=' + textForLoc, { t: 'text', v: textForLoc },
            '精确文本（易随文案变化）', 40);
          if (textForLoc.length >= 4) {
            var head = textForLoc.slice(0, Math.max(2, Math.floor(textForLoc.length * 0.6)));
            push('text', 'text=' + head, { t: 'text', v: head },
              '文本前缀匹配（允许部分变化）', 34);
          }
        }
      } else if (el.children.length <= 3 && fullText) {
        // DrissionPage 的 text= 要求「元素全部文本」精确相等，含子元素时基本用不到；
        // tx= 是包含匹配。注意：文本被 <br>/块级子元素拆成多行时，跨行匹配无效，
        // 所以这里按行拆开，只用「单行片段」做候选（实测 tx=Productivity 可命中 h2）。
        var segs = [];
        String(fullText).split(/[\r\n]+/).forEach(function (line) {
          line = line.trim();
          if (line.length >= 2 && line.length <= 40) { segs.push(line); }
        });
        if (segs.length && segs.length <= 3) {
          push('text', 'tx=' + segs[0], { t: 'text', v: segs[0] },
            segs.length > 1 ? '容器首行文本匹配（多行结构）' : '容器文本包含匹配', 44);
          // 跨行合并的文本在 <br> 结构下匹配不到，不再生成这种无效候选
        }
      }
    }

    // 5) CSS 路径兜底
    var sp = simplePath(el);
    if (sp) {
      if (uniqueCss(sp)) {
        push('css', 'css:' + sp, { t: 'css', v: sp }, '结构路径（唯一）', 50);
      }
      push('css', 'css:' + sp, { t: 'css', v: sp }, '结构路径', 30);
    }

    // 6) XPath 兜底（由 Python 侧补充 DrissionPage 生成的 xpath）
    var xp = xpathOf(el);
    if (xp) {
      push('xpath', 'xpath:' + xp, { t: 'xpath', v: xp }, 'XPath 路径（结构依赖强）', 34);
    }

    return cands;
  }

  function describe(el, withCands) {
    if (!el || el.nodeType !== 1) { return null; }
    var attrs = attrsOf(el);
    var cs = window.getComputedStyle(el);
    var rect = rectOf(el);
    var parent = el.parentElement;
    var root = el.getRootNode ? el.getRootNode() : null;
    var d = {
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      cls: (attrs['class'] || '').split(/\s+/).filter(Boolean),
      attrs: attrs,
      text: textOf(el),
      ownText: ownTextOf(el),
      rect: rect,
      docRect: [round2(rect[0] + window.scrollX), round2(rect[1] + window.scrollY), rect[2], rect[3]],
      inView: rect[1] + rect[3] > 0 && rect[1] < window.innerHeight && rect[0] + rect[2] > 0 && rect[0] < window.innerWidth,
      visible: isVisible(el),
      editable: isEditable(el),
      value: (el.value === undefined ? '' : cap(String(el.value), 200)),
      style: {
        display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
        color: cs.color, backgroundColor: cs.backgroundColor,
        fontSize: cs.fontSize, position: cs.position
      },
      states: {
        disabled: !!el.disabled,
        checked: !!el.checked,
        selected: !!el.selected,
        readOnly: !!el.readOnly,
        multiple: !!el.multiple,
        hidden: !!el.hidden
      },
      html: cap(el.outerHTML, 3000),
      innerHtml: cap(el.innerHTML, 3000),
      cssPath: simplePath(el),
      xpath: xpathOf(el),
      url: location.href,
      title: document.title,
      viewport: [window.innerWidth, window.innerHeight, window.devicePixelRatio],
      scroll: [window.scrollX, window.scrollY],
      shadow: shadowChain(el),
      inShadow: !!(root && root.host),
      parent: parent ? {
        tag: parent.tagName.toLowerCase(), id: parent.id || '',
        cls: (parent.getAttribute('class') || '').split(/\s+/).filter(Boolean)
      } : null,
      iframe: el.tagName.toLowerCase() === 'iframe' || el.tagName.toLowerCase() === 'frame'
    };
    if (withCands !== false) {
      d.cands = candidateList(el);
    }
    return d;
  }

  /* ------------------------------------------------------------- 候选校验 */

  function identityList(probe) {
    var list = [];
    var i;
    if (probe.t === 'css') {
      try {
        list = Array.prototype.slice.call(S.target.getRootNode().querySelectorAll(probe.v));
      } catch (e) { list = []; }
    } else if (probe.t === 'xpath') {
      try {
        var it = document.evaluate(probe.v, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (i = 0; i < it.snapshotLength; i++) { list.push(it.snapshotItem(i)); }
      } catch (e) { list = []; }
    } else if (probe.t === 'attr') {
      var all = allElements();
      for (i = 0; i < all.length; i++) {
        if (all[i].getAttribute(probe.v[0]) === probe.v[1]) { list.push(all[i]); }
      }
    } else if (probe.t === 'tagattr') {
      var all2 = allElements();
      for (i = 0; i < all2.length; i++) {
        var e = all2[i];
        if (e.tagName.toLowerCase() === probe.v[0] && e.getAttribute(probe.v[1]) === probe.v[2]) {
          list.push(e);
        }
      }
    } else if (probe.t === 'text') {
      var all3 = allElements();
      for (i = 0; i < all3.length; i++) {
        var e3 = all3[i];
        if (e3.children.length === 0 && String(e3.textContent || '').trim() === probe.v) {
          list.push(e3);
        }
      }
    }
    return list;
  }

  S.verifyCands = function (cands) {
    var out = [];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i];
      var res = { count: -1, hits: false, err: null };
      try {
        var list = identityList(c.probe);
        var idx = list.indexOf(S.target);
        res.count = list.length;
        res.hits = idx === 0;
        res.idx = idx;
        res.rect = list.length && list[0] ? rectOf(list[0]) : null;
      } catch (e) {
        res.err = String(e);
      }
      out.push(res);
    }
    return out;
  };

  /* --------------------------------------------------------------- 拾取 */

  function elementAt(x, y) {
    var el = document.elementFromPoint(x, y);
    // document.elementFromPoint 只返回 Shadow 宿主，这里逐层穿透 open shadow root
    var guard = 0;
    while (el && el.shadowRoot && guard++ < 8) {
      var inner = null;
      try { inner = el.shadowRoot.elementFromPoint(x, y); } catch (e) { inner = null; }
      if (!inner || inner === el) { break; }
      el = inner;
    }
    return el;
  }

  function labelFor(el, d) {
    var parts = [htmlTagOf(el)];
    if (d.id) { parts.push('#' + d.id); }
    if (d.cls && d.cls.length) { parts.push('.' + d.cls.slice(0, 3).join('.')); }
    var best = null;
    for (var i = 0; i < (d.cands || []).length; i++) {
      if (!best || d.cands[i].st > best.st) { best = d.cands[i]; }
    }
    return parts.join('') + (best ? '   ' + best.loc : '');
  }

  S.hover = function (x, y) {
    var el = elementAt(x, y);
    if (!el || el.id === LAYER_ID || (el.getAttribute && el.getAttribute('data-dpc'))) { return null; }
    var d = describe(el, false);
    if (!d) { return null; }
    S.hoverTarget = el;
    drawHover(d.rect, labelFor(el, describe(el, true)), '#2196f3');
    return d;
  };

  S.hideHover = hideHover;

  S.pickAt = function (x, y, queue) {
    var el = elementAt(x, y);
    if (!el) { return null; }
    if (el.id === LAYER_ID || (el.getAttribute && el.getAttribute('data-dpc'))) { return null; }
    S.target = el;
    S.allCache = null;
    var d = describe(el, true);
    d.chain = ancestorChain(el);
    d.probe = { x: x, y: y };
    if (d.cands) { d.candCheck = S.verifyCands(d.cands); }
    if (queue !== false) {
      S.events.push({ kind: 'pick', t: Date.now(), d: d });
      if (S.events.length > MAX_EVENTS) { S.events.splice(0, S.events.length - MAX_EVENTS); }
    }
    drawHover(d.rect, labelFor(el, d), '#22c55e');
    return d;
  };

  function ancestorChain(el) {
    var chain = [];
    var cur = el.parentElement;
    var guard = 0;
    while (cur && guard++ < 8) {
      var t = cur.tagName.toLowerCase();
      if (t === 'html') { break; }
      chain.push({
        tag: t,
        id: cur.id || '',
        cls: (cur.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 4),
        rect: rectOf(cur),
        text: cap((cur.innerText || '').replace(/\s+/g, ' ').trim(), 40),
        cands: candidateList(cur).slice(0, 4)
      });
      cur = cur.parentElement;
    }
    return chain;
  }

  S.chainOf = function (depth) {
    var el = S.target;
    if (!el || !el.isConnected) { return null; }
    var cur = el;
    for (var i = 0; i < depth && cur.parentElement; i++) { cur = cur.parentElement; }
    if (cur.tagName.toLowerCase() === 'html') { return null; }
    S.target = cur;
    S.allCache = null;
    var d = describe(cur, true);
    d.chain = ancestorChain(cur);
    if (d.cands) { d.candCheck = S.verifyCands(d.cands); }
    drawHover(d.rect, labelFor(cur, d), '#22c55e');
    return d;
  };

  /* --------------------------------------------------------- 高亮绘制 */

  S.drawRects = function (rects, opts) {
    opts = opts || {};
    var l = layer();
    var color = opts.color || '#f59e0b';
    S.drawn.forEach(function (el) {
      if (el && el.parentNode) { el.parentNode.removeChild(el); }
    });
    S.drawn = [];
    for (var i = 0; i < rects.length; i++) {
      var r = rects[i];
      var box = document.createElement('div');
      var isPrimary = i === (opts.primary === undefined ? 0 : opts.primary);
      box.setAttribute('data-dpc', '1');
      box.style.cssText = 'position:fixed;pointer-events:none;box-sizing:border-box;border-radius:2px;' +
        'border:2px ' + (isPrimary ? 'solid ' : 'dashed ') + (isPrimary ? color : '#64748b') + ';' +
        'background:' + hexA(isPrimary ? color : '#64748b', isPrimary ? 0.18 : 0.10) + ';' +
        'left:' + r[0] + 'px;top:' + r[1] + 'px;width:' + Math.max(r[2], 1) + 'px;height:' + Math.max(r[3], 1) + 'px;z-index:2147483646;';
      l.appendChild(box);
      S.drawn.push(box);
      if (rects.length > 1 && opts.tag !== false) {
        var tag = document.createElement('div');
        tag.setAttribute('data-dpc', '1');
        tag.textContent = String(i + 1);
        tag.style.cssText = 'position:fixed;pointer-events:none;font:10px/14px Consolas,monospace;color:#fff;' +
          'background:' + (isPrimary ? color : '#64748b') + ';border-radius:2px;padding:0 3px;' +
          'left:' + r[0] + 'px;top:' + Math.max(0, r[1] - 15) + 'px;z-index:2147483646;';
        l.appendChild(tag);
        S.drawn.push(tag);
      }
    }
    return S.drawn.length;
  };

  S.clearDraw = function () {
    S.drawn.forEach(function (el) {
      if (el && el.parentNode) { el.parentNode.removeChild(el); }
    });
    S.drawn = [];
    hideHover();
    return true;
  };

  S.drawHoverRect = function (rect, label, color) {
    drawHover(rect, label || '', color || '#2196f3');
    return true;
  };

  /* ------------------------------------------------------------- 变更监听 */

  function attach() {
    if (!document.documentElement) { return; }
    try {
      if (S.obs) { S.obs.disconnect(); }
      S.obs = new MutationObserver(function (muts) {
        var n = 0;
        for (var i = 0; i < muts.length && i < 40; i++) {
          // 自己画的覆盖层变化不算页面变化，否则帧泵会被自身高亮刷屏
          var t = muts[i].target;
          if (t && (t.id === LAYER_ID || (t.closest && t.closest('#' + LAYER_ID)))) { continue; }
          n++;
        }
        if (n) { bump(n); S.allCache = null; }
      });
      // 注意：不要传 attributeFilter: null，字典转换会抛 TypeError 导致监听失效
      S.obs.observe(document.documentElement, {
        childList: true, subtree: true, attributes: true, characterData: true
      });
    } catch (e) {
      if (window.console) { console.warn('[DPC] MutationObserver 监听失败', e); }
    }
    window.addEventListener('scroll', function () { bump(1); }, true);
    window.addEventListener('resize', function () { bump(3); });
    window.addEventListener('popstate', function () { bump(5); });
    window.addEventListener('hashchange', function () { bump(5); });
    window.addEventListener('load', function () { bump(2); });
    // SPA 路由
    ['pushState', 'replaceState'].forEach(function (m) {
      var orig = history[m];
      if (typeof orig === 'function') {
        history[m] = function () {
          var rv = orig.apply(this, arguments);
          bump(5);
          return rv;
        };
      }
    });
  }

  /* ------------------------------------------------------------- 拾取模式 */

  function onMove(e) {
    if (!S.pick) { return; }
    S.hover(e.clientX, e.clientY);
  }

  function swallow(e) {
    if (!S.pick) { return; }
    e.preventDefault();
    e.stopPropagation();
    if (e.stopImmediatePropagation) { e.stopImmediatePropagation(); }
  }

  function onClick(e) {
    if (!S.pick) { return; }
    swallow(e);
    S.pickAt(e.clientX, e.clientY, true);
  }

  function onKey(e) {
    if (e.key === 'Escape' && S.pick) {
      S.setPick(false);
      S.events.push({ kind: 'pickmode', t: Date.now(), on: false });
    }
  }

  S.setPick = function (on) {
    S.pick = !!on;
    if (!S.pick) { hideHover(); }
    return S.pick;
  };

  window.addEventListener('mousemove', onMove, true);
  window.addEventListener('mousedown', swallow, true);
  window.addEventListener('mouseup', swallow, true);
  window.addEventListener('click', onClick, true);
  window.addEventListener('dblclick', swallow, true);
  window.addEventListener('contextmenu', swallow, true);
  window.addEventListener('keydown', onKey, true);

  /* ----------------------------------------------------------------- drain */

  S.drain = function () {
    var events = S.events;
    S.events = [];
    return {
      rev: S.rev,
      url: location.href,
      title: document.title,
      sx: window.scrollX,
      sy: window.scrollY,
      iw: window.innerWidth,
      ih: window.innerHeight,
      dpr: window.devicePixelRatio,
      pick: S.pick,
      sha: location.hash || '',
      rdy: document.readyState,
      events: events
    };
  };

  S.info = function () {
    return { ver: VER, rev: S.rev, url: location.href, pick: S.pick, ready: document.readyState };
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }
})();
