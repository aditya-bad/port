// The video stage's engine: an SVG stage 1600×780, scenes told in steps,
// captions under it, and a clock a recorder can seek frame by frame.
// Adapted from the explainer-video skill's engine (.claude/skills/
// explainer-video/engine/engine.js): the same pacing rules, without the
// camera, for scenes built from a story (app/video/story.py) at run time.
//
// Pacing. Each step's animation (its `span`, seconds as drawn) is
// stretched to the voice: the step lasts as long as its voice plus a
// breath, or its animation at 0.6× if that's longer; the animation runs no
// slower than 0.35× and, past that, finishes and holds. What runs on `rt`
// (real seconds since the scene began: pulses, the live dot) keeps moving
// meanwhile, so a scene never looks paused.
//
// Query: ?story=<url> (or window.__STORY), &narr=<url> (or window.__NARR) (the voice's timings and
// clips; without it each step is timed from its words), &record=1 (no
// controls; frames drawn only by __seek), &scene=N (start there).
const NS = 'http://www.w3.org/2000/svg';
const W = 1600, H = 780;
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const k = (t, a, b) => (b <= a ? (t >= a ? 1 : 0) : clamp((t - a) / (b - a)));
const ease = x => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);
const ke = (t, a, b) => ease(k(t, a, b));
const lerp = (a, b, f) => a + (b - a) * f;
let root = null;

