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
  for (let i = i0; i <= i1; i++) if (s[key][i] != null) vals.push(s[key][i]);
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
    g, xs, ys, lo, hi, bands, body,
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

// a fill, as a ticket: SELL 24,800 put @ ₹118, and what it booked
function ticket(p, x, y, w, f) {
  const g = G(p, `ticket ${f.verb.startsWith('Sell') ? 'sell' : 'buy'}`);
  E('rect', { x, y, width: w, height: 58, rx: 8 }, g);
  E('rect', { x, y, width: 8, height: 58, rx: 3, class: 'strip' }, g);
  T(g, x + 24, y + 25, f.verb.toUpperCase(), 'tv', 'start');
  T(g, x + 24, y + 47, f.what, 'tw', 'start');
  T(g, x + w - 18, y + 25, `@ ${px(f.price)}`, 'tp', 'end');
  if (!f.opening && f.pnl != null) T(g, x + w - 18, y + 47, `${f.pnl >= 0 ? 'booked' : 'cost'} ${sinr(f.pnl)}`, `tb ${tone(f.pnl)}`, 'end');
  else T(g, x + w - 18, y + 47, `× ${fmt(f.qty)}`, 'tq', 'end');
  op(g, 0);
  return g;
}
function slideIn(el, t, a, d = 0.45) {
  const f = k(t, a, a + d);
  op(el, Math.min(1, f * 2));
  el.setAttribute('transform', `translate(${((1 - backOut(f)) * 60).toFixed(1)},0)`);
}

