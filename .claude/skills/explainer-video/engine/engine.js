// The explainer engine: a 1600×900 SVG stage, scenes told in narrated
// steps, a camera that keeps moving, a spotlight, captions, a start screen
// for sound, and a clock a recorder can seek frame by frame.
//
// A scene:  scene({ title, dur, steps: [[at, 'What the narrator says.', { focus, spot }], …] },
//                 p => { …build once into group p…; return (t, rt) => { …draw time t… } })
//   t   scene time (seconds of the animation as drawn), slowed to fit the voice
//   rt  real seconds since the scene began: for things that should keep moving
//       while the narrator talks (streams, bobbing, pulses), never paused
//   at  the scene time where that step's animation begins
//   focus [x, y, w, h] | 'all'  the camera eases to frame it (kept until changed)
//   spot  [x, y, w, h]          dim everything else while this step is spoken
const NS = 'http://www.w3.org/2000/svg';
const svg = document.getElementById('stage');
const hud = document.getElementById('hud');
const cam = document.getElementById('cam');
const root = document.getElementById('layer');
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const k = (t, a, b) => clamp((t - a) / (b - a));
const ease = x => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);
const ke = (t, a, b) => ease(k(t, a, b));
const lerp = (a, b, f) => a + (b - a) * f;

function E(tag, a = {}, p = root) {
  const e = document.createElementNS(NS, tag);
  for (const n in a) e.setAttribute(n, a[n]);
  p.appendChild(e);
  return e;
}
function T(p, x, y, s, cls = 'lb', anchor = 'middle') {
  const e = E('text', { x, y, class: cls, 'text-anchor': anchor }, p);
  e.textContent = s;
  return e;
}
function G(p = root, cls = '') { return E('g', cls ? { class: cls } : {}, p); }
function op(el, v) { el.setAttribute('opacity', v.toFixed(3)); }
function tr(el, x, y, s = 1) { el.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})${s !== 1 ? ` scale(${s.toFixed(3)})` : ''}`); }

// A box: label centred (or at the top), optional sub line and corner tag.
// cls: frame (dashed group), key (the important one), band, chip, out (outside
// world), store, file (mono label), proc, art, bad, btn.
function box(p, x, y, w, h, label, o = {}) {
  const g = G(p, 'bx ' + (o.cls || ''));
  E('rect', { x, y, width: w, height: h, rx: o.rx || 0 }, g);
  const ly = o.top ? y + 32 : (o.sub ? y + h / 2 - 3 : y + h / 2 + 8);
  if (label) T(g, x + w / 2, ly, label, o.lcls || 'bl');
  if (o.sub) T(g, x + w / 2, ly + 24, o.sub, 'bs');
  if (o.tag) T(g, x + w - 10, y + 20, o.tag, 'bt', 'end');
  Object.assign(g, { X: x, Y: y, W: w, H: h, cx: x + w / 2, cy: y + h / 2, R: x + w, B: y + h });
  op(g, 0);
  return g;
}
// A line with an arrowhead in its colour (ink, b, g, pu, wt, r).
function line(p, d, color = 'ink', o = {}) {
  const a = { d, class: `ln c-${color}${o.dash ? ' dash' : ''}` };
  if (o.arrow !== false) a['marker-end'] = `url(#ah-${color})`;
  const e = E('path', a, p);
  op(e, 0);
  return e;
}
function reveal(path, t, a, b) {             // draw a line from start to end
  const L = path.getTotalLength();
  const f = k(t, a, b);
  path.style.strokeDasharray = path.classList.contains('dash') ? '' : `${L} ${L}`;
  if (!path.classList.contains('dash')) path.style.strokeDashoffset = `${L * (1 - f)}`;
  op(path, f > 0 ? 1 : 0);
}
function dot(p, color, r = 7) { const e = E('circle', { r, cx: 0, cy: 0, class: 'dt f-' + color }, p); op(e, 0); return e; }
// A labelled packet that travels; its look lives in g._in, so pop/squash
// can scale it while the outer group carries its position.
function packet(p, label, color, o = {}) {
  const g = G(p, 'pk');
  const inner = G(g);
  const w = Math.max(o.w || 0, 44, label.length * 10.8 + 24);
  E('rect', { x: -w / 2, y: -16, width: w, height: 32, rx: 4, class: 'f-' + color }, inner);
  T(inner, 0, 5, label, 'pt');
  g._in = inner;
  op(g, 0);
  return g;
}
function placeOn(el, path, f) {
  const L = path.getTotalLength();
  const pt = path.getPointAtLength(clamp(f) * L);
  tr(el, pt.x, pt.y);
}
function travel(el, path, t, a, b, keep = false) {   // move along a path between a and b
  if (t < a || (t > b && !keep)) { op(el, 0); return false; }
  op(el, 1);
  placeOn(el, path, ease(k(t, a, b)));
  return true;
}
function chain(el, paths, t, t0, seg) {               // along several paths, one after another
  for (let i = 0; i < paths.length; i++) {
    const a = t0 + i * seg;
    if (t >= a && t <= a + seg) { op(el, 1); placeOn(el, paths[i], ease(k(t, a, a + seg))); return i; }
  }
  op(el, 0);
  return -1;
}
// n dots looping along a path: call with rt to keep flowing while the voice talks.
function stream(p, path, color, n, period, r = 6) {
  const ds = Array.from({ length: n }, () => dot(p, color, r));
  return (t, t0, t1 = 1e9) => ds.forEach((d, i) => {
    if (t < t0 || t > t1) { op(d, 0); return; }
    const f = (((t - t0) / period) + i / n) % 1;
    op(d, Math.min(1, (t - t0) * 2) * (f < 0.06 ? f / 0.06 : f > 0.94 ? (1 - f) / 0.06 : 1));
    placeOn(d, path, f);
  });
}
function fade(el, t, a, d = 0.45, out = null) {
  let o = k(t, a, a + d);
  if (out != null) o = Math.min(o, 1 - k(t, out, out + d));
  op(el, o);
}
function flash(el, t, a, d = 0.6) { el.classList.toggle('hot', t >= a && t <= a + d); }   // brief highlight
function txt(el, s) { if (el.textContent !== s) el.textContent = s; }
function check(p, x, y, ok = true) {
  const g = G(p, ok ? 'ck ok' : 'ck no');
  E('circle', { cx: x, cy: y, r: 13 }, g);
  E('path', { d: ok ? `M${x - 6} ${y}l4 4 8-9` : `M${x - 5} ${y - 5}l10 10M${x + 5} ${y - 5}l-10 10` }, g);
  op(g, 0);
  return g;
}
function boom(p, x, y) {
  const g = G(p, 'boom');
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4;
    E('path', { d: `M${x + Math.cos(a) * 14} ${y + Math.sin(a) * 14}L${x + Math.cos(a) * 34} ${y + Math.sin(a) * 34}` }, g);
  }
  E('path', { d: `M${x - 11} ${y - 11}l22 22M${x + 11} ${y - 11}l-22 22`, class: 'bx-x' }, g);
  op(g, 0);
  return g;
}
function gear(p, x, y, r = 18) {
  const g = G(p, 'gear');
  E('circle', { cx: 0, cy: 0, r }, g);
  E('circle', { cx: 0, cy: 0, r: r * 0.4 }, g);
  tr(g, x, y);
  g._x = x; g._y = y;
  return g;
}
function spin(g, t, speed = 120) { g.setAttribute('transform', `translate(${g._x},${g._y}) rotate(${(t * speed) % 360})`); }
function diamond(p, x, y, s = 10) { const e = E('path', { d: `M${x} ${y - s}l${s} ${s}-${s} ${s}-${s}-${s}z`, class: 'snap' }, p); op(e, 0); return e; }
function fmt(n) { return n.toLocaleString('en-IN'); }

