/* ProContract project page: the teaser timeline, the run tiles and the paired viewer. Plain DOM, no libraries.
   Data: window.PC_RUNS (static/js/runs.js), three paired runs taken from the runs' own records. */
(function () {
  'use strict';
  var RUNS = window.PC_RUNS || [];
  var NS = 'http://www.w3.org/2000/svg';
  var C = { pc: '#0b6e62', pcBar: '#cfe6e1', no: '#bf4b30', sub: '#3949a3', har: '#6e7781', ink: '#1f2328', muted: '#656d76', faint: '#8c959f', dots: '#c9ced4' };

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
  function $(id) { return document.getElementById(id); }
  function clock(t) {
    var m = Math.round(t);
    return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + ('0' + (m % 60)).slice(-2) + ' min';
  }
  function pct(p) { return (p === 100 ? '100' : p.toFixed(1)) + '%'; }
  function stateLine(st) {
    return 'standing=outstanding version=' + st.version + ' generation=' + st.generation + ' candidate=' + (st.candidate || 'none') + ' support=none';
  }
  function label(e, lane) {
    if (lane === 'cx') return e.kind === 'done' ? 'declares the goal complete' : 'progress note';
    return { issue: 'the Contract is issued', propose: e.same ? 'the same artifact is submitted again' : 'the worker ends its turn, artifact ' + e.n + ' is frozen', defeat: 'counterexample to artifact ' + e.n, inconclusive: 'the verifier cannot decide', handover: 'work goes to a fresh worker', end: 'six hours are used' }[e.kind];
  }

  /* One figure, three sizes. Codex above, ProContract below, one shared time axis.
     mode 'hero': the large teaser, outcomes on the lane's label row, main events clickable.
     mode 'mini': a tile. mode 'full': the viewer, every event selectable. */
  var SIZE = {
    hero: { H: 232, yC: 62, yP: 152, yA: 198, bar: 14, name: 17, out: 23, small: 14.5, ms: 1.55, L: 2, R: 2 },
    mini: { H: 122, yC: 30, yP: 78, yA: 100, bar: 8, name: 12.5, out: 0, small: 12, ms: 1, L: 2, R: 9 },
    full: { H: 142, yC: 36, yP: 90, yA: 116, bar: 9, name: 13.5, out: 0, small: 12.5, ms: 1.05, L: 10, R: 14 }
  };
  function timeline(run, W, mode) {
    var z = SIZE[mode], narrow = W < 560;
    if (mode === 'hero' && narrow) z = { H: 204, yC: 56, yP: 132, yA: 172, bar: 12, name: 15, out: 17, small: 13, ms: 1.25, L: 2, R: 8 };
    var x = function (t) { return z.L + t / run.limit * (W - z.L - z.R); };
    var defeats = run.pc.events.filter(function (e) { return e.kind === 'defeat'; }).length;
    var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + z.H, width: W, height: z.H, role: 'img', 'aria-label': 'Codex declares the task done at ' + clock(run.codex.minutes) + ' and scores ' + pct(run.codex.pass) + ' of hidden tests. ProContract works for six hours, receives ' + defeats + ' counterexamples from the verifier, and scores ' + pct(run.pc.pass) + '.' });
    var yC = z.yC, yP = z.yP, yA = z.yA, hb = z.bar / 2;
    // lane names, and on the teaser the outcome on the same row
    svg.appendChild(s('text', { x: z.L, y: yC - hb - 12, 'font-size': z.name, 'font-weight': 600, style: 'fill:' + C.har }, 'Codex /goal'));
    svg.appendChild(s('text', { x: z.L, y: yP - hb - 14, 'font-size': z.name, 'font-weight': 700, style: 'fill:' + C.pc }, 'ProContract'));
    if (z.out) {
      var o1 = s('text', { x: W - z.R, y: yC - hb - 12, 'text-anchor': 'end', 'font-size': z.small, style: 'fill:' + C.muted });
      o1.appendChild(s('tspan', { 'font-size': z.out, 'font-weight': 700, style: 'fill:' + C.ink + ';letter-spacing:-.02em' }, pct(run.codex.pass)));
      o1.appendChild(s('tspan', { dx: 7 }, narrow ? 'hidden tests' : 'of hidden tests'));
      svg.appendChild(o1);
      var o2 = s('text', { x: W - z.R, y: yP - hb - 14, 'text-anchor': 'end', 'font-size': z.small, style: 'fill:' + C.muted });
      o2.appendChild(s('tspan', { 'font-size': z.out, 'font-weight': 700, style: 'fill:' + C.pc + ';letter-spacing:-.02em' }, pct(run.pc.pass)));
      o2.appendChild(s('tspan', { dx: 7 }, narrow ? 'hidden tests' : 'of hidden tests'));
      svg.appendChild(o2);
    }
    // Codex: the bar ends where the run ends; the unused allowance is a dotted line
    var xe = x(run.codex.minutes);
    svg.appendChild(s('line', { x1: xe + 6, y1: yC, x2: x(run.limit), y2: yC, stroke: C.dots, 'stroke-width': mode === 'hero' ? 2 : 1.5, 'stroke-dasharray': '0.1 ' + (mode === 'hero' ? 7 : 5), 'stroke-linecap': 'round' }));
    svg.appendChild(s('rect', { x: x(0), y: yC - hb, width: Math.max(2, xe - x(0)), height: z.bar, rx: hb, fill: C.har }));
    svg.appendChild(s('line', { x1: xe, y1: yC - hb - 5, x2: xe, y2: yC + hb + 5, stroke: C.ink, 'stroke-width': mode === 'hero' ? 2.4 : 1.8, 'stroke-linecap': 'round' }));
    var doneText = '“done” at ' + clock(run.codex.minutes) + (mode !== 'mini' && !narrow ? ': the run ends' : '');
    var dt = s('text', { x: xe + (mode === 'hero' ? 13 : 9), y: yC + z.small * 0.36, 'font-size': z.small, 'font-weight': mode === 'hero' ? 500 : 400, style: 'fill:' + C.ink + ';paint-order:stroke;stroke:' + (mode === 'hero' ? '#f6f7f9' : '#fff') + ';stroke-width:5px;stroke-linejoin:round' }, doneText);
    svg.appendChild(dt);
    // ProContract: the bar runs the whole allowance
    svg.appendChild(s('rect', { x: x(0), y: yP - hb, width: x(run.limit) - x(0), height: z.bar, rx: hb, fill: C.pcBar }));
    // axis
    svg.appendChild(s('line', { x1: x(0), y1: yA, x2: x(run.limit), y2: yA, stroke: C.dots, 'stroke-width': 1 }));
    for (var hr = 0; hr <= 6; hr++) {
      svg.appendChild(s('line', { x1: x(hr * 60), y1: yA, x2: x(hr * 60), y2: yA + 5, stroke: C.dots, 'stroke-width': 1 }));
      if (mode !== 'mini' || hr % 2 === 0) svg.appendChild(s('text', { x: x(hr * 60), y: yA + (mode === 'hero' ? 22 : 18), 'font-size': mode === 'hero' ? 13 : 11.5, 'text-anchor': hr === 0 ? 'start' : hr === 6 ? 'end' : 'middle', style: 'fill:' + C.faint }, hr === 0 ? '0' : hr + ' h'));
    }
    function glyph(g, kind, cx, cy) {
      var m = z.ms;
      if (kind === 'propose') g.appendChild(s('path', { d: 'M' + cx + ' ' + (cy - 7.5 * m) + 'L' + (cx + 6.8 * m) + ' ' + (cy + 5.5 * m) + 'H' + (cx - 6.8 * m) + 'z', fill: C.sub, stroke: '#fff', 'stroke-width': mode === 'hero' ? 1.5 : 1, 'stroke-linejoin': 'round' }));
      else if (kind === 'defeat') {
        g.appendChild(s('path', { d: 'M' + (cx - 5 * m) + ' ' + (cy - 5 * m) + 'l' + 10 * m + ' ' + 10 * m + 'M' + (cx + 5 * m) + ' ' + (cy - 5 * m) + 'l' + (-10 * m) + ' ' + 10 * m, stroke: '#fff', 'stroke-width': 2.4 * m + 2.4, fill: 'none', 'stroke-linecap': 'round' }));
        g.appendChild(s('path', { d: 'M' + (cx - 5 * m) + ' ' + (cy - 5 * m) + 'l' + 10 * m + ' ' + 10 * m + 'M' + (cx + 5 * m) + ' ' + (cy - 5 * m) + 'l' + (-10 * m) + ' ' + 10 * m, stroke: C.no, 'stroke-width': 2.4 * m, fill: 'none', 'stroke-linecap': 'round' }));
      }
      else if (kind === 'inconclusive') g.appendChild(s('circle', { cx: cx, cy: cy, r: 4.6 * m, fill: '#fff', stroke: C.muted, 'stroke-width': 1.5 }));
      else if (kind === 'handover') g.appendChild(s('line', { x1: cx, y1: cy + hb + 4, x2: cx, y2: cy + hb + 12, stroke: C.har, 'stroke-width': 2, 'stroke-linecap': 'round' }));
      else if (kind === 'note') g.appendChild(s('line', { x1: cx, y1: cy - hb + 1.5, x2: cx, y2: cy + hb - 1.5, stroke: '#fff', 'stroke-width': 1.5 }));
    }
    var show = { hero: { propose: 1, defeat: 1, inconclusive: 1 }, mini: { propose: 1, defeat: 1, inconclusive: 1 }, full: { issue: 1, propose: 1, defeat: 1, inconclusive: 1, handover: 1, end: 1, note: 1, done: 1 } }[mode];
    function lane(events, lname, cy) {
      events.forEach(function (e, i) {
        if (!show[e.kind]) return;
        var active = mode !== 'mini';
        var g = s('g', active ? { 'class': 'mk', tabindex: 0, role: 'button', 'data-lane': lname, 'data-i': i, 'aria-label': (lname === 'cx' ? 'Codex, ' : 'ProContract, ') + label(e, lname) + ' at ' + clock(e.t) } : {});
        var cx = x(e.t), hy = e.kind === 'handover' ? cy + hb + 8 : cy;
        if (active) g.appendChild(s('circle', { 'class': 'hit', cx: cx, cy: hy, r: e.kind === 'note' ? 7 : (mode === 'hero' ? 15 : 11), fill: 'transparent' }));
        glyph(g, e.kind, cx, cy);
        svg.appendChild(g);
      });
    }
    lane(run.pc.events, 'pc', yP);
    if (mode === 'full') lane(run.codex.events, 'cx', yC);
    if (mode === 'hero') {   // the "done" tick opens Codex's final message
      var last = run.codex.events.length - 1;
      var g = s('g', { 'class': 'mk', tabindex: 0, role: 'button', 'data-lane': 'cx', 'data-i': last, 'aria-label': 'Codex declares the goal complete at ' + clock(run.codex.minutes) });
      g.appendChild(s('circle', { 'class': 'hit', cx: xe, cy: yC, r: 15, fill: 'transparent' }));
      svg.appendChild(g);
    }
    if (mode === 'full') svg.appendChild(s('line', { id: 'v-cursor', x1: 0, y1: 8, x2: 0, y2: yA, stroke: C.ink, 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: .5 }));
    svg._x = x;
    return svg;
  }
  function bindMarkers(svg, pick) {
    Array.prototype.forEach.call(svg.querySelectorAll('.mk'), function (g) {
      var go = function () { pick(g.getAttribute('data-lane'), +g.getAttribute('data-i'), g); };
      g.addEventListener('click', go);
      g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
    });
  }

  /* ---- teaser under the title ---- */
  var tabs = $('teaser-tabs'), stage = $('teaser-fig'), shown = 0;
  function drawTeaser() {
    if (!stage || !RUNS.length) return;
    var run = RUNS[shown], W = Math.max(280, Math.floor(stage.clientWidth) || 980);
    stage.textContent = '';
    var svg = timeline(run, W, 'hero');
    stage.appendChild(svg);
    bindMarkers(svg, function (lane, i, g) { openRun(shown, g, lane, i); });
    $('teaser-cap').textContent = 'Task: rebuild a program that ' + run.what + ' (' + run.repo + '). The same model, GPT-6 Luna, and six hours allowed in each run. Hidden tests are run after a run ends; no agent sees them. Select a marker to read what was said.';
    Array.prototype.forEach.call(tabs.children, function (b, k) { b.setAttribute('aria-selected', k === shown ? 'true' : 'false'); b.tabIndex = k === shown ? 0 : -1; });
  }
  if (tabs && RUNS.length) {
    RUNS.forEach(function (run, k) {
      var b = h('button', { 'class': 'tab', type: 'button', role: 'tab', text: run.id });
      b.addEventListener('click', function () { shown = k; drawTeaser(); });
      b.addEventListener('keydown', function (ev) {
        if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
        shown = (shown + (ev.key === 'ArrowRight' ? 1 : RUNS.length - 1)) % RUNS.length; drawTeaser(); tabs.children[shown].focus();
      });
      tabs.appendChild(b);
    });
    $('teaser-open').addEventListener('click', function () { openRun(shown, $('teaser-open')); });
    drawTeaser();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(drawTeaser);
  }

  /* ---- tiles ---- */
  var tiles = $('run-tiles');
  if (tiles) RUNS.forEach(function (run, idx) {
    var b = h('button', { 'class': 'run', type: 'button', 'aria-haspopup': 'dialog' }, [
      h('span', { 'class': 'task', text: run.repo }),
      h('h3', { text: run.head }),
      h('p', { text: 'Task: rebuild a program that ' + run.what + '. ' + run.blurb })
    ]);
    b.appendChild(timeline(run, 300, 'mini'));
    b.appendChild(h('div', { 'class': 'scores' }, [
      h('div', {}, [h('b', { text: pct(run.codex.pass) }), 'Codex, hidden tests']),
      h('div', { 'class': 'pc' }, [h('b', { text: pct(run.pc.pass) }), 'ProContract, hidden tests'])
    ]));
    b.appendChild(h('span', { 'class': 'open', text: 'Inspect trajectory →' }));
    b.addEventListener('click', function () { openRun(idx, b); });
    tiles.appendChild(b);
  });

  /* ---- viewer ---- */
  var dlg = $('viewer'), cur = null, opener = null;
  function openRun(idx, from, lane, i) {
    var run = RUNS[idx], merged = [];
    run.codex.events.forEach(function (e, k) { merged.push({ lane: 'cx', i: k, t: e.t }); });
    run.pc.events.forEach(function (e, k) { merged.push({ lane: 'pc', i: k, t: e.t }); });
    merged.sort(function (a, b) { return a.t - b.t || (a.lane === 'cx' ? -1 : 1); });
    cur = { idx: idx, run: run, merged: merged, at: 0 };
    merged.forEach(function (m, k) {
      if (lane ? (m.lane === lane && m.i === i) : (m.lane === 'cx' && run.codex.events[m.i].kind === 'done')) cur.at = k;   // default: the moment Codex says "done"
    });
    $('v-title').textContent = run.repo;
    $('v-sub').textContent = 'Task: rebuild a program that ' + run.what + '. GPT-6 Luna, six hours allowed in each run. Codex: ' + run.codex.requests + ' model requests. ProContract: ' + run.pc.requests + '.';
    if (from) opener = from;
    if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', ''); }
    dlg.scrollTop = 0;
    drawTime(); show();
    try { history.replaceState(null, '', '#run-' + run.id); } catch (e) {}
  }
  function find(lane, i) { for (var k = 0; k < cur.merged.length; k++) if (cur.merged[k].lane === lane && cur.merged[k].i === i) return k; return -1; }
  function latest(events, t) { var j = -1; events.forEach(function (e, i) { if (e.t <= t + 1e-9) j = i; }); return j; }
  function drawTime() {
    var box = $('v-time'); box.textContent = '';
    var W = Math.max(300, Math.floor(box.clientWidth - 2) || 1100);
    var svg = timeline(cur.run, W, 'full');
    box.appendChild(svg); cur.svg = svg;
    bindMarkers(svg, function (lane, i) { var k = find(lane, i); if (k >= 0) { cur.at = k; show(); } });
  }
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
  function evalLine(p) {
    return h('div', { 'class': 'eval' }, ['Evaluation after the run: ', h('b', { text: pct(p) + ' of hidden tests' }), '. The agent never sees these tests or this number.']);
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
  function kline(e, prev) {
    var d = h('div', { 'class': 'kline' });
    if (e.cmd) {
      d.appendChild(h('span', { 'class': 'lab', text: 'Kernel: ' + e.cmd + ' from the ' + ({ issuer: 'issuer, for the user', executor: 'worker', verifier: 'verifier' }[e.role]) + ': admitted' }));
      var c = h('code'); c.style.whiteSpace = 'pre-wrap';
      if (prev) { c.appendChild(h('s', { text: stateLine(prev) })); c.appendChild(document.createTextNode('\n')); }
      c.appendChild(document.createTextNode(stateLine(e.state)));
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
    $('v-other').addEventListener('click', function () { openRun((cur.idx + 1) % RUNS.length); });
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
    var want = (location.hash || '').replace('#run-', '');
    RUNS.forEach(function (r, k) { if (r.id === want) openRun(k); });
  }
  var rz;
  window.addEventListener('resize', function () {
    clearTimeout(rz);
    rz = setTimeout(function () { drawTeaser(); if (cur && dlg.open) { drawTime(); show(); } }, 120);
  });

  /* ---- citation ---- */
  var copy = $('copy-bib');
  if (copy) copy.addEventListener('click', function () {
    var text = $('bib').textContent, done = function () { copy.textContent = 'Copied'; setTimeout(function () { copy.textContent = 'Copy'; }, 1600); };
    var select = function () { var r = document.createRange(); r.selectNodeContents($('bib')); var sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, select); else select();
  });
})();
