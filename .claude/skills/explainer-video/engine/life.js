// Life: small effects that make a scene feel alive — things pop in with a
// little overshoot, a stamp slams down, confetti bursts, a crash puffs
// smoke, an idle process snores, a zombie floats. Use one or two where they
// help the point land; never so many that they hide it.
//
// Every effect is driven by the times you pass (scene time t, or rt for
// the ones that should keep moving while the narrator talks), never the
// wall clock, so a recorded video comes out the same every time. Each
// factory draws into group p and returns a group with a .go(…) to animate.

function rng(seed = 1) {                                  // the same "random" every run
  let s = seed | 0;
  return () => { s = s + 0x6D2B79F5 | 0; let x = Math.imul(s ^ s >>> 15, 1 | s); x = x + Math.imul(x ^ x >>> 7, 61 | x) ^ x; return ((x ^ x >>> 14) >>> 0) / 4294967296; };
}
const backOut = x => { const c = 1.70158; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
const elastic = x => (x <= 0 ? 0 : x >= 1 ? 1 : Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * (2 * Math.PI) / 3) + 1);
const bounceOut = x => { const n = 7.5625, d = 2.75; if (x < 1 / d) return n * x * x; if (x < 2 / d) return n * (x -= 1.5 / d) * x + 0.75; if (x < 2.5 / d) return n * (x -= 2.25 / d) * x + 0.9375; return n * (x -= 2.625 / d) * x + 0.984375; };

// Scale / nudge / turn an element about its own centre (CSS transform, so it
// doesn't fight a packet's position, which lives in its transform attribute).
function look(el, { s = 1, dx = 0, dy = 0, rot = 0 } = {}) {
  el.style.transformBox = 'fill-box';
  el.style.transformOrigin = 'center';
  el.style.transform = `translate(${dx.toFixed(2)}px,${dy.toFixed(2)}px) scale(${Math.max(0.001, s).toFixed(4)})${rot ? ` rotate(${rot.toFixed(2)}deg)` : ''}`;
}

// Appear with a little overshoot (instead of fade); out: when to go.
function pop(el, t, a, d = 0.5, out = null) {
  const f = k(t, a, a + d);
  let s = f <= 0 ? 0 : backOut(f), o = Math.min(1, f * 3);
  if (out != null) { const g = k(t, out, out + 0.3); s *= 1 - 0.4 * g; o *= 1 - g; }
  op(el, o);
  look(el, { s: f <= 0 ? 0.001 : s });
}
// A packet lands: squashes flat, then springs back. Use on packet(...)._in.
function squash(inner, t, a, d = 0.45) {
  const f = k(t, a, a + d);
  if (f <= 0 || f >= 1) { look(inner); return; }
  const w = Math.sin(f * Math.PI) * (1 - f);
  inner.style.transformBox = 'fill-box'; inner.style.transformOrigin = 'center';
  inner.style.transform = `scale(${(1 + 0.35 * w).toFixed(3)},${(1 - 0.3 * w).toFixed(3)})`;
}
// A gentle bob for something waiting (pass rt).
function bob(el, rt, amp = 3, speed = 2.2, phase = 0) { look(el, { dy: Math.sin(rt * speed + phase) * amp }); }
// A nervous wobble (an overloaded box).
function wobble(el, rt, deg = 2.5) { look(el, { rot: Math.sin(rt * 17) * deg }); }

// A number that rolls from one value to another.
function countUp(el, t, a, b, from, to, f = v => fmt(Math.round(v))) { txt(el, f(lerp(from, to, ease(k(t, a, b))))); }
// Text typed out, a caret blinking (pass rt for the caret).
function typeOn(el, t, a, b, s, rt = null) {
  const n = Math.round(s.length * k(t, a, b));
  txt(el, s.slice(0, n) + (rt != null && n < s.length && Math.sin(rt * 12) > 0 ? '▌' : ''));
}

// A rubber stamp: REFUSED, APPROVED, DUPLICATE… slams down, the picture shakes.
function stamp(p, x, y, text, color = 'r', o = {}) {
  const g = G(p, `lf-stamp ${color}`);
  const w = text.length * 24 + 40, h = 58;
  E('rect', { x: x - w / 2, y: y - h / 2, width: w, height: h, rx: 8 }, g);
  T(g, x, y + 12, text, 'lf-st');
  op(g, 0);
  g.go = (t, a) => {
    const f = k(t, a, a + 0.2);
    if (t < a) { op(g, 0); return; }
    op(g, Math.min(1, f * 4));
    const settle = k(t, a + 0.2, a + 0.6);
    const s = f < 1 ? lerp(2.6, 1, f * f) : 1 + 0.07 * Math.sin(settle * 18) * (1 - settle);
    look(g, { s, rot: o.rot ?? -9 });
    quake(t, a + 0.2, 0.3, o.shake ?? 7);
  };
  return g;
}