// A shake of the whole picture (a crash, a stamp landing): computed from t,
// so a recorded video shakes the same every time.
let jolt = [0, 0];
function quake(t, a, d = 0.4, amp = 8) {
  if (t < a || t > a + d) return;
  const f = 1 - k(t, a, a + d);
  jolt = [jolt[0] + Math.sin((t - a) * 95) * amp * f, jolt[1] + Math.cos((t - a) * 71) * amp * 0.6 * f];
}

// ── the clock ───────────────────────────────────────────────────────────
// Each step lasts as long as its voice (plus a breath), or its animation
// at 0.6× speed if that's longer. The animation is spread over the whole
// step; only when the voice is much longer than the motion (under 0.35×)
// does it reach its end early, and then the camera drift, the spotlight
// and anything driven by rt keep the picture moving. tools/check.mjs
// reports any half-second where nothing on screen changes.
const SCENES = [];
function scene(meta, build) { SCENES.push({ ...meta, steps: meta.steps || [[0, '']], build }); }
const SLOW = 0.6, MIN_RATE = 0.35, PAD = 0.6, TAIL = 1.0, WPS = 2.5;
const params = new URLSearchParams(location.search);
const RECORD = params.has('record');
const ONLY = params.has('scene') ? +params.get('scene') - 1 : null;
const AT = params.has('t') ? +params.get('t') : null;
const SEEK = params.has('seek');                         // frames drawn only by __seek (video, checks)
const STILL = !RECORD && matchMedia('(prefers-reduced-motion: reduce)').matches;
const DRIFT = params.has('nodrift') ? 0 : 1;            // tools/selftest.sh only: proves check.mjs catches a pause

