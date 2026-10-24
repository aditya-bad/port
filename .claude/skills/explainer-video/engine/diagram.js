// Diagram pieces for architecture explainers: wrapped text, a database
// cylinder, an event log, a language badge, a service box that lists its
// components, and a term card that explains a word the first time it's
// used. Like box(), each returns a group with .X .Y .W .H .cx .cy .R .B,
// starts hidden, and can be named, focused, spotlit and popped.

function bounds(g, x, y, w, h) { return Object.assign(g, { X: x, Y: y, W: w, H: h, cx: x + w / 2, cy: y + h / 2, R: x + w, B: y + h }); }

// Text broken into lines of at most n characters (SVG doesn't wrap).
function wrapLines(s, n) {
  const out = [];
  let cur = '';
  for (const w of s.split(/\s+/)) {
    if (cur && (cur + ' ' + w).length > n) { out.push(cur); cur = w; } else cur = cur ? cur + ' ' + w : w;
  }
  if (cur) out.push(cur);
  return out;
}
function wrap(p, x, y, s, n, cls = 'bs', lh = 20, anchor = 'start') {
  const g = G(p);
  wrapLines(s, n).forEach((l, i) => T(g, x, y + i * lh, l, cls, anchor));
  return g;
}

// A database: a cylinder with its name and what it holds.
function db(p, x, y, w, h, label, sub = '', o = {}) {
  const g = G(p, `db ${o.cls || ''}`);
  const ry = Math.min(12, h / 6);
  E('path', { d: `M${x} ${y + ry}V${y + h - ry}A${w / 2} ${ry} 0 0 0 ${x + w} ${y + h - ry}V${y + ry}A${w / 2} ${ry} 0 0 0 ${x} ${y + ry}Z`, class: 'db-body' }, g);
  E('path', { d: `M${x} ${y + ry}A${w / 2} ${ry} 0 0 0 ${x + w} ${y + ry}`, class: 'db-rim' }, g);
  T(g, x + w / 2, y + ry + (sub ? (h - ry) / 2 - 2 : (h - ry) / 2 + 6), label, o.lcls || 'cl');
  if (sub) wrap(g, x + w / 2, y + ry + (h - ry) / 2 + 20, sub, Math.max(8, Math.floor((w - 16) / 9.2)), 'bs', 17, 'middle');   // 13 px mono is ~9 px a letter
  op(g, 0);
  return bounds(g, x, y, w, h);
}

// An event log (Redpanda topic, NATS subject): a pipe of entries.
function topic(p, x, y, w, label, color = 'b', o = {}) {
  const g = G(p, `topic t-${color}`);
  const h = o.h || 40;
  E('rect', { x, y, width: w, height: h, rx: h / 2, class: 'topic-pipe' }, g);
  for (let i = 1; i < 6; i++) E('line', { x1: x + w - i * 16 - 8, y1: y + 7, x2: x + w - i * 16 - 8, y2: y + h - 7, class: 'topic-entry' }, g);
  T(g, x + 18, y + h / 2 + 6, label, o.lcls || 'mono', 'start');
  g.pipe = E('path', { d: `M${x + 10} ${y + h / 2}H${x + w - 10}`, class: 'ln', fill: 'none' }, g);   // for dots flowing along it
  op(g.pipe, 0);
  op(g, 0);
  return bounds(g, x, y, w, h);
}

// A small badge: a language (Rust, Go, Python…, coloured by its first word)
// or a kind (StatefulSet…, outlined).
function badge(p, x, y, text, kind = 'lang', anchor = 'end') {
  const g = G(p, `badge ${kind}${kind === 'lang' ? ' l-' + text.toLowerCase().split(/[^a-z]/)[0] : ''}`);
  const w = text.length * 8.6 + 14;
  const x0 = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x;
  E('rect', { x: x0, y: y - 15, width: w, height: 21, rx: 4 }, g);
  T(g, x0 + w / 2, y, text, 'bdg');
  return g;
}