// Confetti from a point (a success, a fill, a milestone).
function burst(p, x, y, o = {}) {
  const g = G(p), n = o.n || 28, R = rng(o.seed || 7), colors = o.colors || ['b', 'g', 'y', 'pu', 'r', 'wt'];
  const bits = Array.from({ length: n }, () => {
    const c = colors[Math.floor(R() * colors.length)];
    const e = R() < 0.5 ? E('rect', { x: -5, y: -3, width: 10, height: 6 }, g) : E('circle', { r: 4 }, g);
    e.setAttribute('class', `lf-bit f-${c}`);
    op(e, 0);
    const ang = -Math.PI / 2 + (R() - 0.5) * Math.PI * (o.spread || 1.4), v = 380 + R() * 360;
    return { e, vx: Math.cos(ang) * v, vy: Math.sin(ang) * v, rot: (R() - 0.5) * 900 };
  });
  g.go = (t, a, life = 1.6) => bits.forEach(bt => {
    const age = t - a;
    if (age < 0 || age > life) { op(bt.e, 0); return; }
    const px = x + bt.vx * age * 0.6, py = y + bt.vy * age * 0.6 + 520 * age * age;
    bt.e.setAttribute('transform', `translate(${px.toFixed(1)},${py.toFixed(1)}) rotate(${(bt.rot * age).toFixed(1)})`);
    op(bt.e, 1 - Math.pow(age / life, 2));
  });
  return g;
}

// A puff of smoke (a crash, something stopping).
function puff(p, x, y, o = {}) {
  const g = G(p, 'lf-puff'), R = rng(o.seed || 3);
  const cs = Array.from({ length: 7 }, (_, i) => {
    const a = (i / 7) * Math.PI * 2 + R() * 0.6;
    return { c: E('circle', { cx: 0, cy: 0, r: 8 }, g), dx: Math.cos(a) * (30 + R() * 25), dy: Math.sin(a) * (20 + R() * 20) - 25, r: 14 + R() * 12 };
  });
  op(g, 0);
  g.go = (t, a, life = 1.1) => {
    const age = t - a;
    if (age < 0 || age > life) { op(g, 0); return; }
    op(g, 1);
    const f = ease(age / life);
    cs.forEach(c => { c.c.setAttribute('cx', (x + c.dx * f).toFixed(1)); c.c.setAttribute('cy', (y + c.dy * f).toFixed(1)); c.c.setAttribute('r', (c.r * (0.4 + f)).toFixed(1)); op(c.c, 0.75 * (1 - f)); });
  };
  return g;
}

// A speech bubble (say: 'busy…', 'hello!', 'is this new?'); think: dashed.
function bubble(p, x, y, text, o = {}) {
  const g = G(p, `lf-bubble${o.think ? ' think' : ''}`);
  const w = Math.max(70, text.length * 10 + 28), h = 40, below = o.below;
  const top = below ? y + 18 : y - 18 - h;
  E('rect', { x: x - w / 2, y: top, width: w, height: h, rx: 14 }, g);
  E('path', { d: below ? `M${x - 9} ${top + 1}L${x} ${y + 4}L${x + 9} ${top + 1}` : `M${x - 9} ${top + h - 1}L${x} ${y - 4}L${x + 9} ${top + h - 1}` }, g);
  T(g, x, top + 26, text, 'lf-bt');
  op(g, 0);
  g.go = (t, a, out = null, rt = 0) => {
    const f = k(t, a, a + 0.4);
    let o2 = f > 0 ? 1 : 0, s = f > 0 ? backOut(f) : 0.001;
    if (out != null && t > out) { const q = k(t, out, out + 0.25); o2 = 1 - q; s *= 1 - 0.5 * q; }
    op(g, o2);
    look(g, { s, dy: Math.sin(rt * 2.4) * 2.5 });
  };
  return g;
}