// two bars and the line the rule acts at (the strangle's balance)
function balance(p, x, y, h, g0) {
  const g = G(p, 'bal');
  const top = Math.max(g0.big, g0.small, g0.big0 || 0, g0.small0 || 0, g0.after || 0) * 1.12 || 1;
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
  const R = G(p);
  T(R, 900, 200, d.live ? 'P&L so far' : 'P&L', 'fl', 'start');
  const big = T(R, 900, 268, '', `huge ${tone(d.total)}`, 'start');
  const chips = G(R);
  const b1 = chip(chips, 900, 320, `booked ${sinr(d.booked)}`);
  if (Math.abs(d.open) >= 1) chip(chips, 900 + b1.w + 12, 320, `open ${sinr(d.open)}`, 'soft');
  const n = s.pnl.length - 1;
  const ch = chart(R, { x: 900, y: 400, w: 560, h: 220 }, 0, n, { key: 'pnl', money: true, zero: true, area: true });
  const trades = st().scenes.filter(x => x.kind === 'trade').map(x => x.data.at);
  const dots = trades.map(i => { const c = E('circle', { cx: ch.xs(i), cy: ch.ys(s.pnl[i] || 0), r: 7, class: 'evdot' }, R); op(c, 0); return c; });
  const cnt = T(R, 900, 690, `${d.trades} trade${d.trades === 1 ? '' : 's'}`, 'bigs', 'start');
  const jH = cx.find('headline'), jC = cx.find('count');
  return (t, rt) => {
    pop(L, t, 0, 0.6);
    if (live) op(live.querySelector('.livedot'), 0.5 + 0.5 * Math.sin(rt * 4));
    op(R, k(t, cx.at[jH][0], cx.at[jH][0] + 0.4));
    countUp(big, t, cx.at[jH][0], cx.at[jH][1] * 0.8 + cx.at[jH][0] * 0.2, 0, d.total, v => sinr(v));
    pop(chips, t, lerp(cx.at[jH][0], cx.at[jH][1], 0.7), 0.5);
    ch.reveal(lerp(0, n, cx.cue(t, jC, 0, 0.8)), rt);
    op(ch.g, k(t, cx.at[jC][0], cx.at[jC][0] + 0.3));
    dots.forEach((c, i) => pop(c, t, lerp(cx.at[jC][0], cx.at[jC][1], 0.2 + 0.6 * (trades[i] / Math.max(1, n))), 0.4));
    pop(cnt, t, lerp(cx.at[jC][0], cx.at[jC][1], 0.7), 0.5);
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
DRAW.market = (p, sc, cx) => {
  const s = S(), n = s.idx.length - 1, d = sc.data;
  const strikes = st().legs.filter(l => l.strike).map(l => l.strike);
  const ch = chart(p, { x: 80, y: 130, w: 1340, h: 410 }, 0, n, { strikes: true, extra: strikes });
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
  let vx = null;
  if (s.vix.some(v => v != null)) {
    T(p, 80, 612, 'India VIX', 'fl', 'start');
    vx = chart(p, { x: 80, y: 624, w: 1340, h: 104 }, 0, n, { key: 'vix' });
  }
  const jD = cx.find('draw'), jE = cx.find('extremes'), jS = cx.find('strikes'), jV = cx.find('vix');
  return (t, rt) => {
    ch.reveal(lerp(0, n, jD >= 0 ? cx.cue(t, jD, 0, 0.9) : 1), rt, jD >= 0 && t < cx.at[jD][1] + 0.01);
    marks.forEach((m, i) => pop(m, t, jE >= 0 ? lerp(cx.at[jE][0], cx.at[jE][1], 0.1 + 0.4 * i) : 1e9, 0.5));
    if (jS >= 0) op(ch.bands, cx.cue(t, jS, 0, 0.4));
    if (vx) { op(vx.g, jV >= 0 ? k(t, cx.at[jV][0], cx.at[jV][0] + 0.3) : 0); vx.reveal(lerp(0, n, jV >= 0 ? cx.cue(t, jV, 0, 0.9) : 0), rt, jV >= 0 && t < cx.at[jV][1]); }
  };
};

// ── 4. a trade ─────────────────────────────────────────────────────────
DRAW.trade = (p, sc, cx) => {
  const d = sc.data, s = S();
  const [w0, w1] = d.w, at = d.at;
  const strikes = d.fills.map(f => f.strike).concat(st().legs.filter(l => l.strike && l.a <= w1 && (l.b == null || l.b >= w0)).map(l => l.strike));
  const ch = chart(p, { x: 60, y: 130, w: 860, h: 430 }, w0, w1, { strikes: true, extra: strikes });
  // the moment
  const ev = G(p, 'event');
  E('line', { x1: ch.xs(at), x2: ch.xs(at), y1: 116, y2: 568, class: 'evline' }, ev);
  const lab = sc.title;
  E('rect', { x: ch.xs(at) - lab.length * 5.6 - 14, y: 96, width: lab.length * 11.2 + 28, height: 34, rx: 17, class: 'evpill' }, ev);
  T(ev, ch.xs(at), 119, lab, 'evt');
  op(ev, 0);
  // the profit zone between the sold strikes, from the entry on
  let zone = null;
  const sold = d.fills.filter(f => f.opening && f.verb.startsWith('Sell') && f.strike);
  if (d.family === 'entry' && sold.length >= 2) {
    const ks = sold.map(f => f.strike).sort((a, b) => a - b);
    zone = G(p, 'zone');
    E('rect', { x: ch.xs(at), y: ch.ys(ks[ks.length - 1]), width: ch.xs(w1) - ch.xs(at), height: ch.ys(ks[0]) - ch.ys(ks[ks.length - 1]), class: 'zone' }, zone);
    T(zone, (ch.xs(at) + ch.xs(w1)) / 2, (ch.ys(ks[0]) + ch.ys(ks[ks.length - 1])) / 2 + 6, 'it profits while the price stays in here', 'zonet');
    op(zone, 0);
  }
  // the P&L, as it goes
  const pnlG = G(p, 'pnlchip');
  E('rect', { x: 980, y: 130, width: 560, height: 74, rx: 12 }, pnlG);
  T(pnlG, 1004, 160, 'P&L', 'fl', 'start');
  const pnlV = T(pnlG, 1004, 192, '', 'pnlv', 'start');
  const timeV = T(pnlG, 1516, 192, '', 'mono mute', 'end');
  // what the rule saw
  const viz = G(p);
  let set = () => {};
  const jFirst = 0, last = cx.at.length - 1;
  const after = t => k(t, cx.at[Math.min(1, last)][0], cx.at[last][1]);   // the chart after the moment
  if (d.family === 'balance' && d.gauge) {
    const g0 = { ...d.gauge };
    const side = g0.small_side === 'put' ? 'pe' : 'ce', bside = side === 'pe' ? 'ce' : 'pe';
    g0.small0 = s[side][w0] ?? g0.small; g0.big0 = s[bside][w0] ?? g0.big;
    const b = balance(viz, 1010, 280, 170, g0);
    const stp = stamp(viz, 1330, 300, d.trigger === 'eod_gap_check' ? 'Gap' : 'Roll', 'r');
    const jS = cx.find('sides'), jR = cx.find('ratio'), jO = cx.find('open');
    set = t => {
      const f = jS >= 0 ? cx.cue(t, jS, 0, 0.8) : 1;
      let big = lerp(g0.big0, g0.big, f), small = lerp(g0.small0, g0.small, f);
      const under = small < g0.line * big;
      let state = under && (jR < 0 || t >= cx.at[jR][0]) ? 'under' : '';
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
    const pr = progress(viz, 1010, 300, 500, d.gauge.made, d.gauge.target || d.gauge.made, from);
    const stp = stamp(viz, 1260, 430, 'Target', 'g');
    const jP = cx.find('progress'), jT = cx.find('target');
    op(pr.flag, 0);
    set = t => {
      pr.set(lerp(from, d.gauge.made, jP >= 0 ? cx.cue(t, jP, 0, 0.9) : 1));
      op(pr.flag, jT >= 0 ? k(t, cx.at[jT][0], cx.at[jT][0] + 0.4) : 1);
      if (jT >= 0) stp.go(t, lerp(cx.at[jT][0], cx.at[jT][1], 0.8));
      const jC0 = cx.find('close');
      if (jC0 >= 0 && t > cx.at[jC0][0]) op(stp, 1 - k(t, cx.at[jC0][0], cx.at[jC0][0] + 0.5));
    };
  } else if (d.family === 'stop' && d.gauge && d.gauge.stop != null) {
    const gg = d.gauge, base = gg.base ?? gg.now * 0.9, top = Math.max(gg.now || 0, gg.stop) * 1.12;
    const y0 = 460, hh = 200, yv = v => y0 - (v / top) * hh;
    E('line', { x1: 1040, x2: 1500, y1: y0, y2: y0, class: 'axis' }, viz);
    const bar = E('rect', { x: 1180, width: 160, class: 'bar big' }, viz);
    const val = T(viz, 1260, 0, '', 'barv');
    E('line', { x1: 1150, x2: 1370, y1: yv(gg.stop), y2: yv(gg.stop), class: 'rule-line' }, viz);
    T(viz, 1376, yv(gg.stop) + 5, `stop ${px(gg.stop)}`, 'rulet', 'start');
    T(viz, 1260, y0 + 30, 'call + put together', 'barl');
    const stp = stamp(viz, 1260, 300, 'Stop', 'r');
    const jM = cx.find('meet'), jSt = cx.find('stop');
    set = t => {
      const v = lerp(base, gg.now ?? gg.stop, jSt >= 0 ? cx.cue(t, jSt, 0, 0.7) : jM >= 0 ? cx.cue(t, jM) : 1);
      bar.setAttribute('y', yv(v).toFixed(1)); bar.setAttribute('height', (y0 - yv(v)).toFixed(1));
      bar.setAttribute('class', `bar big ${v > gg.stop ? 'under' : ''}`);
      val.setAttribute('y', (yv(v) - 10).toFixed(1)); txt(val, px(v));
      if (jSt >= 0) stp.go(t, lerp(cx.at[jSt][0], cx.at[jSt][1], 0.8));
    };
  } else if (d.family === 'entry') {
    const g = G(viz);
    T(g, 1010, 270, st().index, 'fl', 'start');
    T(g, 1010, 320, d.spot ? fmt(d.spot) : '—', 'big', 'start');
    const coll = G(viz);
    if (d.collected != null) {
      T(coll, 1010, 400, 'collected up front', 'fl', 'start');
      coll.v = T(coll, 1010, 452, '', 'big good', 'start');
    }
    op(g, 0); op(coll, 0);
    const jC = cx.find('collect');
    set = (t, rt) => {
      pop(g, t, 0.2, 0.5);
      if (jC >= 0) { op(coll, k(t, cx.at[jC][0], cx.at[jC][0] + 0.3)); if (coll.v) countUp(coll.v, t, cx.at[jC][0], lerp(cx.at[jC][0], cx.at[jC][1], 0.5), 0, d.collected, v => inr(v)); }
      if (zone && jC >= 0) op(zone, k(t, lerp(cx.at[jC][0], cx.at[jC][1], 0.45), lerp(cx.at[jC][0], cx.at[jC][1], 0.6)) * (0.85 + 0.15 * Math.sin(rt * 2)));
    };
  } else if (d.values && d.values.length) {
    const cards = d.values.map((x, i) => {
      const g = G(viz, 'vcard');
      const xx = 1010 + (i % 2) * 270, yy = 240 + Math.floor(i / 2) * 110;
      E('rect', { x: xx, y: yy, width: 250, height: 92, rx: 10 }, g);
      T(g, xx + 18, yy + 32, x.k, 'vk', 'start');
      T(g, xx + 18, yy + 72, fmt2(x.v), 'vv', 'start');
      op(g, 0);
      return g;
    });
    set = t => cards.forEach((c, i) => pop(c, t, lerp(cx.at[0][0], cx.at[0][1], 0.3 + 0.12 * i), 0.4));
  }
  // the fills, as tickets, one per step that says it
  const tk = G(p);
  const nT = Math.min(6, d.fills.length), gap = nT > 3 ? 6 : 10;
  const y0t = nT > 3 ? 740 - nT * (58 + gap) : 500;
  const tickets = d.fills.slice(0, 6).map((f, i) => ticket(tk, 980, y0t + i * (58 + gap), 560, f));
  const say = cx.steps.map((x, j) => (['legs', 'close', 'open'].includes(x.cue) ? j : -1)).filter(j => j >= 0);
  const when = tickets.map((_, i) => (say.length ? cx.at[say[Math.min(i, say.length - 1)]][0] + 0.3 : cx.at[last][0]));
  // what it booked
  const jB = cx.steps.findIndex(x => /\b(books|costs)\b/.test(x.text));
  const fx = d.booked != null && jB >= 0 ? (d.booked >= 0 ? burst(p, 1380, 520, { seed: at + 3 }) : puff(p, 1380, 560)) : null;
  let note = null;
  const jN = cx.find('note');
  if (jN >= 0) { note = T(p, 60, 620, 'written in afterwards by a repair, from market prices', 'mono mute', 'start'); op(note, 0); }
  return (t, rt) => {
    // the chart walks up to the moment, then on past it
    const i = t < cx.at[jFirst][1] ? lerp(w0 + (at - w0) * 0.25, at, cx.cue(t, jFirst, 0, 0.85)) : lerp(at, w1, after(t));
    const v = ch.reveal(i, rt);
    pop(ev, t, lerp(cx.at[0][0], cx.at[0][1], 0.75), 0.5);
    const pi = Math.round(clamp(i, w0, w1));
    txt(pnlV, sinr(s.pnl[pi])); pnlV.setAttribute('class', `pnlv ${tone(s.pnl[pi])}`);
    txt(timeV, `${dayOf(s.t[pi])} ${hm(s.t[pi])}`);
    void v;
    set(t, rt);
    tickets.forEach((g, n) => slideIn(g, t, when[n]));
    if (fx) fx.go(t, lerp(cx.at[jB][0], cx.at[jB][1], 0.7));
    if (note) fade(note, t, cx.at[jN][0]);
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
DRAW.journey = (p, sc, cx) => {
  const s = S(), n = s.pnl.length - 1, d = sc.data;
  const ch = chart(p, { x: 80, y: 130, w: 1340, h: 500 }, 0, n, { key: 'pnl', money: true, zero: true, area: true, also: ['booked'] });
  const trades = st().scenes.filter(x => x.kind === 'trade').map(x => x.data);
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
  T(p, 80, 700, '— total P&L     - - booked', 'mono mute', 'start');
  const endV = T(p, 1420, 120, '', 'big', 'end');
  const jD = cx.find('draw'), jE = cx.find('extremes'), jZ = cx.find('end');
  return (t, rt) => {
    const i = lerp(0, n, jD >= 0 ? cx.cue(t, jD, 0, 0.95) : 1);
    ch.reveal(i, rt, t < (cx.at[jD] || [0, 0])[1]);
    dots.forEach(x => op(x.c, i >= x.at ? 1 : 0));
    marks.forEach((m, q) => pop(m, t, jE >= 0 ? lerp(cx.at[jE][0], cx.at[jE][1], 0.1 + 0.45 * q) : 1e9, 0.5));
    if (jZ >= 0) { op(endV, k(t, cx.at[jZ][0], cx.at[jZ][0] + 0.3)); countUp(endV, t, cx.at[jZ][0], lerp(cx.at[jZ][0], cx.at[jZ][1], 0.6), 0, s.pnl[n] || 0, v => sinr(v)); endV.setAttribute('class', `big ${tone(s.pnl[n])}`); }
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
  const rows = d.held.slice(0, 5).map((h, i) => {
    const g = G(p, 'row');
    const y = 190 + i * 66;
    E('rect', { x: 80, y, width: 700, height: 56, rx: 8 }, g);
    T(g, 100, y + 36, h.what, 'roww', 'start');
    T(g, 470, y + 36, `${h.sold ? 'sold' : 'bought'} ${px(h.entry)}`, 'mono mute', 'start');
    T(g, 760, y + 36, `now ${px(h.mark)}`, `mono ${h.sold ? tone(h.entry - h.mark) : tone(h.mark - h.entry)}`, 'end');
    op(g, 0);
    return g;
  });
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
  T(p, 80, 470, 'P&L now', 'fl', 'start');
  T(p, 80, 526, sinr(s.pnl[n]), `big ${tone(s.pnl[n])}`, 'start');
  const jL = cx.find('legs'), jR = cx.find('ratio'), jT = cx.find('target'), jB = cx.find('band');
  return (t, rt) => {
    op(live.querySelector('.livedot'), 0.5 + 0.5 * Math.sin(rt * 4));
    rows.forEach((r, i) => slideIn(r, t, lerp(cx.at[jL][0], cx.at[jL][1], 0.1 + 0.15 * i)));
    if (bal) op(bal.g, jR >= 0 ? k(t, cx.at[jR][0], cx.at[jR][0] + 0.4) : 0);
    if (pr) { op(pr.g, jT >= 0 ? k(t, cx.at[jT][0], cx.at[jT][0] + 0.3) : 0); pr.set(lerp(0, d.target.made, jT >= 0 ? cx.cue(t, jT, 0, 0.8) : 0)); }
    if (band) { op(band, jB >= 0 ? k(t, cx.at[jB][0], cx.at[jB][0] + 0.4) : 0); band.dot.setAttribute('r', (9 + 3 * Math.sin(rt * 4)).toFixed(2)); }
  };
};

// ── 9. the end ─────────────────────────────────────────────────────────
DRAW.wrap = (p, sc, cx) => {
  const d = sc.data;
  T(p, W / 2, 250, `${st().episode}${d.live ? ' so far' : ''}`, 'bigs');
  const v = T(p, W / 2, 360, '', `huge ${tone(d.total)}`);
  const chaps = st().scenes.filter(x => x.kind === 'trade').slice(0, 12);
  const g = G(p);
  const widths = chaps.map(c => c.title.length * 10.5 + 28), rows = [[]];
  let rowW = 0;
  widths.forEach((w, i) => { if (rowW + w > 1300 && rows[rows.length - 1].length) { rows.push([]); rowW = 0; } rows[rows.length - 1].push(i); rowW += w + 14; });
  const cs = [];
  rows.forEach((r, ri) => {
    let x = (W - (r.reduce((a, i) => a + widths[i] + 14, 0) - 14)) / 2;
    for (const i of r) { const ch = chip(g, x, 470 + ri * 52, chaps[i].title, 'soft'); op(ch, 0); cs[i] = ch; x += widths[i] + 14; }
  });
  T(p, W / 2, 700, 'Made by Linck from the strategy’s own record: its trades, the rules it acted on, and market prices.', 'mono mute');
  return t => {
    countUp(v, t, 0, cx.at[0][1] * 0.5, 0, d.total, x => sinr(x));
    cs.forEach((c, i) => pop(c, t, lerp(cx.at[0][0], cx.at[0][1], 0.3 + 0.5 * i / Math.max(1, cs.length)), 0.4));
  };
};

DRAW.blank = () => () => {};