const sentences = s => s.split(/(?<=[.?!])\s+(?=[A-Z0-9₹“"(])/).filter(Boolean);
function estimate(text) {                                 // before the voice exists: words at ~2.5 a second
  const ss = sentences(text), out = [];
  let t = 0;
  for (const s of ss) { out.push({ t, text: s }); t += s.split(/\s+/).length / WPS + 0.3; }
  return { dur: Math.max(0.5, t - 0.3), caps: out };
}
function plan(i) {
  const s = SCENES[i];
  const voice = (typeof NARR !== 'undefined' && NARR && NARR[i]) || [];
  let T0 = 0, focus = 'all';
  const out = s.steps.map(([at, text, o = {}], j) => {
    const v = voice[j] || { ...estimate(text), src: null };
    const span = (j + 1 < s.steps.length ? s.steps[j + 1][0] : s.dur) - at;
    const D = Math.max(span / SLOW, v.dur + PAD);
    if (o.focus) focus = o.focus;
    const r = { at, text, span, D, rate: Math.max(span / D, MIN_RATE), T0, dur: v.dur, caps: v.caps, src: v.src, file: v.file, focus, spot: o.spot || null };
    T0 += D;
    return r;
  });
  return { steps: out, total: T0 + TAIL, dur: s.dur };
}
// real seconds into the scene → [scene time, step, seconds into the step]
function clock(pl, tau) {
  const st = pl.steps;
  for (let j = 0; j < st.length; j++) {
    const x = st[j];
    if (tau < x.T0 + x.D) { const into = Math.max(0, tau - x.T0); return [x.at + Math.min(into * x.rate, x.span), j, into]; }
  }
  const last = st.length - 1;
  return [pl.dur, last, tau - st[last].T0];
}

// ── the camera and the spotlight ───────────────────────────────────────
const FULL = [0, 0, 1600, 900];
function view(rect) {                                     // a rect → scale and centre that frame it
  const [x, y, w, h] = rect === 'all' || !rect ? FULL : rect;
  const s = clamp(Math.min(1600 / (w + 80), 790 / (h + 80)), 1, 1.8);
  return { s, cx: x + w / 2, cy: y + h / 2 };
}
function camera(pl, j, into) {
  const st = pl.steps[j];
  const to = view(st.focus), from = view(j ? pl.steps[j - 1].focus : st.focus);
  const f = ke(into, 0, 1.2);
  const drift = 1 + DRIFT * (0.03 * ease(clamp(into / st.D)) + 0.006 * Math.max(0, into - st.D));   // always a little closer while it's spoken
  let s = lerp(from.s, to.s, f) * drift, cx = lerp(from.cx, to.cx, f), cy = lerp(from.cy, to.cy, f);
  // keep the frame on the stage: no empty paper past its edges
  const hw = 800 / s, hh = 450 / s;
  cx = clamp(cx, hw, 1600 - hw); cy = clamp(cy, hh, 900 - hh);
  if (backdrop) op(backdrop, clamp((s - 1.04) / 0.12) * 0.92);   // zoomed in: the title keeps its own ground
  cam.setAttribute('transform', `translate(${(800 + jolt[0]).toFixed(2)},${(450 + jolt[1]).toFixed(2)}) scale(${s.toFixed(4)}) translate(${(-cx).toFixed(2)},${(-cy).toFixed(2)})`);
}
let veil = null, backdrop = null;
function spotlight(pl, j, into, rt) {
  const st = pl.steps[j];
  if (!veil) return;
  if (!st.spot) { op(veil, 0); return; }
  const [x, y, w, h] = st.spot, r = 14;
  const o = Math.min(ke(into, 0.2, 0.8), 1 - ke(into, st.D - 0.5, st.D)) * 0.6;
  veil.firstChild.setAttribute('d', `M-400 -400H2000V1300H-400Z M${x + r} ${y}H${x + w - r}A${r} ${r} 0 0 1 ${x + w} ${y + r}V${y + h - r}A${r} ${r} 0 0 1 ${x + w - r} ${y + h}H${x + r}A${r} ${r} 0 0 1 ${x} ${y + h - r}V${y + r}A${r} ${r} 0 0 1 ${x + r} ${y}Z`);
  veil.firstChild.setAttribute('opacity', o.toFixed(3));
  const ring = veil.lastChild;
  ring.setAttribute('x', x - 4); ring.setAttribute('y', y - 4); ring.setAttribute('width', w + 8); ring.setAttribute('height', h + 8);
  ring.setAttribute('opacity', (o / 0.6 * (0.45 + 0.35 * Math.sin(rt * 3.2))).toFixed(3));   // it breathes while it's spoken
  op(veil, 1);
}

// ── the player ──────────────────────────────────────────────────────────
let cur = 0, pl = null, start = 0, paused = false, pausedAt = 0, update = null;
let sound = false, step = -1, capShown = null;
const aud = new Audio();
const capEl = () => document.getElementById('cap');

function load(i) {
  cur = (i + SCENES.length) % SCENES.length;
  root.innerHTML = ''; hud.innerHTML = '';
  const s = SCENES[cur];
  pl = plan(cur);
  backdrop = E('rect', { x: 0, y: 0, width: 1600, height: 100, class: 'hud-ground' }, hud);
  op(backdrop, 0);
  T(hud, 60, 70, s.title, 'ttl', 'start');
  T(hud, 1540, 70, `${cur + 1} / ${SCENES.length}`, 'idx', 'end');
  const prog = E('rect', { x: 0, y: 893, width: 0, height: 7, class: 'prog' }, hud);
  const body = G(root);
  const u = s.build(body);
  veil = G(root);
  E('path', { class: 'spot-veil', 'fill-rule': 'evenodd' }, veil);
  E('rect', { class: 'spot-ring', rx: 16 }, veil);
  op(veil, 0);
  update = tau => {
    const [t, j, into] = clock(pl, tau);
    jolt = [0, 0];
    u(t, tau);
    camera(pl, j, into);
    spotlight(pl, j, into, tau);
    prog.setAttribute('width', (1600 * clamp(tau / pl.total)).toFixed(1));
    narrate(j, into);
  };
  aud.pause(); step = -1; capShown = null;
  start = performance.now();
  pausedAt = 0;
  if (STILL) {                                            // reduced motion: the finished picture, all its words
    paused = true; pausedAt = pl.total;
    capEl().textContent = pl.steps.map(st => st.caps.map(c => c.text).join(' ')).join(' ');
  }
  document.querySelectorAll('.dots button').forEach((b, j) => b.classList.toggle('on', j === cur));
  // the scene in the address, where allowed (a sandboxed srcdoc viewer refuses it)
  if (!RECORD) try { history.replaceState(null, '', `#${cur + 1}`); } catch (e) { /* no address to update */ }
  window.__sceneDone = false;
}
function narrate(j, into) {                               // the step's caption, and its clip when sound is on
  if (STILL) return;
  const st = pl.steps[j];
  if (j !== step) {
    step = j;
    aud.pause();
    if (st.src) { aud.src = st.src; if (sound && !paused && into < st.dur) { aud.currentTime = Math.max(0, into); aud.play().catch(() => {}); } }
  }
  let c = null;
  for (const x of st.caps) if (into >= x.t) c = x.text;
  if (c !== capShown) { capShown = c; capEl().textContent = c || ''; }
}
function frame(now) {
  if (update) {
    const tau = AT != null ? AT : paused ? pausedAt : (now - start) / 1000;
    update(tau);
    if (AT != null) { window.__sceneDone = true; requestAnimationFrame(frame); return; }
    if (!paused && tau > pl.total) {
      if (RECORD) { window.__sceneDone = true; if (ONLY == null && cur < SCENES.length - 1) load(cur + 1); else update = null; }
      else load(cur + 1);
    }
  }
  requestAnimationFrame(frame);
}
function resumeAudio() {
  if (!sound || paused || step < 0) return;
  const st = pl.steps[step], into = (performance.now() - start) / 1000 - st.T0;
  if (st.src && into < st.dur) { aud.currentTime = Math.max(0, into); aud.play().catch(() => {}); }
}
function setPaused(p) {
  if (p === paused) return;
  if (paused) { start = performance.now() - pausedAt * 1000; paused = false; resumeAudio(); }
  else { pausedAt = (performance.now() - start) / 1000; paused = true; aud.pause(); }
  document.getElementById('pp').textContent = paused ? '▶' : '❚❚';
}
function setSound(on) {
  sound = on;
  const b = document.getElementById('snd');
  b.textContent = on ? '🔊' : '🔇';
  b.setAttribute('aria-label', on ? 'Mute narration' : 'Turn narration on');
  if (on) resumeAudio(); else aud.pause();
}
function begin(withSound) {                               // the start screen's buttons
  document.getElementById('start').hidden = true;
  setSound(withSound);
  setPaused(false);
}
// for frame-by-frame video and checks: a scene's plan, and a seek to a moment
window.__plan = n => { const p = plan(n - 1); return { total: p.total, steps: p.steps.map(s => ({ T0: s.T0, D: s.D, dur: s.dur, span: s.span, rate: s.rate, file: s.file, text: s.text })) }; };
window.__seek = (n, tau) => { if (cur !== n - 1 || !update) load(n - 1); update(tau); };

window.addEventListener('DOMContentLoaded', () => {
  const dots = document.querySelector('.dots');
  SCENES.forEach((s, i) => {
    const b = document.createElement('button');
    b.title = `${i + 1}. ${s.title}`;
    b.setAttribute('aria-label', b.title);
    b.onclick = () => load(i);
    dots.appendChild(b);
  });
  const startEl = document.getElementById('start');
  document.getElementById('mins').textContent = Math.max(1, Math.round(SCENES.reduce((a, s, i) => a + plan(i).total, 0) / 60));
  document.getElementById('prev').onclick = () => load(cur - 1);
  document.getElementById('next').onclick = () => load(cur + 1);
  document.getElementById('pp').onclick = () => setPaused(!paused);
  document.getElementById('snd').onclick = () => setSound(!sound);
  document.getElementById('go').onclick = () => begin(true);
  document.getElementById('go-muted').onclick = () => begin(false);
  window.addEventListener('keydown', e => {
    if (!startEl.hidden) {
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); begin(true); return; }
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') begin(false);
    }
    if (e.key === 'ArrowRight') load(cur + 1);
    else if (e.key === 'ArrowLeft') load(cur - 1);
    else if (e.key === ' ') { e.preventDefault(); setPaused(!paused); }
  });
  if (RECORD) document.body.classList.add('record');
  const h = parseInt(location.hash.slice(1), 10);
  load(ONLY != null ? ONLY : (h >= 1 && h <= SCENES.length ? h - 1 : 0));
  if (!RECORD && !STILL) { startEl.hidden = false; paused = true; pausedAt = 0; document.getElementById('pp').textContent = '▶'; }
  if (!SEEK) requestAnimationFrame(frame);
});