// Zzz rising from something idle or asleep (pass rt; on: true while it sleeps).
function zzz(p, x, y) {
  const g = G(p, 'lf-zzz');
  const zs = [0, 1, 2].map(i => T(g, x, y, 'z', 'lf-z', 'start'));
  g.go = (rt, on) => zs.forEach((z, i) => {
    if (!on) { op(z, 0); return; }
    const f = ((rt * 0.6) + i / 3) % 1;
    z.setAttribute('x', (x + f * 26 + Math.sin(f * 6) * 4).toFixed(1));
    z.setAttribute('y', (y - f * 46).toFixed(1));
    z.style.fontSize = `${14 + f * 14}px`;
    op(z, Math.sin(f * Math.PI));
  });
  return g;
}

// Sweat drops off something working too hard (pass rt; on while it strains).
function sweat(p, x, y) {
  const g = G(p);
  const ds = [0, 1].map(i => E('path', { d: 'M0 -9C4 -3 6 1 6 4A6 6 0 0 1 -6 4C-6 1 -4 -3 0 -9Z', class: 'lf-drop' }, g));
  g.go = (rt, on) => ds.forEach((d, i) => {
    if (!on) { op(d, 0); return; }
    const f = ((rt * 0.9) + i * 0.5) % 1;
    tr(d, x + (i ? 14 : -10) + f * (i ? 10 : -8), y + f * 34);
    op(d, Math.sin(f * Math.PI));
  });
  return g;
}

// Twinkling stars around a success.
function sparkle(p, x, y, o = {}) {
  const g = G(p), R = rng(o.seed || 11), r = o.r || 60;
  const st = Array.from({ length: o.n || 5 }, (_, i) => {
    const a = (i / (o.n || 5)) * Math.PI * 2 + R(), d = r * (0.6 + R() * 0.5);
    return { e: E('path', { d: 'M0 -10L2.5 -2.5L10 0L2.5 2.5L0 10L-2.5 2.5L-10 0L-2.5 -2.5Z', class: 'lf-star' }, g), x: x + Math.cos(a) * d, y: y + Math.sin(a) * d, ph: R() };
  });
  g.go = (t, a, life = 1.4) => st.forEach(s => {
    const age = t - a - s.ph * 0.3;
    if (age < 0 || age > life) { op(s.e, 0); return; }
    const f = Math.sin((age / life) * Math.PI);
    s.e.setAttribute('transform', `translate(${s.x.toFixed(1)},${s.y.toFixed(1)}) scale(${(0.4 + f).toFixed(3)}) rotate(${(age * 120).toFixed(1)})`);
    op(s.e, f);
  });
  return g;
}

// A ghost (a zombie process, something that should be gone): floats.
function ghost(p, x, y, s = 1) {
  const g = G(p, 'lf-ghost');
  const inner = G(g);
  E('path', { class: 'body', d: 'M-26 30V-6A26 26 0 0 1 26 -6V30L17 22L8 30L0 22L-8 30L-17 22Z' }, inner);
  E('circle', { class: 'eye', cx: -9, cy: -6, r: 4 }, inner);
  E('circle', { class: 'eye', cx: 9, cy: -6, r: 4 }, inner);
  tr(g, x, y, s);
  op(g, 0);
  g.go = rt => look(inner, { dy: Math.sin(rt * 2) * 6, rot: Math.sin(rt * 1.3) * 4 });
  return g;
}

// A little rocket (a deploy, a launch) that flies along a path, flame flickering.
function rocket(p) {
  const g = G(p, 'lf-rocket');
  const inner = G(g);
  E('path', { class: 'flame', d: 'M-30 -6L-48 0L-30 6Z' }, inner);
  E('path', { class: 'fin', d: 'M-22 -10L-30 -20L-12 -10ZM-22 10L-30 20L-12 10Z' }, inner);
  E('path', { class: 'hull', d: 'M-26 -10H12Q30 -10 38 0Q30 10 12 10H-26Z' }, inner);
  E('circle', { cx: 8, cy: 0, r: 4, class: 'f-b' }, inner);
  op(g, 0);
  g.go = (path, t, a, b, rt = 0) => {
    if (t < a || t > b + 0.2) { op(g, 0); return; }
    op(g, 1);
    const f = ease(k(t, a, b)), L = path.getTotalLength();
    const pt = path.getPointAtLength(f * L), pn = path.getPointAtLength(Math.min(L, f * L + 2));
    const ang = Math.atan2(pn.y - pt.y, pn.x - pt.x) * 180 / Math.PI;
    g.setAttribute('transform', `translate(${pt.x.toFixed(1)},${pt.y.toFixed(1)}) rotate(${ang.toFixed(1)})`);
    inner.firstChild.setAttribute('transform', `scale(${(0.8 + 0.4 * Math.abs(Math.sin(rt * 30))).toFixed(2)},1)`);
  };
  return g;
}