// A service: its name, an optional nickname, its language and how it runs,
// its part of the system (a coloured band), and its components as chips.
//   svc(p, x, y, w, h, 'orders-and-risk', { nick: 'Heimdall', lang: 'Go', kind: 'StatefulSet',
//        part: 'trade' | 'lens' | 'platform' | 'infra', comps: ['intake', 'risk checks', …], cols: 2 })
// Each component chip is g.comp[name] (a box), so it can be named, focused, flashed.
function svc(p, x, y, w, h, name, o = {}) {
  const g = G(p, `svc part-${o.part || 'trade'}`);
  E('rect', { x, y, width: w, height: h, rx: 8, class: 'svc-body' }, g);
  E('rect', { x, y, width: 8, height: h, rx: 3, class: 'svc-band' }, g);
  const title = o.nick ? `${o.nick}` : name;
  T(g, x + 22, y + 32, title, 'svc-name', 'start');
  if (o.nick) T(g, x + 22 + title.length * 13.5 + 12, y + 31, name, 'svc-plain', 'start');
  let bx = x + w - 12;
  if (o.lang) { const b = badge(g, bx, y + 30, o.lang, 'lang'); bx -= o.lang.length * 8.6 + 22; }
  if (o.kind) badge(g, bx, y + 30, o.kind, 'kind');
  const dn = Math.floor((w - 40) / 8.2), dl = o.does ? wrapLines(o.does, dn).length : 0;
  if (o.does) wrap(g, x + 22, y + 60, o.does, dn, 'svc-does', 21);
  g.comp = {};
  g.top = y + (o.does ? 60 + 21 * dl : 50);                // where the space under the words starts
  const comps = o.comps || [];
  if (comps.length) {
    const cols = o.cols || 2, top = g.top;
    const gap = 8, cw = (w - 30 - gap * (cols - 1)) / cols, ch = o.ch || 34;
    comps.forEach((c, i) => {
      const cx = x + 18 + (i % cols) * (cw + gap), cy = top + Math.floor(i / cols) * (ch + gap);
      const b = box(g, cx, cy, cw, ch, c, { cls: 'chip comp', lcls: 'cmp' });
      op(b, 1);
      g.comp[c] = b;
    });
    g.top = top + Math.ceil(comps.length / cols) * (ch + gap);
  }
  op(g, 0);
  return bounds(g, x, y, w, h);
}

// A term card: the word, and what it means here, the first time it's used.
function term(p, x, y, w, word, meaning, o = {}) {
  const g = G(p, 'term');
  const lh = o.lh || 27, lines = wrapLines(meaning, Math.floor((w - 32) / (o.cw || 10.2)));
  const h = o.h || 50 + lines.length * lh + 14;
  E('rect', { x, y, width: w, height: h, rx: 8, class: 'term-card' }, g);
  E('rect', { x, y, width: w, height: 5, rx: 2, class: 'term-top' }, g);
  T(g, x + 16, y + 34, word, 'term-word', 'start');
  lines.forEach((l, i) => T(g, x + 16, y + 68 + i * lh, l, 'term-text', 'start'));
  op(g, 0);
  return bounds(g, x, y, w, h);
}
// How tall a term card will be, to lay a grid of them out.
function termH(w, meaning, o = {}) { return o.h || 50 + wrapLines(meaning, Math.floor((w - 32) / (o.cw || 10.2))).length * (o.lh || 27) + 14; }

// A box that moves and resizes every frame, for one map turning into
// another: its name, language and part can change on the way. Its name
// sits on top and its language badge under it, like a map node.
//   const m = mbox(p, 'md-ingest', 'Rust', 'trade');
//   m.set(x, y, w, h, { name: 'md', lang: 'Go', label: 1 })   // label: 0…1, fade the words while it moves
function mbox(p, name, lang, part = 'trade') {
  const g = G(p, `svc part-${part}`);
  const r = E('rect', { rx: 7, class: 'svc-body' }, g), band = E('rect', { width: 6, rx: 3, class: 'svc-band' }, g);
  const t = T(g, 0, 0, name, 'mname', 'start');
  const bg = G(g);
  let cur = { name, lang, part };
  const draw = () => { bg.innerHTML = ''; if (cur.lang) badge(bg, 0, 0, cur.lang, 'lang', 'start'); };
  draw();
  g.set = (x, y, w, h, o = {}) => {
    for (const [e, a] of [[r, { x, y, width: w, height: h }], [band, { x, y, height: h }]]) for (const n in a) e.setAttribute(n, a[n].toFixed(1));
    t.setAttribute('x', (x + 16).toFixed(1)); t.setAttribute('y', (y + Math.min(26, h / 2 + 6)).toFixed(1));   // name on top,
    bg.setAttribute('transform', `translate(${(x + 16).toFixed(1)},${(y + h - 13).toFixed(1)})`);                 // language under it
    if (o.name != null && o.name !== cur.name) { cur.name = o.name; txt(t, o.name); }
    if (o.lang !== undefined && o.lang !== cur.lang) { cur.lang = o.lang; draw(); }
    if (o.part && o.part !== cur.part) { g.classList.replace(`part-${cur.part}`, `part-${o.part}`); cur.part = o.part; }
    const lab = o.label ?? 1;
    op(t, lab); op(bg, lab);
  };
  op(g, 0);
  return g;
}

