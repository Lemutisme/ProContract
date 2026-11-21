/* ProContract project page: trajectory tiles and the paired viewer. Plain DOM, no libraries.
   Data: window.PC_RUNS (static/js/runs.js), three paired runs taken from the runs' own records. */
(function () {
  'use strict';
  var RUNS = window.PC_RUNS || [];
  var NS = 'http://www.w3.org/2000/svg';
  var C = { kernel: '#0F766E', kernelBg: '#CFE7E2', defeat: '#B54732', propose: '#3949A3', harness: '#64748B', track: '#E6EAE7', ink: '#18222D', muted: '#5C6670', rule: '#D5DCD8' };

  function h(tag, attrs, kids) {
    var n = document.createElement(tag), k;
    for (k in attrs || {}) { if (k === 'text') n.textContent = attrs[k]; else if (k === 'class') n.className = attrs[k]; else n.setAttribute(k, attrs[k]); }
    (kids || []).forEach(function (c) { if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  }
  function s(tag, attrs, text) {
    var n = document.createElementNS(NS, tag), k;
    for (k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function clock(t) {
    var m = Math.round(t);
    return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + ('0' + (m % 60)).slice(-2) + ' min';
  }
  function stateLine(st) {
    return 'standing=outstanding version=' + st.version + ' generation=' + st.generation + ' candidate=' + (st.candidate || 'none') + ' support=none';
  }

  /* ---- one timeline: Codex above, ProContract below, one shared time axis ---- */
  function timeline(run, W, full) {
    var H = full ? 138 : 108, L = full ? 10 : 2, R = full ? 14 : 9;
    var yC = full ? 34 : 27, yP = full ? 86 : 70, yA = full ? 112 : 88;
    var x = function (t) { return L + t / run.limit * (W - L - R); };
    var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img', 'aria-label': 'Codex stops at ' + clock(run.codex.minutes) + '; ProContract works for six hours with ' + run.pc.events.filter(function (e) { return e.kind === 'defeat'; }).length + ' counterexamples from the verifier.' });
    var fs = full ? 13 : 12;
    // Codex lane: the bar ends where the run ends
    svg.appendChild(s('text', { x: L, y: yC - 12, 'font-size': fs, 'font-weight': 600, style: 'fill:' + C.harness }, 'Codex /goal'));
    svg.appendChild(s('line', { x1: x(run.codex.minutes), y1: yC, x2: x(run.limit), y2: yC, stroke: C.rule, 'stroke-width': 1.5, 'stroke-dasharray': '1 5', 'stroke-linecap': 'round' }));
    svg.appendChild(s('rect', { x: x(0), y: yC - 4, width: Math.max(2, x(run.codex.minutes) - x(0)), height: 8, rx: 2, fill: C.harness }));
    svg.appendChild(s('line', { x1: x(run.codex.minutes), y1: yC - 10, x2: x(run.codex.minutes), y2: yC + 10, stroke: C.ink, 'stroke-width': 1.8 }));
    svg.appendChild(s('text', { x: x(run.codex.minutes) + 8, y: yC + 4.5, 'font-size': fs, style: 'fill:' + C.ink }, '“done” at ' + clock(run.codex.minutes) + (W > 520 ? ': the run ends' : '')));
    // ProContract lane
    svg.appendChild(s('text', { x: L, y: yP - 14, 'font-size': fs, 'font-weight': 600, style: 'fill:' + C.kernel }, 'ProContract'));
    svg.appendChild(s('rect', { x: x(0), y: yP - 4, width: x(run.limit) - x(0), height: 8, rx: 2, fill: C.kernelBg }));
    // axis
    svg.appendChild(s('line', { x1: x(0), y1: yA, x2: x(run.limit), y2: yA, stroke: C.muted, 'stroke-width': 1 }));
    for (var hr = 0; hr <= 6; hr++) {
      svg.appendChild(s('line', { x1: x(hr * 60), y1: yA, x2: x(hr * 60), y2: yA + 4, stroke: C.muted, 'stroke-width': 1 }));
      if (full || hr % 2 === 0) svg.appendChild(s('text', { x: x(hr * 60), y: yA + 17, 'font-size': 11.5, 'text-anchor': hr === 0 ? 'start' : hr === 6 ? 'end' : 'middle', style: 'fill:' + C.muted, 'font-family': 'IBM Plex Mono, monospace' }, hr === 0 ? '0' : hr + ' h'));
    }
    function glyph(g, kind, cx, cy) {
      if (kind === 'propose') g.appendChild(s('path', { d: 'M' + cx + ' ' + (cy - 7) + 'L' + (cx + 6.5) + ' ' + (cy + 5.5) + 'H' + (cx - 6.5) + 'z', fill: C.propose }));
      else if (kind === 'defeat') g.appendChild(s('path', { d: 'M' + (cx - 5) + ' ' + (cy - 5) + 'l10 10M' + (cx + 5) + ' ' + (cy - 5) + 'l-10 10', stroke: C.defeat, 'stroke-width': 2.4, fill: 'none', 'stroke-linecap': 'round' }));
      else if (kind === 'inconclusive') g.appendChild(s('circle', { cx: cx, cy: cy, r: 4.6, fill: '#fff', stroke: C.muted, 'stroke-width': 1.5 }));
      else if (kind === 'handover') g.appendChild(s('line', { x1: cx, y1: cy + 7, x2: cx, y2: cy + 16, stroke: C.harness, 'stroke-width': 2 }));
      else if (kind === 'note') g.appendChild(s('line', { x1: cx, y1: cy - 4, x2: cx, y2: cy + 4, stroke: '#fff', 'stroke-width': 1.6 }));
      else if (kind === 'issue' || kind === 'end') g.appendChild(s('rect', { x: cx - 1.5, y: cy - 8, width: 3, height: 16, fill: C.kernel }));
      else if (kind === 'done') g.appendChild(s('rect', { x: cx - 1.5, y: cy - 9, width: 3, height: 18, fill: C.ink }));
    }
    function lane(events, lname, cy) {
      events.forEach(function (e, i) {
        if (!full && (e.kind === 'handover' || e.kind === 'note' || e.kind === 'issue' || e.kind === 'end' || e.kind === 'done')) return;
        var g = s('g', full ? { 'class': 'mk', tabindex: 0, role: 'button', 'data-lane': lname, 'data-i': i, 'aria-label': (lname === 'cx' ? 'Codex, ' : 'ProContract, ') + label(e, lname) + ' at ' + clock(e.t) } : {});
        var cx = x(e.t), hy = e.kind === 'handover' ? cy + 11.5 : cy;
        if (full) g.appendChild(s('circle', { 'class': 'hit', cx: cx, cy: hy, r: e.kind === 'note' ? 7 : 11, fill: 'transparent' }));
        glyph(g, e.kind, cx, cy);
        svg.appendChild(g);
      });
    }
    lane(run.pc.events, 'pc', yP);
    if (full) lane(run.codex.events, 'cx', yC);
    if (full) svg.appendChild(s('line', { id: 'v-cursor', x1: 0, y1: 6, x2: 0, y2: yA, stroke: C.ink, 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: .55 }));
    svg._x = x;
    return svg;
  }
  function label(e, lname) {
    if (lname === 'cx') return e.kind === 'done' ? 'declares the goal complete' : 'progress note';
    return { issue: 'the Contract is issued', propose: e.same ? 'the same artifact is submitted again' : 'the worker ends its turn, artifact ' + e.n + ' is frozen', defeat: 'counterexample to artifact ' + e.n, inconclusive: 'the verifier cannot decide', handover: 'work goes to a fresh worker', end: 'six hours are used' }[e.kind];
  }

  /* ---- tiles ---- */
  var tiles = document.getElementById('run-tiles');
  if (tiles) RUNS.forEach(function (run, idx) {
    var b = h('button', { 'class': 'run', type: 'button', 'aria-haspopup': 'dialog' }, [
      h('span', { 'class': 'task', text: run.repo }),
      h('h3', { text: run.head }),
      h('p', { text: 'Task: rebuild a program that ' + run.what + '. ' + run.blurb })
    ]);
    b.appendChild(timeline(run, 328, false));
    b.appendChild(h('div', { 'class': 'scores' }, [
      h('div', {}, [h('b', { text: run.codex.pass.toFixed(1) + '%' }), 'Codex, hidden tests']),
      h('div', { 'class': 'pc' }, [h('b', { text: (run.pc.pass === 100 ? '100' : run.pc.pass.toFixed(1)) + '%' }), 'ProContract, hidden tests'])
    ]));
    b.appendChild(h('span', { 'class': 'open', text: 'Inspect trajectory →' }));
    b.addEventListener('click', function () { openRun(idx, b); });
    tiles.appendChild(b);
  });

  /* ---- viewer ---- */
  var dlg = document.getElementById('viewer'), cur = null, opener = null;
  function $(id) { return document.getElementById(id); }
  function openRun(idx, from) {
    var run = RUNS[idx];
    var merged = [];
    run.codex.events.forEach(function (e, i) { merged.push({ lane: 'cx', i: i, t: e.t }); });
    run.pc.events.forEach(function (e, i) { merged.push({ lane: 'pc', i: i, t: e.t }); });
    merged.sort(function (a, b) { return a.t - b.t || (a.lane === 'cx' ? -1 : 1); });
    cur = { idx: idx, run: run, merged: merged, at: 0 };
    // start at the moment Codex declares the task complete
    merged.forEach(function (m, k) { if (m.lane === 'cx' && run.codex.events[m.i].kind === 'done') cur.at = k; });
    $('v-title').textContent = run.repo;
    $('v-sub').textContent = 'Task: rebuild a program that ' + run.what + '. GPT-6 Luna, six hours allowed in each run. Codex: ' + run.codex.requests + ' model requests. ProContract: ' + run.pc.requests + '.';
    if (from) opener = from;
    if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', ''); }
    drawTime(); show();
    try { history.replaceState(null, '', '#run-' + run.id); } catch (e) {}
  }
  function drawTime() {
    var box = $('v-time'); box.textContent = '';
    var W = Math.max(300, Math.floor(box.clientWidth - 2) || 1100);
    var svg = timeline(cur.run, W, true);
    box.appendChild(svg); cur.svg = svg;
    Array.prototype.forEach.call(svg.querySelectorAll('.mk'), function (g) {
      var pick = function () { var k = find(g.getAttribute('data-lane'), +g.getAttribute('data-i')); if (k >= 0) { cur.at = k; show(); } };
      g.addEventListener('click', pick);
      g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(); } });
    });
  }
  function find(lane, i) { for (var k = 0; k < cur.merged.length; k++) if (cur.merged[k].lane === lane && cur.merged[k].i === i) return k; return -1; }
  function latest(events, t) { var j = -1; events.forEach(function (e, i) { if (e.t <= t + 1e-9) j = i; }); return j; }

  function show() {
    var m = cur.merged[cur.at], run = cur.run, T = m.t;
    $('v-at').textContent = clock(T);
    $('v-prev').disabled = cur.at === 0; $('v-next').disabled = cur.at === cur.merged.length - 1;
    Array.prototype.forEach.call(cur.svg.querySelectorAll('.mk'), function (g) {
      g.classList.toggle('sel', g.getAttribute('data-lane') === m.lane && +g.getAttribute('data-i') === m.i);
    });
    var cx = cur.svg._x(T), line = cur.svg.querySelector('#v-cursor');
    line.setAttribute('x1', cx); line.setAttribute('x2', cx);
    paneCodex($('v-cx'), run, m.lane === 'cx' ? m.i : latest(run.codex.events, T), T, m.lane === 'cx');
    panePC($('v-pc'), run, m.lane === 'pc' ? m.i : latest(run.pc.events, T), T, m.lane === 'pc');
  }
  function head(box, who, title, e, selected) {
    box.textContent = '';
    box.appendChild(h('div', { 'class': 'who', text: who }));
    box.appendChild(h('h4', { text: title }));
    box.appendChild(h('div', { 'class': 'when', text: (selected ? 'at ' : 'latest event, at ') + clock(e.t) }));
  }
  function paneCodex(box, run, i, T, selected) {
    var e = run.codex.events[i < 0 ? 0 : i], over = T > run.codex.minutes + 1e-9;
    head(box, 'Codex /goal', e.kind === 'done' ? 'The agent declares the goal complete' : 'The agent reports progress', e, selected);
    box.appendChild(h('blockquote', { text: e.text }));
    box.appendChild(h('div', { 'class': 'src', text: e.kind === 'done' ? 'The agent’s final message. Its goal is marked complete and the run ends.' : 'The agent’s own note while working.' }));
    if (over || e.kind === 'done') {
      box.appendChild(h('div', { 'class': 'ended', text: 'Run ended at ' + clock(run.codex.minutes) + ', with ' + clock(run.limit - run.codex.minutes) + ' of the six hours unused.' }));
      box.appendChild(evalLine(run.codex.pass));
    }
  }
  function evalLine(p) {
    var d = h('div', { 'class': 'eval' }, ['Evaluation after the run: ', h('b', { text: (p === 100 ? '100' : p.toFixed(1)) + '% of hidden tests' }), '. The agent never sees these tests or this number.']);
    return d;
  }
  function kline(e, prev) {
    var d = h('div', { 'class': 'kline' });
    if (e.cmd) {
      d.appendChild(h('span', { 'class': 'lab', text: 'Kernel: ' + e.cmd + ' from the ' + ({ issuer: 'issuer, for the user', executor: 'worker', verifier: 'verifier' }[e.role]) + ': admitted' }));
      var c = h('code');
      if (prev) { c.appendChild(h('s', { text: stateLine(prev) })); c.appendChild(document.createTextNode('\n')); }
      c.appendChild(document.createTextNode(stateLine(e.state)));
      c.style.whiteSpace = 'pre-wrap';
      d.appendChild(c);
    } else {
      d.appendChild(h('span', { 'class': 'lab', text: 'Kernel: no request, nothing recorded' }));
      d.appendChild(h('code', { text: stateLine(e.state) }));
    }
    return d;
  }
  function between(b) {
    if (!b || (!b.check && !b.diagnose)) return null;
    var parts = [];
    if (b.check) parts.push(b.check + (b.check === 1 ? ' run of the public checks' : ' runs of the public checks'));
    if (b.diagnose) parts.push(b.diagnose + (b.diagnose === 1 ? ' focused question' : ' focused questions') + ' to a reviewer');
    return h('div', { 'class': 'src', text: 'Before this, on the frozen copy: ' + parts.join(' and ') + '.' });
  }
  function panePC(box, run, i, T, selected) {
    var e = run.pc.events[i < 0 ? 0 : i], prev = i > 0 ? run.pc.events[i - 1].state : null;
    var title = { issue: 'The Contract is issued', propose: e.same ? 'The same artifact is submitted for a full verification' : 'The worker ends its turn. Artifact ' + e.n + ' is frozen.', defeat: 'The verifier finds a counterexample to artifact ' + e.n, inconclusive: 'The verifier cannot decide', handover: 'Work goes to a fresh worker', end: 'The six hours are used' }[e.kind];
    head(box, 'ProContract', title, e, selected);
    if (e.kind === 'propose') {
      if (e.said) { box.appendChild(h('blockquote', { text: e.said })); box.appendChild(h('div', { 'class': 'src', text: 'The worker’s closing message. Under the kernel it is a submission: the workspace is frozen as a candidate and the task stays outstanding.' })); }
      else box.appendChild(h('div', { 'class': 'ended', text: 'No new work. The harness submits the artifact it already holds, so that the verifier judges it in full.' }));
    } else if (e.kind === 'defeat') {
      (e.cex || []).forEach(function (c) { box.appendChild(h('blockquote', { 'class': 'cex', text: c })); });
      var st = e.steps ? ' Checks on the frozen copy: ' + (e.steps.pass || 0) + ' passed, ' + (e.steps.fail || 0) + ' failed.' : '';
      box.appendChild(h('div', { 'class': 'src', text: 'The verifier’s finding, recorded with the artifact it concerns.' + st }));
    } else if (e.kind === 'inconclusive') {
      box.appendChild(h('blockquote', { text: e.text }));
      box.appendChild(h('div', { 'class': 'src', text: 'The verifier gives neither Support nor a counterexample. The artifact keeps its place and the task stays outstanding.' }));
    } else if (e.kind === 'handover') {
      box.appendChild(h('blockquote', { 'class': 'inst', text: e.text }));
      box.appendChild(h('div', { 'class': 'src', text: 'The harness’s instruction to the next worker, written from the verifier’s findings. The worker changes; the Contract does not.' }));
    } else {
      box.appendChild(h('div', { 'class': 'ended', text: e.text }));
    }
    var bt = between(e.between); if (bt) box.appendChild(bt);
    box.appendChild(kline(e, e.cmd ? prev : null));
    if (e.kind === 'end') {
      box.appendChild(h('div', { 'class': 'src', text: run.pc.candidates + ' different artifacts were submitted and no Support was recorded. The Contract is outstanding, as it was at the start.' }));
      box.appendChild(evalLine(run.pc.pass));
    }
  }
  function step(d) { if (!cur) return; var k = cur.at + d; if (k >= 0 && k < cur.merged.length) { cur.at = k; show(); } }
  if (dlg) {
    $('v-prev').addEventListener('click', function () { step(-1); });
    $('v-next').addEventListener('click', function () { step(1); });
    $('v-other').addEventListener('click', function () { openRun((cur.idx + 1) % RUNS.length); dlg.scrollTop = 0; });
    $('v-close').addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('click', function (ev) { if (ev.target === dlg) dlg.close(); });
    dlg.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowRight') { ev.preventDefault(); step(1); }
      else if (ev.key === 'ArrowLeft') { ev.preventDefault(); step(-1); }
    });
    dlg.addEventListener('close', function () {
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
      if (opener && opener.focus) opener.focus();
      cur = null;
    });
    var rz;
    window.addEventListener('resize', function () { clearTimeout(rz); rz = setTimeout(function () { if (cur && dlg.open) { drawTime(); show(); } }, 120); });
    var want = (location.hash || '').replace('#run-', '');
    RUNS.forEach(function (r, i) { if (r.id === want) openRun(i); });
  }

  /* ---- citation ---- */
  var copy = $('copy-bib');
  if (copy) copy.addEventListener('click', function () {
    var text = $('bib').textContent, done = function () { copy.textContent = 'Copied'; setTimeout(function () { copy.textContent = 'Copy'; }, 1600); };
    var select = function () { var r = document.createRange(); r.selectNodeContents($('bib')); var sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, select); else select();
  });
})();