function E(tag, a = {}, p = root) {
  const e = document.createElementNS(NS, tag);
  for (const n in a) if (a[n] != null) e.setAttribute(n, a[n]);
  p.appendChild(e);
  return e;
}
function T(p, x, y, s, cls = 'lb', anchor = 'middle') {
  const e = E('text', { x, y, class: cls, 'text-anchor': anchor }, p);
  e.textContent = s;
  return e;
}
function G(p = root, cls = '') { return E('g', cls ? { class: cls } : {}, p); }
function op(el, v) { el.setAttribute('opacity', clamp(v).toFixed(3)); }
function tr(el, x, y, s = 1) { el.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})${s !== 1 ? ` scale(${s.toFixed(3)})` : ''}`); }
function txt(el, s) { if (el.textContent !== s) el.textContent = s; }
function fmt(n) { return Math.round(n).toLocaleString('en-IN'); }
function fade(el, t, a, d = 0.45, out = null) { let o = k(t, a, a + d); if (out != null) o = Math.min(o, 1 - k(t, out, out + d)); op(el, o); }
let jolt = [0, 0];
function quake(t, a, d = 0.4, amp = 8) {
  if (t < a || t > a + d) return;
  const f = 1 - k(t, a, a + d);
  jolt = [jolt[0] + Math.sin((t - a) * 95) * amp * f, jolt[1] + Math.cos((t - a) * 71) * amp * 0.6 * f];
}
// lines of at most n characters, broken at spaces
function wrap(s, n) {
  const out = [];
  let cur = '';
  for (const w of String(s || '').split(/\s+/)) {
    if (cur && (cur + ' ' + w).length > n) { out.push(cur); cur = w; } else cur = cur ? cur + ' ' + w : w;
  }
  if (cur) out.push(cur);
  return out;
}
function para(p, x, y, s, n, lh, cls, anchor = 'start') {  // a wrapped paragraph
  const g = G(p);
  wrap(s, n).forEach((l, i) => T(g, x, y + i * lh, l, cls, anchor));
  return g;
}

// ── the clock ───────────────────────────────────────────────────────────
const SCENES = [];
const SLOW = 0.6, MIN_RATE = 0.35, PAD = 0.6, TAIL = 0.8, WPS = 2.5;
const params = new URLSearchParams(location.search);
const RECORD = params.has('record');
let NARR = null, STORY = null;

const sentences = s => s.split(/(?<=[.?!])\s+(?=[A-Z0-9₹“"(])/).filter(Boolean);
function estimate(text) {
  const out = [];
  let t = 0;
  for (const s of sentences(text)) { out.push({ t, text: s }); t += s.split(/\s+/).length / WPS + 0.3; }
  return { dur: Math.max(0.5, t - 0.3), caps: out };
}
function plan(i) {
  const s = SCENES[i];
  const voice = (NARR && NARR[i]) || [];
  let T0 = 0, at = 0;
  const steps = s.steps.map((x, j) => {
    const v = voice[j] || { ...estimate(x.text), src: null };
    const span = x.span || 2;
    const D = Math.max(span / SLOW, v.dur + PAD);
    const r = { at, span, D, rate: Math.max(span / D, MIN_RATE), T0, dur: v.dur, caps: v.caps, src: v.src, file: v.file, text: x.text, cue: x.cue };
    T0 += D; at += span;
    return r;
  });
  return { steps, total: T0 + TAIL, dur: at };
}
function clock(pl, tau) {
  const st = pl.steps;
  for (let j = 0; j < st.length; j++) {
    const x = st[j];
    if (tau < x.T0 + x.D) { const into = Math.max(0, tau - x.T0); return [x.at + Math.min(into * x.rate, x.span), j, into]; }
  }
  const last = st.length - 1;
  return [pl.dur, last, tau - st[last].T0];
}

// ── the player ──────────────────────────────────────────────────────────
let cur = 0, pl = null, start = 0, paused = false, pausedAt = 0, update = null, step = -1, capShown = null, sound = false;
const aud = new Audio();
const $ = id => document.getElementById(id);

function load(i) {
  cur = (i + SCENES.length) % SCENES.length;
  const layer = $('layer'), hud = $('hud');
  layer.innerHTML = ''; hud.innerHTML = '';
  root = layer;
  const s = SCENES[cur];
  pl = plan(cur);
  T(hud, 56, 62, s.title, 'ttl', 'start');
  T(hud, 56, 90, STORY.title, 'sub', 'start');
  T(hud, 1544, 62, `${cur + 1} / ${SCENES.length}`, 'idx', 'end');
  E('rect', { x: 0, y: H - 5, width: W, height: 5, class: 'prog-bg' }, hud);
  const prog = E('rect', { x: 0, y: H - 5, width: 0, height: 5, class: 'prog' }, hud);
  // each step's window of scene time: cue(j) is 0→1 while step j's animation plays
  const at = pl.steps.map(x => [x.at, x.at + x.span]);
  const cue = (t, j, a = 0, b = 1) => (j < 0 || j >= at.length ? 0 : ke(t, lerp(at[j][0], at[j][1], a), lerp(at[j][0], at[j][1], b)));
  const u = s.build(G(layer), s.sc, { cue, at, steps: s.steps, find: c => s.steps.findIndex(x => x.cue === c), all: c => s.steps.map((x, j) => (x.cue === c ? j : -1)).filter(j => j >= 0) });
  update = tau => {
    const [t, j] = clock(pl, tau);
    jolt = [0, 0];
    u(t, tau, j);
    layer.setAttribute('transform', `translate(${jolt[0].toFixed(2)},${jolt[1].toFixed(2)})`);
    prog.setAttribute('width', (W * clamp(tau / pl.total)).toFixed(1));
    narrate(j, tau - pl.steps[j].T0);
  };
  aud.pause(); step = -1; capShown = null;
  start = performance.now(); pausedAt = 0;
  document.querySelectorAll('.dots button').forEach((b, j) => b.classList.toggle('on', j === cur));
  window.__sceneDone = false;
}
function narrate(j, into) {
  const st = pl.steps[j];
  if (j !== step) {
    step = j;
    aud.pause();
    if (st.src && !RECORD) { aud.src = st.src; if (sound && !paused && into < st.dur) { aud.currentTime = Math.max(0, into); aud.play().catch(() => {}); } }
  }
  let c = null;
  for (const x of st.caps) if (into >= x.t) c = x.text;
  if (c !== capShown) { capShown = c; $('cap').textContent = c || ''; }
}
function frame(now) {
  if (update) {
    const tau = paused ? pausedAt : (now - start) / 1000;
    update(tau);
    if (!paused && tau > pl.total) { if (cur < SCENES.length - 1) load(cur + 1); else { paused = true; pausedAt = pl.total; $('pp').textContent = '▶'; } }
  }
  requestAnimationFrame(frame);
}
function setPaused(p) {
  if (p === paused) return;
  if (paused) { start = performance.now() - pausedAt * 1000; paused = false; if (sound) { const st = pl.steps[step]; if (st && st.src) { aud.currentTime = Math.max(0, pausedAt - st.T0); aud.play().catch(() => {}); } } }
  else { pausedAt = (performance.now() - start) / 1000; paused = true; aud.pause(); }
  $('pp').textContent = paused ? '▶' : '❚❚';
}

// for the recorder (app/video/render.py): a scene's plan, and a seek to a moment
window.__plan = n => { const p = plan(n - 1); return { total: p.total, title: SCENES[n - 1].title, steps: p.steps.map(s => ({ T0: s.T0, D: s.D, dur: s.dur, file: s.file, text: s.text })) }; };
window.__seek = (n, tau) => { if (cur !== n - 1 || !update) load(n - 1); update(tau); };

async function boot() {
  const get = async u => { const r = await fetch(u, { credentials: 'same-origin' }); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); };
  // the recorder hands them in directly (app/video/render.py); a browser fetches them
  STORY = window.__STORY || await get(params.get('story'));
  NARR = window.__NARR || (params.get('narr') ? await get(params.get('narr')) : null);
  document.title = STORY.title;
  for (const sc of STORY.scenes) SCENES.push({ title: sc.title, steps: sc.steps, sc, build: (window.DRAW[sc.kind] || window.DRAW.blank) });
  if (RECORD) document.body.classList.add('record');
  const dots = document.querySelector('.dots');
  SCENES.forEach((s, i) => {
    const b = document.createElement('button');
    b.title = `${i + 1}. ${s.title}`; b.setAttribute('aria-label', b.title);
    b.onclick = () => load(i);
    dots.appendChild(b);
  });
  $('prev').onclick = () => load(cur - 1);
  $('next').onclick = () => load(cur + 1);
  $('pp').onclick = () => setPaused(!paused);
  $('snd').onclick = () => { sound = !sound; $('snd').textContent = sound ? '🔊' : '🔇'; if (!sound) aud.pause(); };
  window.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight') load(cur + 1);
    else if (e.key === 'ArrowLeft') load(cur - 1);
    else if (e.key === ' ') { e.preventDefault(); setPaused(!paused); }
  });
  load(params.has('scene') ? +params.get('scene') - 1 : 0);
  if (!RECORD) requestAnimationFrame(frame);
  return SCENES.length;
}
// after every script has loaded (scenes.js defines the drawers)
window.__ready = new Promise(r => window.addEventListener('DOMContentLoaded', r)).then(boot).catch(e => { document.body.dataset.error = e.message; $('cap').textContent = `The video couldn't load: ${e.message}`; throw e; });