// ── maps and flows ──────────────────────────────────────────────────────
// For architecture explainers: parts as dashed frames, services as map
// nodes, labelled links between them, and a scene that follows one thing
// hop by hop (flowScene). Part colours: trade b, lens pu, platform wt;
// 'cell' is a neutral dotted frame.

// A part of the system: a dashed frame with its name in its colour (kind: trade, lens, platform, cell).
function area(p, x, y, w, h, title, kind, sub = '') {
  const g = G(p);
  E('rect', { x, y, width: w, height: h, rx: 14, class: `part-frame ${kind}` }, g);
  if (title) T(g, x + 18, y + 30, title, `part-t ${kind}`, 'start');
  if (sub) T(g, x + 18 + title.length * 13.8 + 16, y + 29, sub, 'bs', 'start');
  op(g, 0);
  return bounds(g, x, y, w, h);
}

// A service on a map: its name (or nickname), its language, how it runs.
function node(p, x, y, w, name, lang, part, o = {}) {
  const h = o.h || (o.sub ? 96 : 64);
  const g = G(p, `svc part-${part}`);
  E('rect', { x, y, width: w, height: h, rx: 8, class: 'svc-body' }, g);
  E('rect', { x, y, width: 6, height: h, rx: 3, class: 'svc-band' }, g);
  T(g, x + 18, y + 27, o.nick || name, o.nick ? 'mnick' : 'mname', 'start');
  let bx = x + 18;
  for (const [s, kind] of [[lang, 'lang'], [o.nick ? name : o.kind, 'kind']]) {
    if (!s) continue;
    badge(g, bx, y + 51, s, kind, 'start');
    bx += s.length * 8.6 + 22;
  }
  if (o.sub) T(g, x + 18, y + h - 14, o.sub, 'bs', 'start');
  op(g, 0);
  return bounds(g, x, y, w, h);
}

