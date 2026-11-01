// The video's scenes, drawn from the story (app/video/story.py): one
// drawer per scene kind. A drawer gets its group, its scene and the
// engine's step helpers, builds once, and returns (t, rt, j) => draw:
//   t   scene time (each step's animation runs over its span)
//   rt  real seconds into the scene (pulses that keep going while it's said)
//   j   the step being said
// cx.cue(t, j, a, b) is step j's animation, eased 0→1 over the part a..b.
const DRAW = {};
window.DRAW = DRAW;
const st = () => STORY;
const S = () => STORY.series;
// rounded half to even, as the story's words are (Python's round), so a caption and the picture agree
const rnd = v => { const f = Math.floor(v), d = v - f; return Math.abs(d - 0.5) < 1e-9 ? (f % 2 === 0 ? f : f + 1) : Math.round(v); };
const inr = v => (v == null ? '—' : `${v < 0 ? '−' : ''}₹${rnd(Math.abs(v)).toLocaleString('en-IN')}`);
const sinr = v => (v == null ? '—' : `${rnd(v) > 0 ? '+' : rnd(v) < 0 ? '−' : ''}₹${rnd(Math.abs(v)).toLocaleString('en-IN')}`);
const px = v => (v == null ? '—' : `₹${Math.abs(v - Math.round(v)) < 0.005 ? Math.round(v).toLocaleString('en-IN') : v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const tone = v => (v > 0 ? 'good' : v < 0 ? 'bad' : '');
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
// IST wall time from an ISO string (the page may run in any zone)
const ist = s => new Date(new Date(s).getTime() + 330 * 60000);
const hm = s => { const d = ist(s); return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`; };
const dayOf = s => { const d = ist(s); return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()}`; };
let uid = 0;

function chip(p, x, y, s, cls = '') {
  const g = G(p, `chip ${cls}`);
  const w = s.length * 10.5 + 28;
  E('rect', { x, y: y - 24, width: w, height: 34, rx: 17 }, g);
  T(g, x + w / 2, y - 1, s, 'chipt');
  g.w = w;
  return g;
}

// ── a chart over the series ────────────────────────────────────────────
// box {x, y, w, h}; keys: what to draw ('idx', 'pnl', …); i0..i1 the steps.
// .reveal(i) shows it up to step i (a fraction is fine), .head the dot there.
function chart(p, box, i0, i1, o = {}) {
  const s = S(), key = o.key || 'idx';
  const vals = [];
  for (const k2 of [key, ...(o.also || [])]) for (let i = i0; i <= i1; i++) if (s[k2][i] != null) vals.push(s[k2][i]);   // every line drawn fits
  for (const v of o.extra || []) if (v != null) vals.push(v);
  if (o.zero) vals.push(0);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const dp = hi - lo < 10 && !o.money ? 1 : 0;
  const num = v => (dp ? v.toFixed(1) : fmt(v));
  const xs = i => box.x + (i - i0) / Math.max(1, i1 - i0) * box.w;
  const ys = v => box.y + box.h - (v - lo) / (hi - lo) * box.h;
  const g = G(p, 'chart');
  // the grid, and its numbers on the right
  const step = niceStep((hi - lo) / 4);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    E('line', { x1: box.x, x2: box.x + box.w, y1: ys(v), y2: ys(v), class: v === 0 && o.zero ? 'zero' : 'grid' }, g);
    T(g, box.x + box.w + 10, ys(v) + 5, o.money ? sinr(v) : num(v), 'glab', 'start');
  }
  // time: day starts across days, else hours
  const multi = dayOf(s.t[i0]) !== dayOf(s.t[i1]);
  let last = null, lastX = -1e9;
  const every = Math.max(1, Math.round((i1 - i0) / 9));
  for (let i = i0; i <= i1; i++) {
    const lab = multi ? dayOf(s.t[i]) : hm(s.t[i]).slice(0, 3) + '00';
    if (lab !== last && (multi || (i - i0) % every === 0 || i === i0)) {
      if ((last !== null || i === i0) && xs(i) - lastX > 70) {
        lastX = xs(i);
        E('line', { x1: xs(i), x2: xs(i), y1: box.y, y2: box.y + box.h, class: 'vgrid' }, g);
        T(g, xs(i) + 4, box.y + box.h + 22, multi ? lab : hm(s.t[i]), 'glab', 'start');
      }
      last = lab;
    }
  }
  const id = `c${++uid}`;
  const cp = E('clipPath', { id }, g);
  const clipR = E('rect', { x: box.x - 2, y: box.y - 40, width: 0, height: box.h + 80 }, cp);
  const body = G(g);
  body.setAttribute('clip-path', `url(#${id})`);
  const path = (k2, a = i0, b = i1) => {
    let d = '', pen = false;
    for (let i = a; i <= b; i++) {
      const v = s[k2][i];
      if (v == null) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${xs(i).toFixed(1)} ${ys(v).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  const bands = G(body, 'bands');
  if (o.strikes) {
    for (const L of st().legs) {
      if (!L.strike || !['CE', 'PE'].includes(L.kind)) continue;
      const a = Math.max(L.a, i0), b = Math.min(L.b == null ? i1 : L.b, i1);
      if (b - a < 1 || L.strike < lo || L.strike > hi) continue;
      const y = ys(L.strike);
      E('line', { x1: xs(a), x2: xs(b), y1: y, y2: y, class: `band ${L.kind === 'CE' ? 'ce' : 'pe'}` }, bands);
      if (L.a >= i0) E('circle', { cx: xs(L.a), cy: y, r: 6, class: `bend ${L.kind === 'CE' ? 'ce' : 'pe'}` }, bands);
      if (L.b != null && L.b <= i1) E('circle', { cx: xs(L.b), cy: y, r: 6, class: 'bend shut' }, bands);
      if (!o.lastLabels || L.b == null || L.b >= i1)
        T(bands, xs(a) + 6, y + (L.kind === 'CE' ? -9 : 21), `${fmt(L.strike)} ${L.kind === 'CE' ? 'call' : 'put'}`, `bandt ${L.kind === 'CE' ? 'ce' : 'pe'}`, 'start');
    }
  }
  const areas = {};
  if (o.area) {
    const d = path(key);
    if (d) {
      const z = ys(Math.max(lo, Math.min(hi, 0)));
      const full = `${d}L${xs(i1).toFixed(1)} ${z}L${xs(i0).toFixed(1)} ${z}Z`;
      const up = `cu${uid}`, dn = `cd${uid}`;
      E('rect', { x: box.x, y: box.y - 40, width: box.w, height: z - box.y + 40 }, E('clipPath', { id: up }, g));
      E('rect', { x: box.x, y: z, width: box.w, height: box.y + box.h - z + 40 }, E('clipPath', { id: dn }, g));
      areas.up = E('path', { d: full, class: 'area pos', 'clip-path': `url(#${up})` }, body);
      areas.dn = E('path', { d: full, class: 'area neg', 'clip-path': `url(#${dn})` }, body);
    }
  }
  for (const k2 of o.also || []) E('path', { d: path(k2), class: `ln-${k2}` }, body);
  E('path', { d: path(key), class: `ln-${key}${o.faint ? ' faint' : ''}` }, body);
  const head = G(g, 'head');
  const ring = E('circle', { r: 13, class: 'ring' }, head);
  E('circle', { r: 6.5, class: `dot ${key}` }, head);
  const lab = T(head, 14, -14, '', 'headt', 'start');
  op(head, 0);
  const valAt = i => {
    const a = Math.floor(i), b = Math.min(i1, a + 1), f = i - a;
    const va = s[key][a], vb = s[key][b];
    return va == null ? vb : vb == null ? va : lerp(va, vb, f);
  };
  return {
    g, xs, ys, lo, hi, bands, body, lab,
    reveal(i, rt = 0, show = true) {
      i = clamp(i, i0, i1);
      clipR.setAttribute('width', Math.max(0, xs(i) - box.x + 3).toFixed(1));
      const v = valAt(i);
      if (v == null || !show || i <= i0) { op(head, 0); return v; }
      op(head, 1);
      tr(head, xs(i), ys(v));
      ring.setAttribute('r', (11 + 4 * Math.sin(rt * 4)).toFixed(2));
      txt(lab, o.money ? sinr(v) : num(v));
      // near the right edge the label goes left of the dot, clear of the axis numbers
      const left = xs(i) > box.x + box.w * 0.82;
      lab.setAttribute('x', left ? -16 : 14); lab.setAttribute('text-anchor', left ? 'end' : 'start');
      return v;
    },
  };
}
function niceStep(x) {
  const e = Math.pow(10, Math.floor(Math.log10(Math.max(x, 1e-9))));
  const f = x / e;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * e;
}

function slideIn(el, t, a, d = 0.45) {
  const f = k(t, a, a + d);
  op(el, Math.min(1, f * 2));
  el.setAttribute('transform', `translate(${((1 - backOut(f)) * 60).toFixed(1)},0)`);
}

// two bars and the line the rule acts at (the strangle's balance)
function balance(p, x, y, h, g0) {
  const g = G(p, 'bal');
  const top = Math.max(g0.big, g0.small, g0.big0 || 0, g0.small0 || 0, g0.after || 0) * 1.3 || 1;
  const yv = v => y + h - (v / top) * h;
  E('line', { x1: x - 20, x2: x + 380, y1: y + h, y2: y + h, class: 'axis' }, g);
  const bars = [0, 1].map(i => {
    const bx = x + 20 + i * 190;
    const sideCls = (i === 0 ? g0.big_side : g0.small_side) === 'call' ? 'call' : 'put';
    const r = E('rect', { x: bx, y: y + h, width: 130, height: 0, class: `bar ${sideCls}` }, g);
    r.base = `bar ${sideCls}`;
    const v = T(g, bx + 65, y + h - 10, '', 'barv');
    T(g, bx + 65, y + h + 30, `${(i === 0 ? g0.big_side : g0.small_side)} side`, 'barl');
    return { r, v, bx };
  });
  const lineY = yv(g0.line * g0.big);
  const ln = E('line', { x1: bars[1].bx - 16, x2: bars[1].bx + 146, y1: lineY, y2: lineY, class: 'rule-line' }, g);
  const lt = T(g, bars[1].bx + 150, lineY + 5, `${Math.round(g0.line * 100)}% line`, 'rulet', 'start');
  const pctT = T(g, x + 190, y - 18, '', 'ratio');
  return {
    g,
    set(big, small, ratio, state = '') {
      for (const [b, v] of [[bars[0], big], [bars[1], small]]) {
        b.r.setAttribute('y', yv(v).toFixed(1));
        b.r.setAttribute('height', (y + h - yv(v)).toFixed(1));
        b.v.setAttribute('y', (yv(v) - 10).toFixed(1));
        txt(b.v, px(v));
      }
      bars[1].r.setAttribute('class', `${bars[1].r.base} ${state}`);
      if (ratio != null) txt(pctT, `${Math.round(ratio * 100)}% of the ${g0.big_side} side`);
    },
    line: [ln, lt],
  };
}
function progress(p, x, y, w, made, target, from = 0) {
  const g = G(p, 'prog2');
  const top = Math.max(made, target, from) * 1.12 || 1;
  E('rect', { x, y, width: w, height: 40, rx: 20, class: 'track' }, g);
  const fill = E('rect', { x, y, width: 0, height: 40, rx: 20, class: 'fillp' }, g);
  const fx = x + (target / top) * w;
  const flag = G(g, 'flag');
  E('line', { x1: fx, x2: fx, y1: y - 34, y2: y + 52, class: 'flagpole' }, flag);
  E('path', { d: `M${fx} ${y - 34}l34 9-34 9z`, class: 'flagf' }, flag);
  T(flag, fx, y + 76, `checkpoint ${inr(target)}`, 'flagt');
  const val = T(g, x, y - 16, '', 'progv', 'start');
  return {
    g, flag,
    set(v) {
      const vv = Math.max(0, v);
      fill.setAttribute('width', Math.max(0, Math.min(w, (vv / top) * w)).toFixed(1));
      fill.setAttribute('class', `fillp ${v >= target ? 'hit' : ''}`);
      txt(val, `${inr(v)} made`);
    },
  };
}


// ── each leg's own price, step by step (series.held) ───────────────────
let MK = null;
function legMarks() {
  if (MK) return MK;
  const s = S(), L = st().legs;
  MK = L.map(() => new Array(s.t.length).fill(null));
  (s.held || []).forEach((row, i) => row.forEach(([j, m]) => { if (MK[j]) MK[j][i] = m; }));
  return MK;
}
const isOpt = l => l && (l.kind === 'CE' || l.kind === 'PE');
const sideCls = l => (l.kind === 'CE' ? 'ce' : l.kind === 'PE' ? 'pe' : 'fut');
const legPnl = (L, mark) => (L.entry == null || mark == null ? null : (mark - L.entry) * (L.qty || 0) * (L.sold ? -1 : 1));
const verbOf = L => (L.sold ? 'sold' : 'bought');

// the option prices under the index, on the same steps: one line a leg,
// from where it was opened (a dot, its price) to where it was closed
// (a ring, its price); .reveal(i) draws them to step i with a dot and a
// price on each leg still open there, so they move as the index moves
function prices(p, box, i0, i1, o = {}) {
  const s = S(), L = st().legs, M = legMarks();
  const js = L.map((_, j) => j).filter(j => isOpt(L[j]) && L[j].a <= i1 && (L[j].b == null || L[j].b > i0));
  const vals = [];
  const pts = {};
  for (const j of js) {
    const l = L[j], a = l.a, b = l.b == null ? i1 : Math.min(l.b, i1), q = [];
    if (a >= i0 && l.entry != null) q.push([a, l.entry]);
    for (let i = Math.max(i0, a + (a >= i0 && l.entry != null ? 1 : 0)); i <= b; i++) if (M[j][i] != null && !(l.b != null && i === l.b && l.exit != null)) q.push([i, M[j][i]]);
    if (l.b != null && l.b <= i1 && l.exit != null) q.push([l.b, l.exit]);
    pts[j] = q;
    q.forEach(x => vals.push(x[1]));
  }
  let lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12; lo = Math.max(0, lo - pad); hi += pad;
  const xs = i => box.x + (i - i0) / Math.max(1, i1 - i0) * box.w;
  const ys = v => box.y + box.h - (v - lo) / (hi - lo) * box.h;
  const g = G(p, 'chart prices');
  const stp = niceStep((hi - lo) / 3);
  for (let v = Math.ceil(lo / stp) * stp; v <= hi; v += stp) {
    E('line', { x1: box.x, x2: box.x + box.w, y1: ys(v), y2: ys(v), class: 'grid' }, g);
    T(g, box.x + box.w + 10, ys(v) + 5, `₹${fmt(v)}`, 'glab', 'start');
  }
  const id = `c${++uid}`;
  const clipR = E('rect', { x: box.x - 12, y: box.y - 60, width: 0, height: box.h + 120 }, E('clipPath', { id }, g));
  const body = G(g);
  body.setAttribute('clip-path', `url(#${id})`);
  const legs = {};
  const mg = G(g, 'pmarks');   // the opened and closed marks, outside the clip so their prices aren't cut
  for (const j of js) {
    const l = L[j], q = pts[j], lg = G(body, `pleg ${sideCls(l)}`);
    E('path', { d: q.map((x, n) => `${n ? 'L' : 'M'}${xs(x[0]).toFixed(1)} ${ys(x[1]).toFixed(1)}`).join(''), class: `ln-opt ${sideCls(l)}` }, lg);
    // a call's words above its mark, a put's below, so two legs opened together don't collide
    const dy = l.kind === 'CE' ? -12 : 24;
    let inM = null, outM = null;
    if (l.a >= i0 && l.entry != null) {
      inM = G(mg, 'pmk');
      E('circle', { cx: xs(l.a), cy: ys(l.entry), r: 7, class: `mk in ${sideCls(l)}` }, inM);
      const right = xs(l.a) > box.x + box.w * 0.62;
      if (o.labels && (!o.only || o.only.has(j))) T(inM, xs(l.a) + (right ? -12 : 12), ys(l.entry) + dy, `${l.what} · ${verbOf(l)} ${px(l.entry)}`, `mkt ${sideCls(l)}`, right ? 'end' : 'start');
      op(inM, 0);
    }
    if (l.b != null && l.b <= i1 && l.exit != null) {
      outM = G(mg, 'pmk');
      E('circle', { cx: xs(l.b), cy: ys(l.exit), r: 7, class: `mk out ${sideCls(l)}` }, outM);
      if (o.labels && (!o.only || o.only.has(j))) T(outM, xs(l.b) - 12, ys(l.exit) + dy, `closed ${px(l.exit)}`, `mkt ${sideCls(l)}`, 'end');
      op(outM, 0);
    }
    const head = G(g, 'phead');
    E('circle', { r: 6, class: `dot ${sideCls(l)}` }, head);
    const ht = T(head, 12, 5, '', `pheadt ${sideCls(l)}`, 'start');
    op(head, 0);
    legs[j] = { g: lg, inM, outM, head, ht, show: 1, showOut: 1 };
  }
  // marks' words near one another (two legs opened or closed at one moment) pushed apart
  const words = [...mg.querySelectorAll('text')].map(e => ({ e, x: +e.getAttribute('x'), y: +e.getAttribute('y') })).sort((a, b) => a.y - b.y);
  words.forEach((w, n) => {
    for (let q = 0; q < n; q++) {
      const v = words[q];
      if (Math.abs(v.x - w.x) < 260 && w.y - v.y < 18) { w.y = v.y + 18; w.e.setAttribute('y', w.y.toFixed(1)); }
    }
  });
  if (!js.length) T(g, box.x + box.w / 2, box.y + box.h / 2, 'no options held here', 'mono mute');
  return {
    g, xs, ys, legs, any: js.length > 0,
    reveal(i, at = null) {
      i = clamp(i, i0, i1);
      clipR.setAttribute('width', Math.max(0, xs(i) - box.x + 14).toFixed(1));
      const pi = at == null ? Math.round(i) : at, shown = [];
      for (const j of js) {
        const x = legs[j], l = L[j];
        if (x.inM) op(x.inM, i >= l.a - 1e-6 ? x.show : 0);
        if (x.outM) op(x.outM, i >= l.b - 1e-6 ? x.showOut : 0);
        const open = l.a <= pi && (l.b == null || pi < l.b) && M[j][pi] != null;
        if (!open || !o.heads || !x.show) { op(x.head, 0); continue; }
        shown.push([j, ys(M[j][pi]), M[j][pi]]);
      }
      // the prices beside the dots, pushed apart so none hides another (past five, the dots alone)
      shown.sort((a, b) => a[1] - b[1]);
      const say = shown.length <= 5;
      let last = -1e9;
      for (const [j, y, v] of shown) {
        const x = legs[j], ly = Math.max(y, last + 20);
        last = ly;
        op(x.head, x.show);
        tr(x.head, xs(i), y);
        x.ht.setAttribute('y', (ly - y + 5).toFixed(1));
        txt(x.ht, say ? px(v) : '');
      }
    },
  };
}

// the positions table: each leg held at that moment, what it was opened
// at, its price now and its P&L, with a bar that grows and shrinks with
// it; a close stays on as "closed" with what it booked, a new leg comes in
// marked "new". More legs than rows: each side's legs as one row.
function positions(p, x, y, w, nRows) {
  const g = G(p, 'pos');
  T(g, x, y, 'Positions', 'fl', 'start');
  const n = Math.max(1, Math.min(5, nRows)), h = Math.min(54, 230 / n);
  const bx = x + w * 0.64, bw = 64;   // the P&L bar: from a zero tick, right for a gain, left for a loss
  let scale = 1;
  const rows = Array.from({ length: n }, (_, r) => {
    const rg = G(g, 'prow'), yy = y + 12 + r * h, hh = h - 6;
    E('rect', { x, y: yy, width: w, height: hh, rx: 8, class: 'pbg' }, rg);
    E('rect', { x, y: yy, width: 7, height: hh, rx: 3, class: 'strip' }, rg);
    E('line', { x1: bx, x2: bx, y1: yy + hh * 0.2, y2: yy + hh * 0.8, class: 'pzero' }, rg);
    const bar = E('rect', { x: bx, y: yy + hh * 0.3, width: 0, height: hh * 0.4, rx: 2, class: 'pbar' }, rg);
    const a = T(rg, x + 20, yy + hh * 0.45, '', 'pw', 'start');
    const b = T(rg, x + 20, yy + hh * 0.84, '', 'pm', 'start');
    const c = T(rg, x + w - 16, yy + hh * 0.45, '', 'pp', 'end');
    const d = T(rg, x + w - 16, yy + hh * 0.84, '', 'pm', 'end');
    op(rg, 0);
    return { rg, bar, a, b, c, d };
  });
  const none = T(g, x + 20, y + 40, 'nothing open', 'pm', 'start');
  const more = T(g, x + w, y, '', 'pm', 'end');
  const grouped = list => {
    if (list.length <= n) return list;
    const by = new Map();
    for (const v of list) {
      const key = `${v.cls}|${v.state || ''}|${v.sold}`;
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(v);
    }
    return [...by.values()].map(vs => (vs.length === 1 ? vs[0] : {
      ...vs[0], n: vs.length,
      what: `${vs.length} ${vs[0].cls === 'ce' ? 'calls' : vs[0].cls === 'pe' ? 'puts' : 'legs'}`,
      entry: vs.reduce((a, v) => a + (v.entry || 0), 0), mark: vs.reduce((a, v) => a + (v.mark || 0), 0),
      pnl: vs.some(v => v.pnl == null) ? null : vs.reduce((a, v) => a + v.pnl, 0),
      o: Math.max(...vs.map(v => (v.o == null ? 1 : v.o))),
    }));
  };
  return {
    g,
    scale(v) { scale = Math.max(1, v); },
    set(list0) {
      const list = grouped(list0);
      op(none, list.length ? 0 : 1);
      txt(more, list.length > n ? `+${list.length - n} more` : '');
      rows.forEach((r, i) => {
        const v = list[i];
        if (!v) { op(r.rg, 0); return; }
        op(r.rg, v.o == null ? 1 : v.o);
        r.rg.setAttribute('class', `prow ${v.cls || ''} ${v.state || ''}`);
        const verb = v.sold ? 'sold' : 'bought', all = v.n ? ' in all' : '';
        txt(r.a, `${v.sold ? 'Short' : 'Long'} ${v.what}`);
        txt(r.b, v.state === 'closed' ? `${verb} ${px(v.entry)}${all} → closed ${px(v.mark)}`
          : v.state === 'new' ? `${verb} just now at ${px(v.entry)}${all}`
            : `${verb} ${px(v.entry)}${all} → now ${px(v.mark)}`);
        txt(r.c, v.pnl == null ? '' : sinr(v.pnl));
        r.c.setAttribute('class', `pp ${tone(v.pnl)}`);
        txt(r.d, v.state === 'closed' ? 'booked' : v.state === 'new' ? 'new' : 'open');
        const bwid = v.pnl == null ? 0 : Math.min(bw, Math.abs(v.pnl) / scale * bw);
        r.bar.setAttribute('width', bwid.toFixed(1));
        r.bar.setAttribute('x', (v.pnl < 0 ? bx - bwid : bx).toFixed(1));
        r.bar.setAttribute('class', `pbar ${tone(v.pnl)}`);
      });
    },
  };
}

// the P&L box: booked, open and the total at the moment shown; with
// `sp` ({i0, i1}), the total P&L's line under them, drawn to the playhead
function pnlBox(p, x, y, w, sp = null) {
  const g = G(p, 'pnlchip'), s = S();
  const hh = sp ? 182 : 142;
  E('rect', { x, y, width: w, height: hh, rx: 12 }, g);
  const head = T(g, x + 24, y + 30, 'P&L', 'fl', 'start');
  const when = T(g, x + w - 24, y + 30, '', 'mono mute pwhen', 'end');
  const cols = ['Booked', 'Open', 'Total'].map((l, i) => {
    const cx0 = x + 24 + i * ((w - 48) / 3);
    T(g, cx0, y + 60, l, 'fl sm', 'start');
    return T(g, cx0, y + 96, '', i === 2 ? 'pnlv' : 'pnlv2', 'start');
  });
  let spark = null;
  if (sp) {
    const vals = [0];
    for (let i = sp.i0; i <= sp.i1; i++) if (s.pnl[i] != null) vals.push(s.pnl[i]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1) { hi += 1; lo -= 1; }
    const bx = x + 24, bw = w - 48, by = y + 112, bh = 40;
    const sx = i => bx + (i - sp.i0) / Math.max(1, sp.i1 - sp.i0) * bw, sy = v => by + bh - (v - lo) / (hi - lo) * bh;
    E('line', { x1: bx, x2: bx + bw, y1: sy(0), y2: sy(0), class: 'sparkzero' }, g);
    const line = E('path', { class: 'spark' }, g), dot = E('circle', { r: 5, class: 'sparkdot' }, g);
    spark = i => {
      let d = '', pen = false, last = null;
      for (let q = sp.i0; q <= Math.min(i, sp.i1); q++) {
        const v = s.pnl[q];
        if (v == null) { pen = false; continue; }
        d += `${pen ? 'L' : 'M'}${sx(q).toFixed(1)} ${sy(v).toFixed(1)}`;
        pen = true; last = [q, v];
      }
      line.setAttribute('d', d);
      op(dot, last ? 1 : 0);
      if (last) { dot.setAttribute('cx', sx(last[0]).toFixed(1)); dot.setAttribute('cy', sy(last[1]).toFixed(1)); dot.setAttribute('class', `sparkdot ${tone(last[1])}`); }
    };
  }
  const note = T(g, x + 24, y + hh - 12, '', 'tb', 'start');
  return {
    g, h: hh,
    set(label, whenS, booked, total, noteS, noteV) {
      txt(head, label); txt(when, whenS);
      const open = total == null || booked == null ? null : total - booked;
      [booked, open, total].forEach((v, i) => { txt(cols[i], sinr(v)); cols[i].setAttribute('class', `${i === 2 ? 'pnlv' : 'pnlv2'} ${tone(v)}`); });
      txt(note, noteS || ''); note.setAttribute('class', `tb ${tone(noteV)}`);
    },
    spark(i) { if (spark) spark(i); },
  };
}

// ── 1. what this video is ──────────────────────────────────────────────
DRAW.open = (p, sc, cx) => {
  const d = sc.data, s = S();
  const L = G(p);
  T(L, 80, 230, d.name, 'huge', 'start');
  const c1 = chip(L, 80, 290, d.strategy);
  chip(L, 80 + c1.w + 12, 290, d.index, 'soft');
  T(L, 80, 360, d.episode, 'bigs', 'start');
  T(L, 80, 394, d.window, 'mono mute', 'start');
  let live = null;
  if (d.live) {
    live = G(L, 'live');
    E('rect', { x: 80, y: 420, width: 132, height: 38, rx: 19 }, live);
    E('circle', { cx: 104, cy: 439, r: 7, class: 'livedot' }, live);
    T(live, 120, 446, 'RUNNING', 'livet', 'start');
  }
  // the hook: the P&L's whole path, its low and high marked, the total rolling up
  const R = G(p);
  T(R, 900, 200, d.live ? 'P&L so far' : 'P&L', 'fl', 'start');
  const big = T(R, 900, 268, '', `huge ${tone(d.total)}`, 'start');
  const chips = G(R);
  const b1 = chip(chips, 900, 320, `booked ${sinr(d.booked)}`);
  if (Math.abs(d.open) >= 1) chip(chips, 900 + b1.w + 12, 320, `open ${sinr(d.open)}`, 'soft');
  const n = s.pnl.length - 1;
  const ch = chart(R, { x: 900, y: 400, w: 560, h: 220 }, 0, n, { key: 'pnl', money: true, zero: true, area: true });
  const ps = s.pnl.map((v, i) => [i, v]).filter(x => x[1] != null);
  const ext = ps.length ? [ps.reduce((a, b) => (b[1] < a[1] ? b : a)), ps.reduce((a, b) => (b[1] > a[1] ? b : a))] : [];
  const marks = ext.map(([i, v], q) => {
    const g = G(R, 'mark');
    E('circle', { cx: ch.xs(i), cy: ch.ys(v), r: 8, class: q ? 'high' : 'low' }, g);
    T(g, clamp(ch.xs(i), 940, 1420), ch.ys(v) + (q ? -16 : 30), sinr(v), `markt ${q ? 'high' : 'low'}`);
    op(g, 0);
    return g;
  });
  const trades = (st().events || st().scenes.filter(x => x.kind === 'trade').map(x => x.data)).map(x => x.at);
  const dots = trades.map(i => { const c = E('circle', { cx: ch.xs(i), cy: ch.ys(s.pnl[i] || 0), r: 7, class: 'evdot' }, R); op(c, 0); return c; });
  const cnt = T(R, 900, 690, `${d.trades} trade${d.trades === 1 ? '' : 's'}`, 'bigs', 'start');
  const jH = Math.max(0, cx.find('hook')), jT = cx.find('title'), jC = cx.find('count');
  return (t, rt) => {
    op(R, k(t, cx.at[jH][0], cx.at[jH][0] + 0.4));
    ch.reveal(lerp(0, n, cx.cue(t, jH, 0, 0.75)), rt);
    countUp(big, t, cx.at[jH][0], lerp(cx.at[jH][0], cx.at[jH][1], 0.75), 0, d.total, v => sinr(v));
    marks.forEach((m, q) => pop(m, t, lerp(cx.at[jH][0], cx.at[jH][1], 0.3 + 0.35 * q), 0.5));
    pop(L, t, jT >= 0 ? cx.at[jT][0] : 0, 0.6);
    if (live) op(live.querySelector('.livedot'), 0.5 + 0.5 * Math.sin(rt * 4));
    const c0 = jC >= 0 ? cx.at[jC][0] : cx.at[jH][1];
    pop(chips, t, c0, 0.5);
    dots.forEach((c, i) => pop(c, t, c0 + 0.3 + 0.25 * i, 0.4));
    pop(cnt, t, c0 + 0.5, 0.5);
  };
};

// ── 2. how it plays ────────────────────────────────────────────────────
DRAW.rules = (p, sc, cx) => {
  const cards = sc.data.cards;
  const n = cards.length, strangle = st().strategy === 'strangle_monthly_v2';
  const row1 = Math.min(3, n), cw = 460, chh = 270, gap = 30;
  const glyphs = strangle ? [gSell, gBalance, gClock, gFlag, gMeet] : [gChart, gArrows, gGear, gGear];
  const built = cards.map((c, i) => {
    const r = i < row1 ? 0 : 1, inRow = r === 0 ? row1 : n - row1, ci = r === 0 ? i : i - row1;
    const x0 = (W - (inRow * cw + (inRow - 1) * gap)) / 2 + ci * (cw + gap), y0 = 125 + r * (chh + 30);
    const g = G(p, 'card');
    E('rect', { x: x0, y: y0, width: cw, height: chh, rx: 14 }, g);
    const gl = (glyphs[i] || gChart)(g, x0 + cw / 2, y0 + 70);
    T(g, x0 + 28, y0 + 168, c.h, 'cardh', 'start');
    para(g, x0 + 28, y0 + 204, c.b, 40, 28, 'cardb');
    op(g, 0);
    return { g, gl };
  });
  return (t, rt, j) => built.forEach((b, i) => {
    const jj = Math.min(i, cx.at.length - 1);
    pop(b.g, t, cx.at[jj][0], 0.55);
    b.gl(rt, j === jj);
  });
};
function gSell(p, x, y) {
  const g = G(p, 'glyph');
  E('line', { x1: x - 120, x2: x + 120, y1: y - 40, y2: y - 40, class: 'band ce' }, g);
  E('line', { x1: x - 120, x2: x + 120, y1: y + 40, y2: y + 40, class: 'band pe' }, g);
  const ln = E('path', { class: 'ln-idx' }, g);
  return (rt, on) => {
    let d = '';
    for (let i = 0; i <= 40; i++) {
      const xx = x - 120 + i * 6, v = Math.sin(i * 0.45 + (on ? rt * 2.2 : 0)) * 18 + Math.sin(i * 0.17 + 1) * 8;
      d += `${i ? 'L' : 'M'}${xx} ${(y + v).toFixed(1)}`;
    }
    ln.setAttribute('d', d);
  };
}
function gBalance(p, x, y) {
  const g = G(p, 'glyph');
  const a = E('rect', { x: x - 70, width: 50, class: 'bar big' }, g), b = E('rect', { x: x + 20, width: 50, class: 'bar small' }, g);
  E('line', { x1: x + 10, x2: x + 80, y1: y + 10, y2: y + 10, class: 'rule-line' }, g);
  return (rt, on) => {
    const f = on ? 0.5 + 0.5 * Math.sin(rt * 1.8) : 0.5;
    const ha = 50 + 20 * f, hb = 70 - 46 * f;
    a.setAttribute('y', y + 50 - ha); a.setAttribute('height', ha);
    b.setAttribute('y', y + 50 - hb); b.setAttribute('height', hb);
    b.setAttribute('class', `bar small ${hb < 40 ? 'under' : ''}`);
  };
}
function gClock(p, x, y) {
  const g = G(p, 'glyph');
  E('circle', { cx: x, cy: y, r: 46, class: 'clockf' }, g);
  const hh = E('line', { x1: x, y1: y, class: 'hand h' }, g), mm = E('line', { x1: x, y1: y, class: 'hand m' }, g);
  return (rt, on) => {
    const a = (on ? rt * 1.2 : 0) - Math.PI / 2 + 0.6, b = -Math.PI / 2 + 2.6;
    mm.setAttribute('x2', x + Math.cos(a) * 38); mm.setAttribute('y2', y + Math.sin(a) * 38);
    hh.setAttribute('x2', x + Math.cos(b) * 24); hh.setAttribute('y2', y + Math.sin(b) * 24);
  };
}
function gFlag(p, x, y) {
  const g = G(p, 'glyph');
  E('rect', { x: x - 120, y: y - 10, width: 240, height: 24, rx: 12, class: 'track' }, g);
  const f = E('rect', { x: x - 120, y: y - 10, height: 24, rx: 12, class: 'fillp' }, g);
  E('line', { x1: x + 80, x2: x + 80, y1: y - 46, y2: y + 26, class: 'flagpole' }, g);
  E('path', { d: `M${x + 80} ${y - 46}l30 8-30 8z`, class: 'flagf' }, g);
  return (rt, on) => { const v = on ? ((rt * 0.35) % 1) : 0.6; f.setAttribute('width', (240 * v).toFixed(1)); f.setAttribute('class', `fillp ${v > 0.83 ? 'hit' : ''}`); };
}
function gMeet(p, x, y) {
  const g = G(p, 'glyph');
  const a = E('path', { class: 'band ce' }, g), b = E('path', { class: 'band pe' }, g);
  return (rt, on) => {
    const f = on ? 0.5 + 0.5 * Math.sin(rt * 1.5) : 1;
    a.setAttribute('d', `M${x - 120} ${y - 44}C${x - 20} ${y - 44} ${x} ${y - 44 * (1 - f)} ${x + 120} ${y - 44 * (1 - f)}`);
    b.setAttribute('d', `M${x - 120} ${y + 44}C${x - 20} ${y + 44} ${x} ${y + 44 * (1 - f)} ${x + 120} ${y + 44 * (1 - f)}`);
  };
}
function gChart(p, x, y) {
  const g = G(p, 'glyph');
  const ln = E('path', { class: 'ln-idx' }, g);
  return (rt, on) => {
    let d = '';
    const n = on ? Math.floor(((rt * 14) % 40) + 1) : 40;
    for (let i = 0; i <= n; i++) d += `${i ? 'L' : 'M'}${x - 120 + i * 6} ${(y + Math.sin(i * 0.5) * 16 - i * 0.8 + 16).toFixed(1)}`;
    ln.setAttribute('d', d);
  };
}
function gArrows(p, x, y) {
  const g = G(p, 'glyph');
  const a = E('path', { d: `M${x - 90} ${y + 30}L${x - 10} ${y - 30}`, class: 'arrow up' }, g);
  const b = E('path', { d: `M${x + 10} ${y - 30}L${x + 90} ${y + 30}`, class: 'arrow dn' }, g);
  return (rt, on) => { op(a, on ? 0.6 + 0.4 * Math.sin(rt * 3) : 1); op(b, on ? 0.6 - 0.4 * Math.sin(rt * 3) : 1); };
}
function gGear(p, x, y) {
  const g = G(p, 'glyph gear');
  const inner = G(g);
  E('circle', { cx: 0, cy: 0, r: 36 }, inner);
  E('circle', { cx: 0, cy: 0, r: 14 }, inner);
  return (rt, on) => inner.setAttribute('transform', `translate(${x},${y}) rotate(${((on ? rt : 0) * 60) % 360})`);
}

// ── 3. the market ──────────────────────────────────────────────────────
// the index and, under it on the same steps, every option it held: the
// two drawn together, so a move in one shows in the other
DRAW.market = (p, sc, cx) => {
  const s = S(), n = s.idx.length - 1, d = sc.data;
  const strikes = st().legs.filter(l => l.strike).map(l => l.strike);
  const opt = st().legs.some(isOpt), vxOn = s.vix.some(v => v != null);
  const ch = chart(p, { x: 80, y: 130, w: 1340, h: opt ? 250 : 410 }, 0, n, { strikes: true, extra: strikes });
  op(ch.bands, 0);
  const marks = [];
  for (const [i, lbl] of [[d.hi, 'high'], [d.lo, 'low']]) {
    if (i == null || s.idx[i] == null) continue;
    const g = G(p, 'mark');
    E('circle', { cx: ch.xs(i), cy: ch.ys(s.idx[i]), r: 9, class: lbl }, g);
    T(g, ch.xs(i), ch.ys(s.idx[i]) + (lbl === 'high' ? -20 : 34), `${lbl} ${fmt(s.idx[i])}`, `markt ${lbl}`);
    op(g, 0);
    marks.push(g);
  }
  let pr = null;
  if (opt) {
    T(p, 80, 438, 'Option prices', 'fl', 'start');
    const lg = G(p, 'legend');
    E('line', { x1: 290, x2: 320, y1: 433, y2: 433, class: 'ln-opt ce' }, lg); T(lg, 328, 438, 'calls', 'glab', 'start');
    E('line', { x1: 390, x2: 420, y1: 433, y2: 433, class: 'ln-opt pe' }, lg); T(lg, 428, 438, 'puts', 'glab', 'start');
    E('circle', { cx: 500, cy: 433, r: 6, class: 'mk in ce' }, lg); T(lg, 512, 438, 'opened', 'glab', 'start');
    E('circle', { cx: 600, cy: 433, r: 6, class: 'mk out ce' }, lg); T(lg, 612, 438, 'closed', 'glab', 'start');
    pr = prices(p, { x: 80, y: 452, w: 1340, h: vxOn ? 170 : 260 }, 0, n, { heads: true });
  }
  let vx = null;
  if (vxOn) {
    const y0 = opt ? 668 : 612;
    T(p, 80, y0 - 10, 'India VIX', 'fl', 'start');
    vx = chart(p, { x: 80, y: y0, w: 1340, h: opt ? 60 : 104 }, 0, n, { key: 'vix' });
  }
  const jD = cx.find('draw'), jE = cx.find('extremes'), jS = cx.find('strikes'), jV = cx.find('vix');
  return (t, rt) => {
    const i = lerp(0, n, jD >= 0 ? cx.cue(t, jD, 0, 0.9) : 1);
    ch.reveal(i, rt, jD >= 0 && t < cx.at[jD][1] + 0.01);
    if (pr) pr.reveal(i);
    marks.forEach((m, q) => pop(m, t, jE >= 0 ? lerp(cx.at[jE][0], cx.at[jE][1], 0.1 + 0.4 * q) : 1e9, 0.5));
    if (jS >= 0) op(ch.bands, cx.cue(t, jS, 0, 0.4));
    if (vx) { op(vx.g, jV >= 0 ? k(t, cx.at[jV][0], cx.at[jV][0] + 0.3) : 0); vx.reveal(lerp(0, n, jV >= 0 ? cx.cue(t, jV, 0, 0.9) : 0), rt, jV >= 0 && t < cx.at[jV][1]); }
  };
};

// ── 4. a trade ─────────────────────────────────────────────────────────
// A desk: on the left the index (its strikes, where each was sold and
// closed) and under it every option's own price, on one playhead; on the
// right the P&L (booked, open, total), the positions held, and what the
// rule saw. The playhead walks from the trade before to this one while the
// stretch between is told, everything moving together, then stays at the
// moment while the trade is explained: a close turns its row to "closed"
// and moves what it booked from open to booked; a new leg comes in.
DRAW.trade = (p, sc, cx) => {
  const d = sc.data, s = S(), Lg = st().legs, M = legMarks();
  const [w0, w1] = d.w, at = d.at;
  const opt = Lg.some(l => isOpt(l) && l.a <= w1 && (l.b == null || l.b >= w0));
  const strikes = d.fills.map(f => f.strike).concat(Lg.filter(l => l.strike && l.a <= w1 && (l.b == null || l.b >= w0)).map(l => l.strike));
  const ch = chart(p, { x: 60, y: 170, w: 820, h: opt ? 210 : 470 }, w0, w1, { strikes: true, extra: strikes });
  // the index where the playhead is, and how far it has come
  const ixV = T(p, 60, 150, '', 'bigs ixv', 'start'), ixD = T(p, 300, 150, '', 'mono ixd', 'start');
  let pr = null;
  if (opt) {
    T(p, 60, 436, 'Option prices', 'fl', 'start');
    // with many legs on the chart, words only on the ones this trade opens or closes
    const touched = new Set(d.fills.map(f => f.leg).filter(j => j != null));
    const many = Lg.filter(l => isOpt(l) && l.a <= w1 && (l.b == null || l.b > w0)).length > 4;
    pr = prices(p, { x: 60, y: 452, w: 820, h: 210 }, w0, w1, { labels: true, heads: true, only: many ? touched : null });
  }
  // the moment
  const ev = G(p, 'event');
  E('line', { x1: ch.xs(at), x2: ch.xs(at), y1: 160, y2: opt ? 668 : 640, class: 'evline' }, ev);
  const lab = sc.title, pw = lab.length * 11.2 + 28;
  const pxl = clamp(ch.xs(at) - pw / 2, 420, 900 - pw);
  E('rect', { x: pxl, y: 116, width: pw, height: 34, rx: 17, class: 'evpill' }, ev);
  T(ev, pxl + pw / 2, 139, lab, 'evt');
  op(ev, 0);
  // the profit zone between the sold strikes, from the entry on
  let zone = null;
  const sold = d.fills.filter(f => f.opening && f.verb.startsWith('Sell') && f.strike);
  if (d.family === 'entry' && sold.length >= 2) {
    const ks = sold.map(f => f.strike).sort((a, b) => a - b);
    zone = G(p, 'zone');
    const zx = ch.xs(at);
    E('rect', { x: zx, y: ch.ys(ks[ks.length - 1]), width: 880 - zx, height: ch.ys(ks[0]) - ch.ys(ks[ks.length - 1]), class: 'zone' }, zone);
    T(zone, zx - 14, ch.ys(ks[ks.length - 1]) + 22, 'it profits while the price stays in here →', 'zonet', 'end');
    op(zone, 0);
  }
  // the right-hand side: the P&L (its line from the trade before), the positions, the rule
  const frm = d.from == null ? w0 + Math.round((at - w0) * 0.25) : d.from;
  const box = pnlBox(p, 980, 116, 560, { i0: w0, i1: at });
  // which fills say what, and when: the k-th close on the k-th step that closes, …
  const stepsOf = cues => cx.steps.map((x, j) => (cues.includes(x.cue) ? j : -1)).filter(j => j >= 0);
  const sayC = stepsOf(['close']), sayO = stepsOf(['legs', 'open']);
  const last = cx.at.length - 1;
  const when = (list, k2) => (list.length ? cx.at[list[Math.min(k2, list.length - 1)]][0] + 0.3 : cx.at[last][0]);
  const closes = d.fills.filter(f => !f.opening), opens = d.fills.filter(f => f.opening);
  const tC = closes.map((_, k2) => when(sayC, k2)), tO = opens.map((_, k2) => when(sayO, k2));
  const gone = new Set(closes.map(f => f.leg).filter(j => j != null)), fresh = new Set(opens.map(f => f.leg).filter(j => j != null));
  // the most rows it will show: what's held on the walk, or at the moment with its closes and opens
  let most = 0;
  for (let i = w0; i <= at; i++) most = Math.max(most, (s.held[i] || []).length);
  const atRows = (s.held[at] || []).filter(([j]) => !gone.has(j) && !fresh.has(j)).length + closes.length + opens.length;
  const pos = positions(p, 980, 330, 560, Math.max(most, atRows));
  // the bars' scale: the biggest P&L a leg shows in this scene
  let big = 1;
  for (let q = w0; q <= at; q++) for (const [j, m] of s.held[q] || []) big = Math.max(big, Math.abs(legPnl(Lg[j], m) || 0));
  d.fills.forEach(f => { big = Math.max(big, Math.abs(f.pnl || 0)); });
  pos.scale(big);
  const rowOf = ([j, m]) => { const L = Lg[j]; return { what: L.what, sold: L.sold, entry: L.entry, mark: m, pnl: legPnl(L, m), cls: sideCls(L) }; };
  // what the rule saw
  const viz = G(p);
  let set = () => {};
  if (d.family === 'balance' && d.gauge) {
    const g0 = { ...d.gauge };
    const side = g0.small_side === 'put' ? 'pe' : 'ce', bside = side === 'pe' ? 'ce' : 'pe';
    // the bars' scale holds every price they'll show on the walk
    for (let q = w0; q <= at; q++) { g0.big0 = Math.max(g0.big0 || 0, s[bside][q] || 0); g0.small0 = Math.max(g0.small0 || 0, s[side][q] || 0); }
    const b = balance(viz, 1010, 612, 118, g0);
    const stp = stamp(viz, 1440, 620, d.trigger === 'eod_gap_check' ? 'Gap' : 'Roll', 'r');
    const jR = cx.find('ratio'), jO = cx.find('open');
    set = (t, i, there) => {
      // the two sides as the playhead moves, the same prices as the lines on the left
      const pi = Math.round(i);
      let big = there ? g0.big : (s[bside][pi] ?? g0.big), small = there ? g0.small : (s[side][pi] ?? g0.small);
      let state = small < g0.line * big && (jR < 0 || t >= cx.at[jR][0]) ? 'under' : '';
      if (jO >= 0 && g0.after != null) {
        const fo = cx.cue(t, jO, 0.3, 0.9);
        if (fo > 0) { small = lerp(small, g0.after, fo); state = fo > 0.5 ? 'ok' : state; }
      }
      b.set(big, small, small / big, state);
      b.line.forEach(e => op(e, jR >= 0 ? k(t, cx.at[jR][0], cx.at[jR][0] + 0.5) : 1));
      if (jR >= 0) stp.go(t, lerp(cx.at[jR][0], cx.at[jR][1], 0.85));
      const jC0 = cx.find('close');
      if (jC0 >= 0 && t > cx.at[jC0][0]) op(stp, 1 - k(t, cx.at[jC0][0], cx.at[jC0][0] + 0.5));
    };
  } else if (d.family === 'target' && d.gauge) {
    const from = Math.max(0, s.pnl[w0] || 0);
    const pr2 = progress(viz, 1010, 650, 500, d.gauge.made, d.gauge.target || d.gauge.made, from);
    const stp = stamp(viz, 1300, 620, 'Target', 'g');
    const jP = cx.find('progress'), jT = cx.find('target');
    op(pr2.flag, 0);
    set = (t, i, there) => {
      const live = Math.max(0, s.pnl[Math.round(i)] || 0);
      pr2.set(there ? lerp(live, d.gauge.made, jP >= 0 ? cx.cue(t, jP, 0, 0.9) : 1) : live);
      op(pr2.flag, jT >= 0 ? k(t, cx.at[jT][0], cx.at[jT][0] + 0.4) : 1);
      if (jT >= 0) stp.go(t, lerp(cx.at[jT][0], cx.at[jT][1], 0.8));
      const jC0 = cx.find('close');
      if (jC0 >= 0 && t > cx.at[jC0][0]) op(stp, 1 - k(t, cx.at[jC0][0], cx.at[jC0][0] + 0.5));
    };
  } else if (d.family === 'stop' && d.gauge && d.gauge.stop != null) {
    const gg = d.gauge, base = gg.base ?? gg.now * 0.9, top = Math.max(gg.now || 0, gg.stop) * 1.12;
    const y0 = 740, hh = 120, yv = v => y0 - (v / top) * hh;
    E('line', { x1: 1040, x2: 1500, y1: y0, y2: y0, class: 'axis' }, viz);
    const bar = E('rect', { x: 1110, width: 140, class: 'bar big' }, viz);
    const val = T(viz, 1180, 0, '', 'barv');
    E('line', { x1: 1090, x2: 1270, y1: yv(gg.stop), y2: yv(gg.stop), class: 'rule-line' }, viz);
    T(viz, 1276, yv(gg.stop) + 5, `stop ${px(gg.stop)}`, 'rulet', 'start');
    T(viz, 1290, y0 - 10, 'call + put together', 'barl', 'start');
    const stp = stamp(viz, 1430, 640, 'Stop', 'r');
    const jM = cx.find('meet'), jSt = cx.find('stop');
    set = t => {
      const v = lerp(base, gg.now ?? gg.stop, jSt >= 0 ? cx.cue(t, jSt, 0, 0.7) : jM >= 0 ? cx.cue(t, jM) : 1);
      bar.setAttribute('y', yv(v).toFixed(1)); bar.setAttribute('height', (y0 - yv(v)).toFixed(1));
      bar.setAttribute('class', `bar big ${v > gg.stop ? 'under' : ''}`);
      val.setAttribute('y', (yv(v) - 10).toFixed(1)); txt(val, px(v));
      if (jSt >= 0) stp.go(t, lerp(cx.at[jSt][0], cx.at[jSt][1], 0.8));
    };
  } else if (d.family === 'entry') {
    const coll = G(viz);
    if (d.collected != null) {
      T(coll, 1010, 640, 'collected up front', 'fl', 'start');
      coll.v = T(coll, 1010, 692, '', 'big good', 'start');
      T(coll, 1010, 728, 'the most this can make', 'mono mute', 'start');
    }
    op(coll, 0);
    const jC = cx.find('collect');
    set = (t, i, there, rt) => {
      if (jC >= 0) { op(coll, k(t, cx.at[jC][0], cx.at[jC][0] + 0.3)); if (coll.v) countUp(coll.v, t, cx.at[jC][0], lerp(cx.at[jC][0], cx.at[jC][1], 0.5), 0, d.collected, v => inr(v)); }
      if (zone && jC >= 0) op(zone, k(t, lerp(cx.at[jC][0], cx.at[jC][1], 0.45), lerp(cx.at[jC][0], cx.at[jC][1], 0.6)) * (0.85 + 0.15 * Math.sin(rt * 2)));
    };
  } else if (d.values && d.values.length) {
    const cards = d.values.map((x, i) => {
      const g = G(viz, 'vcard');
      const xx = 980 + (i % 2) * 284, yy = 590 + Math.floor(i / 2) * 88;
      E('rect', { x: xx, y: yy, width: 276, height: 80, rx: 10 }, g);
      T(g, xx + 18, yy + 30, x.k, 'vk', 'start');
      T(g, xx + 18, yy + 66, fmt2(x.v), 'vv', 'start');
      op(g, 0);
      return g;
    });
    set = t => cards.forEach((c, i) => pop(c, t, lerp(cx.at[0][0], cx.at[0][1], 0.3 + 0.12 * i), 0.4));
  }
  // what it booked
  const jB = cx.steps.findIndex(x => /\b(books|costs)\b/.test(x.text));
  const tB = jB >= 0 ? lerp(cx.at[jB][0], cx.at[jB][1], 0.5) : (tC.length ? Math.max(...tC) : 1e9);
  const fx = d.booked != null && jB >= 0 ? (d.booked >= 0 ? burst(p, 1300, 220, { seed: at + 3 }) : puff(p, 1300, 240)) : null;
  let note = null;
  const jN = cx.find('note');
  if (jN >= 0) { note = T(p, 60, 750, 'written in afterwards by a repair, from market prices', 'mono mute', 'start'); op(note, 0); }
  // the walk: from the trade before to this one, over every step that tells it
  let wl = 0;
  while (wl + 1 < cx.steps.length && ['spot', 'move', 'walk'].includes(cx.steps[wl + 1].cue) && ['spot', 'move', 'walk'].includes(cx.steps[wl].cue)) wl++;
  const wa = cx.at[0][0], wb = lerp(cx.at[wl][0], cx.at[wl][1], 0.9);
  return (t, rt) => {
    // the playhead plays the market from the trade before to this one while
    // that stretch is told, index and options and P&L together, then stays
    const i = lerp(frm, at, k(t, wa, wb));
    // short of the moment, the step before it: the trade's own step already holds what it opens
    const there = i >= at - 1e-6, pi = there ? at : Math.min(Math.round(clamp(i, w0, at)), d.pre ?? Math.max(w0, at - 1));
    // one step for every number shown: the index, each option, the positions, the P&L
    ch.reveal(i, rt);
    const iv = s.idx[pi];
    if (iv != null) txt(ch.lab, fmt(iv));
    txt(ixV, iv == null ? '' : `${st().index} ${fmt(iv)}`);
    const dv = iv != null && s.idx[w0] != null ? iv - s.idx[w0] : null;
    txt(ixD, dv == null || Math.round(Math.abs(dv)) < 1 ? '' : `${dv >= 0 ? '▲' : '▼'} ${fmt(Math.abs(dv))} since ${hm(s.t[w0])}`);
    ixD.setAttribute('class', `mono ixd ${dv > 0 ? 'good' : dv < 0 ? 'bad' : 'mute'}`);
    ixD.setAttribute('x', (60 + ixV.getComputedTextLength() + 18).toFixed(1));
    pop(ev, t, lerp(cx.at[0][0], cx.at[0][1], 0.75), 0.5);
    // new legs show once they're said; closes once they're said
    if (pr) {
      opens.forEach((f, k2) => { const x = pr.legs[f.leg]; if (x) { const o = there ? k(t, tO[k2], tO[k2] + 0.4) : 0; op(x.g, o); x.show = o; } });
      closes.forEach((f, k2) => { const x = pr.legs[f.leg]; if (x) x.showOut = there ? k(t, tC[k2], tC[k2] + 0.4) : 0; });
      pr.reveal(i, pi);
    }
    // the positions, and the P&L, at the playhead
    let rows, booked, total;
    if (!there) {
      rows = (s.held[pi] || []).map(rowOf);
      booked = s.booked[pi]; total = s.pnl[pi];
    } else {
      rows = (s.held[at] || []).filter(([j]) => !gone.has(j) && !fresh.has(j)).map(rowOf);
      closes.forEach((f, k2) => {
        const shut = t >= tC[k2];
        rows.push({ what: f.what, sold: f.sold, entry: f.entry, mark: f.price, pnl: f.pnl, cls: f.kind === 'CE' ? 'ce' : 'pe', state: shut ? 'closed' : '' });
      });
      opens.forEach((f, k2) => {
        if (t < tO[k2]) return;
        rows.push({ what: f.what, sold: f.sold, entry: f.price, mark: f.price, pnl: 0, cls: f.kind === 'CE' ? 'ce' : 'pe', state: 'new', o: k(t, tO[k2], tO[k2] + 0.4) });
      });
      total = d.pnl != null ? d.pnl : s.pnl[at];
      booked = d.booked_before == null ? s.booked[at] : (t >= tB ? d.booked_after : d.booked_before);
    }
    pos.set(rows);
    box.spark(there ? at : pi);
    const bookedNow = there && d.booked != null && jB >= 0 && t >= tB;
    box.set(there ? `P&L at ${hm(d.t)}` : 'P&L', there ? dayOf(d.t) : `${dayOf(s.t[pi])} ${hm(s.t[pi])}`, booked, total,
      bookedNow ? `booked on this trade ${sinr(d.booked)}` : '', d.booked);
    set(t, i, there, rt);
    if (fx) fx.go(t, lerp(cx.at[jB][0], cx.at[jB][1], 0.7));
    if (note) fade(note, t, cx.at[jN][0]);
  };
};
// ── 4b. a run: trades between the full ones, as quick beats ────────────
// The same desk, the market playing on: each step walks the playhead to
// the trade it tells, the positions and the P&L moving all the way; each
// trade is marked on the charts as it's reached, and logged at the bottom
// right with what it booked, a counter showing how far through the run it is.
DRAW.run = (p, sc, cx) => {
  const d = sc.data, s = S(), Lg = st().legs;
  const [w0, w1] = d.w, beats = d.beats, segs = d.segs;
  const last = beats[beats.length - 1].at;
  const opt = Lg.some(l => isOpt(l) && l.a <= w1 && (l.b == null || l.b >= w0));
  const strikes = Lg.filter(l => l.strike && l.a <= w1 && (l.b == null || l.b >= w0)).map(l => l.strike);
  const ch = chart(p, { x: 60, y: 170, w: 820, h: opt ? 210 : 470 }, w0, w1, { strikes: true, extra: strikes, lastLabels: true });
  const ixV = T(p, 60, 150, '', 'bigs ixv', 'start'), ixD = T(p, 300, 150, '', 'mono ixd', 'start');
  let pr = null;
  if (opt) {
    T(p, 60, 436, 'Option prices', 'fl', 'start');
    pr = prices(p, { x: 60, y: 452, w: 820, h: 210 }, w0, w1, { labels: false, heads: true });
  }
  // each trade marked where it happened, once the playhead gets there
  const marks = beats.map(b => {
    const g = G(p, 'beatmk');
    E('line', { x1: ch.xs(b.at), x2: ch.xs(b.at), y1: 170, y2: opt ? 662 : 640, class: 'bline' }, g);
    E('circle', { cx: ch.xs(b.at), cy: 170 + (opt ? 210 : 470), r: 6, class: `bdot ${tone(b.booked)}` }, g);
    op(g, 0);
    return g;
  });
  const box = pnlBox(p, 980, 116, 560, { i0: w0, i1: w1 });
  let most = 0;
  for (let i = w0; i <= w1; i++) most = Math.max(most, (s.held[i] || []).length);
  const pos = positions(p, 980, 330, 560, most);
  let big = 1;
  for (let q = w0; q <= w1; q++) for (const [j, m] of s.held[q] || []) big = Math.max(big, Math.abs(legPnl(Lg[j], m) || 0));
  pos.scale(big);
  // the log: the counter and the last three trades
  const log = G(p, 'blog');
  T(log, 980, 600, d.noun.replace(/s$/, '') === 'roll' ? 'Rolls' : 'Trades', 'fl', 'start');
  const cnt = T(log, 1540, 600, '', 'mono', 'end');
  const lines = [0, 1, 2].map(q => {
    const g = G(log, 'bline2');
    E('rect', { x: 980, y: 612 + q * 50, width: 560, height: 42, rx: 8, class: 'pbg' }, g);
    const a = T(g, 998, 640 + q * 50, '', 'mono', 'start'), b = T(g, 1524, 640 + q * 50, '', 'pp', 'end');
    op(g, 0);
    return { g, a, b };
  });
  const said = b => {
    const shut = b.fills.filter(f => !f.opening).map(f => f.what), opened = b.fills.filter(f => f.opening).map(f => f.what);
    const k2 = shut.length === 1 && opened.length === 1 && shut[0].split(' ')[1] === opened[0].split(' ')[1]
      ? `${shut[0].split(' ')[1]} ${shut[0].split(' ')[0]} → ${opened[0].split(' ')[0]}` : [...shut.map(x => `− ${x}`), ...opened.map(x => `+ ${x}`)].join(', ');
    return `${hm(b.t)}  ${k2}`;
  };
  // the playhead's stops: each step walks to its segment's last trade
  const stops = segs.map(g => g.to);
  return (t, rt, j) => {
    const jj = Math.min(j, stops.length - 1);
    const a = jj === 0 ? w0 : stops[jj - 1], b = stops[jj];
    const f = k(t, cx.at[jj][0], lerp(cx.at[jj][0], cx.at[jj][1], 0.85));
    const i = lerp(a, b, f), pi = Math.round(i);
    ch.reveal(i, rt);
    const iv = s.idx[pi];
    if (iv != null) txt(ch.lab, fmt(iv));
    txt(ixV, iv == null ? '' : `${st().index} ${fmt(iv)}`);
    const dv = iv != null && s.idx[w0] != null ? iv - s.idx[w0] : null;
    txt(ixD, dv == null || Math.round(Math.abs(dv)) < 1 ? '' : `${dv >= 0 ? '▲' : '▼'} ${fmt(Math.abs(dv))} since ${hm(s.t[w0])}`);
    ixD.setAttribute('class', `mono ixd ${dv > 0 ? 'good' : dv < 0 ? 'bad' : 'mute'}`);
    ixD.setAttribute('x', (60 + ixV.getComputedTextLength() + 18).toFixed(1));
    if (pr) pr.reveal(i, pi);
    pos.set((s.held[pi] || []).map(([q, m]) => { const L = Lg[q]; return { what: L.what, sold: L.sold, entry: L.entry, mark: m, pnl: legPnl(L, m), cls: sideCls(L) }; }));
    box.set('P&L', `${dayOf(s.t[pi])} ${hm(s.t[pi])}`, s.booked[pi], s.pnl[pi], '', null);
    box.spark(pi);
    const done = beats.filter(x => x.at <= pi + 1e-9);
    marks.forEach((m, q) => op(m, beats[q].at <= i + 1e-9 ? 1 : 0));
    txt(cnt, done.length ? `${done.length} of ${beats.length}` : '');
    const recent = done.slice(-3).reverse();
    lines.forEach((l, q) => {
      const x = recent[q];
      if (!x) { op(l.g, 0); return; }
      op(l.g, 1);
      l.g.setAttribute('class', `bline2 ${q === 0 ? 'now' : ''}`);
      txt(l.a, said(x));
      txt(l.b, x.booked == null ? '' : sinr(x.booked));
      l.b.setAttribute('class', `pp ${tone(x.booked)}`);
    });
  };
};

function fmt2(v) { return Math.abs(v) < 10 && v % 1 ? v.toFixed(2) : fmt(v); }

// ── the smaller trades ─────────────────────────────────────────────────
DRAW.more = (p, sc, cx) => {
  const s = S(), n = s.idx.length - 1;
  const ch = chart(p, { x: 80, y: 160, w: 1340, h: 380 }, 0, n, { faint: true });
  ch.reveal(n, 0, false);
  const ms = sc.data.marks.map((m, i) => {
    const g = G(p, 'mark');
    E('line', { x1: ch.xs(m.at), x2: ch.xs(m.at), y1: 160, y2: 540, class: 'evline' }, g);
    T(g, ch.xs(m.at), 580 + (i % 3) * 30, `${m.label}${m.booked != null ? ` ${sinr(m.booked)}` : ''}`, `markt ${tone(m.booked)}`);
    op(g, 0);
    return g;
  });
  return t => ms.forEach((g, i) => pop(g, t, lerp(cx.at[0][0], cx.at[0][1], 0.1 + 0.8 * i / Math.max(1, ms.length)), 0.4));
};

// ── 5. what held it back ───────────────────────────────────────────────
DRAW.checks = (p, sc, cx) => {
  const s = S(), n = s.idx.length - 1;
  const ch = chart(p, { x: 80, y: 300, w: 1340, h: 330 }, 0, n, { faint: true, strikes: true });
  ch.reveal(n, 0, false);
  const marks = sc.data.marks.map(m => {
    const g = G(p, `cmark ${m.blocked ? 'blocked' : ''}`);
    E('line', { x1: ch.xs(m.at), x2: ch.xs(m.at), y1: 300, y2: 630, class: 'cline' }, g);
    E('circle', { cx: ch.xs(m.at), cy: 300, r: 9 }, g);
    op(g, 0);
    const bx = clamp(ch.xs(m.at), 300, 1300);
    const b = G(p, 'cbub');
    const lines = wrap(m.text, 58);
    E('rect', { x: bx - 290, y: 140, width: 580, height: 34 + lines.length * 28, rx: 12 }, b);
    lines.forEach((l, i) => T(b, bx, 172 + i * 28, l, 'cbt'));
    op(b, 0);
    return { g, b };
  });
  const js = cx.all('mark');
  return (t, rt, j) => marks.forEach((m, i) => {
    const jj = js[i];
    if (jj == null) return;
    pop(m.g, t, cx.at[jj][0], 0.4);
    const on = j === jj;
    op(m.b, on ? Math.min(k(t, cx.at[jj][0], cx.at[jj][0] + 0.3), 1) : 0);
    if (on) look(m.b, { dy: Math.sin(rt * 2.2) * 2 });
  });
};

// ── 6. the P&L on the way ──────────────────────────────────────────────
// the total P&L and, dashed, what was booked; above it, as the line
// draws, booked, open and total at that moment
DRAW.journey = (p, sc, cx) => {
  const s = S(), n = s.pnl.length - 1, d = sc.data;
  const ch = chart(p, { x: 80, y: 200, w: 1340, h: 430 }, 0, n, { key: 'pnl', money: true, zero: true, area: true, also: ['booked'] });
  const trades = st().events || st().scenes.filter(x => x.kind === 'trade').map(x => x.data);
  const dots = trades.map(x => { const c = E('circle', { cx: ch.xs(x.at), cy: ch.ys(s.pnl[x.at] || 0), r: 7, class: 'evdot' }, p); op(c, 0); return { c, at: x.at }; });
  const marks = [];
  for (const [i, lbl] of [[d.hi, 'best'], [d.lo, 'lowest']]) {
    if (i == null || s.pnl[i] == null) continue;
    const g = G(p, 'mark');
    E('circle', { cx: ch.xs(i), cy: ch.ys(s.pnl[i]), r: 10, class: lbl === 'best' ? 'high' : 'low' }, g);
    T(g, clamp(ch.xs(i), 160, 1340), ch.ys(s.pnl[i]) + (lbl === 'best' ? -22 : 36), `${lbl} ${sinr(s.pnl[i])}`, `markt ${lbl === 'best' ? 'high' : 'low'}`);
    op(g, 0);
    marks.push(g);
  }
  const lgd = G(p, 'legend');
  E('line', { x1: 80, x2: 116, y1: 694, y2: 694, class: 'ln-pnl' }, lgd); T(lgd, 126, 700, 'total P&L', 'mono mute', 'start');
  E('line', { x1: 290, x2: 326, y1: 694, y2: 694, class: 'ln-booked' }, lgd); T(lgd, 336, 700, 'booked', 'mono mute', 'start');
  T(lgd, 470, 700, 'the gap between the two lines is the open positions', 'mono mute', 'start');
  const rd = G(p, 'readout');
  const cols = ['Booked', 'Open', 'Total'].map((l, q) => { T(rd, 640 + q * 220, 128, l, 'fl', 'start'); return T(rd, 640 + q * 220, 168, '', 'bigs', 'start'); });
  const at = T(rd, 1420, 168, '', 'mono mute', 'end');
  const jD = cx.find('draw'), jE = cx.find('extremes');
  return (t, rt) => {
    const i = lerp(0, n, jD >= 0 ? cx.cue(t, jD, 0, 0.95) : 1);
    ch.reveal(i, rt, t < (cx.at[jD] || [0, 0])[1]);
    dots.forEach(x => op(x.c, i >= x.at ? 1 : 0));
    marks.forEach((m, q) => pop(m, t, jE >= 0 ? lerp(cx.at[jE][0], cx.at[jE][1], 0.1 + 0.45 * q) : 1e9, 0.5));
    const pi = Math.round(i), b = s.booked[pi], tot = s.pnl[pi];
    [b, tot == null || b == null ? null : tot - b, tot].forEach((v, q) => { txt(cols[q], sinr(v)); cols[q].setAttribute('class', `bigs ${tone(v)}`); });
    txt(at, `${dayOf(s.t[pi])} ${hm(s.t[pi])}`);
  };
};

// ── 7. where the money came from ───────────────────────────────────────
DRAW.split = (p, sc, cx) => {
  const d = sc.data;
  const parts = [['time passing', d.time, 'time'], [`${st().index}'s move`, d.market, 'market'], ['volatility', d.vol, 'vol']];
  let run = 0;
  const lv = [0];
  parts.forEach(x => { run += x[1]; lv.push(run); });
  const lo = Math.min(0, ...lv), hi = Math.max(0, ...lv);
  const y0 = 170, hh = 440, yv = v => y0 + hh - (v - lo) / ((hi - lo) || 1) * hh;
  E('line', { x1: 140, x2: 1460, y1: yv(0), y2: yv(0), class: 'zero' }, p);
  const bars = parts.concat([['together', run, 'sum']]).map((x, i) => {
    const a = x[2] === 'sum' ? 0 : lv[i], b = x[2] === 'sum' ? run : lv[i + 1];
    const g = G(p, `wf ${x[2]}`);
    const r = E('rect', { x: 200 + i * 320, width: 200, class: `wfb ${x[1] >= 0 ? 'pos' : 'neg'} ${x[2] === 'sum' ? 'sum' : ''}` }, g);
    const v = T(g, 300 + i * 320, 0, '', `wfv ${tone(x[1])}`);
    T(g, 300 + i * 320, y0 + hh + 46, x[0], 'wfl');
    op(g, 0);
    return { g, r, v, a, b, val: x[1], cue: x[2] };
  });
  T(p, 80, 720, 'an estimate: each leg revalued one change at a time, at the volatility it was sold at', 'mono mute', 'start');
  return t => bars.forEach(b => {
    const j = cx.find(b.cue);
    const f = j >= 0 ? cx.cue(t, j, 0, 0.7) : 0;
    op(b.g, j >= 0 ? k(t, cx.at[j][0], cx.at[j][0] + 0.2) : 0);
    const top = lerp(b.a, b.a + (b.b - b.a) * f, 1);
    const y1 = Math.min(yv(b.a), yv(top)), y2 = Math.max(yv(b.a), yv(top));
    b.r.setAttribute('y', y1.toFixed(1)); b.r.setAttribute('height', Math.max(1, y2 - y1).toFixed(1));
    b.v.setAttribute('y', (y1 - 14).toFixed(1));
    txt(b.v, sinr(b.val * f));
  });
};

// ── 8. where a running cycle stands ────────────────────────────────────
DRAW.now = (p, sc, cx) => {
  const d = sc.data, s = S(), n = s.idx.length - 1;
  const live = G(p, 'live');
  E('rect', { x: 1300, y: 104, width: 240, height: 38, rx: 19 }, live);
  E('circle', { cx: 1324, cy: 123, r: 7, class: 'livedot' }, live);
  T(live, 1342, 130, `LIVE · ${hm(st().as_of)}`, 'livet', 'start');
  // the same P&L box and positions table as the trades: booked, open, total; each leg and its P&L
  const box = pnlBox(p, 80, 116, 640);
  box.set(`P&L now`, `${dayOf(st().as_of)} ${hm(st().as_of)}`, d.booked ?? null, d.total ?? s.pnl[n], '', null);
  op(box.g, 0);
  const pos = positions(p, 80, 290, 640, d.held.length);
  const rows = d.held.map(h => ({ what: h.what, sold: h.sold, entry: h.entry, mark: h.mark, cls: h.kind === 'CE' ? 'ce' : h.kind === 'PE' ? 'pe' : 'fut',
    pnl: h.entry == null || h.mark == null ? null : (h.mark - h.entry) * (h.qty || 0) * (h.sold ? -1 : 1) }));
  let bal = null, pr = null, band = null;
  if (d.gauge) { bal = balance(p, 900, 220, 190, { ...d.gauge, small0: d.gauge.small, big0: d.gauge.big }); bal.set(d.gauge.big, d.gauge.small, d.gauge.small / d.gauge.big, d.gauge.small < d.gauge.line * d.gauge.big ? 'under' : ''); op(bal.g, 0); }
  if (d.target) { pr = progress(p, 900, 560, 600, d.target.made, d.target.target, 0); op(pr.g, 0); }
  const legs = st().legs.filter(l => l.b == null && l.strike);
  const calls = legs.filter(l => l.kind === 'CE').map(l => l.strike), puts = legs.filter(l => l.kind === 'PE').map(l => l.strike);
  const spot = s.idx[n];
  if (spot && calls.length && puts.length) {
    band = G(p, 'bandd');
    const kc = Math.min(...calls), kp = Math.max(...puts), lo = Math.min(kp, spot), hi = Math.max(kc, spot);
    const xv = v => 120 + (v - lo) / ((hi - lo) || 1) * 620;
    E('line', { x1: 100, x2: 760, y1: 640, y2: 640, class: 'axis' }, band);
    E('line', { x1: xv(kp), x2: xv(kp), y1: 610, y2: 670, class: 'band pe' }, band);
    E('line', { x1: xv(kc), x2: xv(kc), y1: 610, y2: 670, class: 'band ce' }, band);
    T(band, xv(kp), 696, `put ${fmt(kp)}`, 'bandt pe');
    T(band, xv(kc), 696, `call ${fmt(kc)}`, 'bandt ce');
    band.dot = E('circle', { cx: xv(spot), cy: 640, r: 10, class: 'dot idx' }, band);
    T(band, xv(spot), 600, `${st().index} ${fmt(spot)}`, 'headt');
    op(band, 0);
  }
  const jL = cx.find('legs'), jR = cx.find('ratio'), jT = cx.find('target'), jB = cx.find('band');
  return (t, rt) => {
    op(live.querySelector('.livedot'), 0.5 + 0.5 * Math.sin(rt * 4));
    op(box.g, k(t, cx.at[jL][0], cx.at[jL][0] + 0.4));
    pos.set(rows.map((r, i) => ({ ...r, o: k(t, lerp(cx.at[jL][0], cx.at[jL][1], 0.1 + 0.15 * i), lerp(cx.at[jL][0], cx.at[jL][1], 0.1 + 0.15 * i) + 0.4) })));
    if (bal) op(bal.g, jR >= 0 ? k(t, cx.at[jR][0], cx.at[jR][0] + 0.4) : 0);
    if (pr) { op(pr.g, jT >= 0 ? k(t, cx.at[jT][0], cx.at[jT][0] + 0.3) : 0); pr.set(lerp(0, d.target.made, jT >= 0 ? cx.cue(t, jT, 0, 0.8) : 0)); }
    if (band) { op(band, jB >= 0 ? k(t, cx.at[jB][0], cx.at[jB][0] + 0.4) : 0); band.dot.setAttribute('r', (9 + 3 * Math.sin(rt * 4)).toFixed(2)); }
  };
};

// ── 9. the end ─────────────────────────────────────────────────────────
DRAW.wrap = (p, sc, cx) => {
  const d = sc.data;
  T(p, W / 2, 210, `${st().episode}${d.live ? ' so far' : ''}`, 'bigs');
  const v = T(p, W / 2, 300, '', `huge ${tone(d.total)}`);
  const sum = G(p);
  const parts = [['booked', d.booked]].concat(Math.abs(d.open || 0) >= 1 ? [['open', d.open]] : []);
  const pw = parts.map(([l, x]) => `${l} ${sinr(x)}`.length * 10.5 + 28);
  let x0 = (W - (pw.reduce((a, b) => a + b, 0) + 14 * (parts.length - 1))) / 2;
  parts.forEach(([l, x], q) => { chip(sum, x0, 360, `${l} ${sinr(x)}`, q ? 'soft' : ''); x0 += pw[q] + 14; });
  op(sum, 0);
  const list = (d.trades || st().scenes.filter(x => x.kind === 'trade').map(x => ({ title: x.title, booked: x.data.booked }))).slice(0, 12);
  const label = c => `${c.title}${c.booked != null ? `  ${sinr(c.booked)}` : ''}`;
  const g = G(p);
  const widths = list.map(c => label(c).length * 10.5 + 28), rows = [[]];
  let rowW = 0;
  widths.forEach((w, i) => { if (rowW + w > 1300 && rows[rows.length - 1].length) { rows.push([]); rowW = 0; } rows[rows.length - 1].push(i); rowW += w + 14; });
  const cs = [];
  rows.forEach((r, ri) => {
    let x = (W - (r.reduce((a, i) => a + widths[i] + 14, 0) - 14)) / 2;
    for (const i of r) { const ch = chip(g, x, 450 + ri * 52, label(list[i]), 'soft'); op(ch, 0); cs[i] = ch; x += widths[i] + 14; }
  });
  T(p, W / 2, 720, 'Made by Linck from the strategy’s own record: its trades, the rules it acted on, and market prices.', 'mono mute');
  return t => {
    countUp(v, t, 0, cx.at[0][1] * 0.5, 0, d.total, x => sinr(x));
    pop(sum, t, cx.at[0][1] * 0.3, 0.5);
    cs.forEach((c, i) => pop(c, t, lerp(cx.at[0][0], cx.at[0][1], 0.35 + 0.5 * i / Math.max(1, cs.length)), 0.4));
  };
};

DRAW.blank = () => () => {};