// A flow, told hop by hop. build(L, B, M) draws the boxes and returns
// { edges: [[path, fromBox, toBox], …], extra(t, rt) }. Each step names
// the edges a packet travels while it's said, and the packet's label.
//   flowScene('An order', build, [['What is said.', { focus }, [0, 1], 'intent'], …])
// A travelled edge keeps a stream of dots on it, so the path stays alive.
function flowScene(title, build, narr) {
  const S = beats(narr.map(([text, o]) => [text, o || {}]));
  scene({ title, dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const f = build(L, B, M, S);
    const boxes = [...new Set(f.edges.flatMap(([, a, b]) => [a, b]).concat(f.more || []))];
    const pk = narr.map(([, , , lab]) => lab ? packet(M, lab, f.color || 'b') : dot(M, f.color || 'b', 9));
    const first = new Map();                              // edge → when it is first travelled
    narr.forEach(([, , es = []], i) => es.forEach((e, j) => { if (!first.has(e)) first.set(e, S.at[i] + 0.3 + j * (S.to[i] - S.at[i] - 0.6) / es.length); }));
    const streams = f.edges.map(([path], i) => stream(M, path, f.edges[i][3] || f.color || 'b', 2, 1.6, 5));
    return (t, rt) => {
      pops(boxes, t, 0.2, Math.max(0.08, (S.to[0] - 0.8) / boxes.length));
      f.edges.forEach(([path, a, b], i) => {
        const t0 = first.get(i);
        if (t0 == null) { show(path, t, 0.4 + i * 0.1); streams[i](rt, 1e9); return; }
        show(path, t, t0, t0 + 0.4);
        streams[i](rt, t > t0 + 1.2 ? t0 + 1.2 : 1e9);
      });
      const hot = new Set();                              // the box a packet is arriving at
      narr.forEach(([, , es = []], i) => {
        if (!es.length) { op(pk[i], 0); return; }
        const seg = (S.to[i] - S.at[i] - 0.6) / es.length;
        // along each edge in turn, keeping off the boxes at both ends
        let j = -1;
        es.forEach((e, n) => { const a = S.at[i] + 0.3 + n * seg; if (t >= a && t <= a + seg) j = n; });
        if (j >= 0) { const q = k(t, S.at[i] + 0.3 + j * seg, S.at[i] + 0.3 + (j + 1) * seg); placeOn(pk[i], f.edges[es[j]][0], lerp(0.14, 0.86, ease(q))); op(pk[i], Math.min(1, q * 6, (1 - q) * 6)); }
        else op(pk[i], 0);
        if (j >= 0 && k(t, S.at[i] + 0.3 + j * seg, S.at[i] + 0.3 + (j + 1) * seg) > 0.8) hot.add(f.edges[es[j]][2]);
      });
      boxes.forEach(b => b.classList.toggle('hot', hot.has(b)));
      if (f.extra) f.extra(t, rt);
    };
  });
}

// A line from one thing's edge to another's, curved, with a label on it.
function edgePt(b, s, d = 0) {
  return s === 'r' ? [b.R, b.cy + d] : s === 'l' ? [b.X, b.cy + d] : s === 't' ? [b.cx + d, b.Y] : [b.cx + d, b.B];
}
function link(p, a, b, color = 'ink', o = {}) {
  let [s1, s2] = o.sides || (b.X >= a.R ? ['r', 'l'] : b.R <= a.X ? ['l', 'r'] : b.Y >= a.B ? ['b', 't'] : ['t', 'b']);
  let [x1, y1] = edgePt(a, s1, o.d1 || 0), [x2, y2] = edgePt(b, s2, o.d2 || 0);
  const pull = 6;                                         // stop short so the arrowhead shows
  if (s2 === 'l') x2 -= pull; if (s2 === 'r') x2 += pull; if (s2 === 't') y2 -= pull; if (s2 === 'b') y2 += pull;
  const hz = s1 === 'r' || s1 === 'l';
  const d = o.d || (hz ? `M${x1} ${y1}C${(x1 + x2) / 2} ${y1} ${(x1 + x2) / 2} ${y2} ${x2} ${y2}`
                       : `M${x1} ${y1}C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`);
  const l = line(p, d, color, { dash: o.dash });
  if (o.label) {
    const m = l.getPointAtLength(l.getTotalLength() * (o.at ?? 0.5));
    l._lab = T(p, m.x + (o.lx || 0), m.y + (o.ly ?? -22), o.label, o.lcls || 'hop', o.anchor || 'middle');   // clear of a packet on the line
    op(l._lab, 0);
  }
  return l;
}
function show(l, t, a, b = a + 0.6) { reveal(l, t, a, b); if (l._lab) op(l._lab, k(t, b - 0.2, b + 0.2)); }
// An invisible path through points, for a dot to travel along.
function route(p, pts) { const e = E('path', { d: 'M' + pts.map(q => q.join(' ')).join('L'), class: 'ln', fill: 'none' }, p); op(e, 0); return e; }
const mid = b => [b.cx, b.cy];
// Light up a row of things one after another between a and b.
function walk(list, t, a, b) { const d = (b - a) / list.length; list.forEach((el, i) => flash(el, t, a + i * d, d * 0.95)); }
// Pop a list of things in turn.
function pops(list, t, a, gap = 0.12) { list.forEach((el, i) => pop(el, t, a + i * gap)); }
// A dot that travels a route over and over while the voice talks (rt), from t0.
function loopDot(p, path, color, period = 2.4, n = 1) { return stream(p, path, color, n, period, 7); }

