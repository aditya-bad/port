// One tooltip for the whole app — the spec's "chart detail card".
//   data-tip="Title\nLabel  value\n..."  on any element
//   data-scrub="key" on an SVG/line chart, with registerScrub(key, fn)
//     where fn(fraction 0–1) → { t, ys: [0–1...], cs: [css colour...] }
//     or, for charts stacked in panels that scrub together (several
//     elements sharing one key): { t, span: element the guide runs
//     through, pts: [{ el, y: 0–1 within el, c }] }
// Desktop follows the pointer; on touch it sits above the finger and
// line charts scrub on sideways drag.

import { esc } from '../core/dom.js';

const scrubs = new Map();
export function registerScrub(key, fn) { scrubs.set(key, fn); return () => scrubs.delete(key); }

let tip, guide, dots = [], touching = false;

function render(text) {
  return String(text).split('\n').map((l, i) => {
    if (i === 0) return `<div class="h">${esc(l)}</div>`;
    const p = l.split('  ');
    return p.length > 1
      ? `<div class="kv"><span>${esc(p[0])}</span><span>${esc(p.slice(1).join(' ').trim())}</span></div>`
      : `<div style="opacity:.8">${esc(l)}</div>`;
  }).join('');
}
function hide() {
  if (!tip) return;
  tip.style.display = 'none'; guide.style.display = 'none';
  dots.forEach(d => { d.style.display = 'none'; });
}
function show(e, el) {
  const rect = el.getBoundingClientRect();
  let text, ys = [], cs = [], gx = null, guideBox = null, pts = null;
  if (el.dataset.scrub) {
    const fn = scrubs.get(el.dataset.scrub);
    if (!fn) return hide();
    const f = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const r = fn(f);
    if (!r) return hide();
    text = r.t; ys = r.ys || []; cs = r.cs || [];
    gx = rect.left + f * rect.width;
    if (r.span) {
      // one guide through every linked panel, a dot on each panel's line
      guideBox = r.span.getBoundingClientRect();
      pts = (r.pts || []).map(p => { const b = p.el.getBoundingClientRect(); return p.y == null ? null : { top: b.top + p.y * b.height, c: p.c }; });
    }
  } else {
    text = el.getAttribute('data-tip');
    if (!text) return hide();
  }
  tip.innerHTML = render(text);
  tip.style.display = 'block';
  const tw = tip.offsetWidth, th = tip.offsetHeight, ax = gx ?? e.clientX, touch = e.pointerType === 'touch';
  let x = ax + 14, y = touch ? e.clientY - th - 28 : e.clientY + 16;
  if (x + tw > innerWidth - 8) x = ax - tw - 14;
  if (x < 8) x = 8;
  if (y + th > innerHeight - 8) y = e.clientY - th - 14;
  if (y < 8) y = e.clientY + 28;
  tip.style.left = `${x}px`; tip.style.top = `${y}px`;
  if (gx !== null && pts) {
    Object.assign(guide.style, { display: 'block', left: `${gx}px`, top: `${guideBox.top}px`, height: `${guideBox.height}px` });
    while (dots.length < pts.length) {
      const d = document.createElement('div');
      d.className = 'tip-dot';
      document.body.appendChild(d);
      dots.push(d);
    }
    dots.forEach((d, i) => {
      const p = pts[i];
      if (!p) { d.style.display = 'none'; return; }
      Object.assign(d.style, { display: 'block', left: `${gx}px`, top: `${p.top}px`, borderColor: p.c || 'var(--ink)' });
    });
  } else if (gx !== null) {
    Object.assign(guide.style, { display: 'block', left: `${gx}px`, top: `${rect.top}px`, height: `${rect.height}px` });
    while (dots.length < ys.length) {
      const d = document.createElement('div');
      d.className = 'tip-dot';
      document.body.appendChild(d);
      dots.push(d);
    }
    dots.forEach((d, i) => {
      if (ys[i] == null) { d.style.display = 'none'; return; }
      Object.assign(d.style, { display: 'block', left: `${gx}px`, top: `${rect.top + ys[i] * rect.height}px`, borderColor: cs[i] || 'var(--ink)' });
    });
  } else {
    guide.style.display = 'none';
    dots.forEach(d => { d.style.display = 'none'; });
  }
}
const find = e => (e.target && e.target.closest ? e.target.closest('[data-tip],[data-scrub]') : null);

export function installTooltip() {
  if (tip) return;
  tip = document.createElement('div'); tip.className = 'tip'; tip.setAttribute('role', 'tooltip');
  guide = document.createElement('div'); guide.className = 'tip-guide';
  document.body.append(tip, guide);
  hide();
  document.addEventListener('pointermove', e => {
    if (e.pointerType === 'touch' && !touching) return;
    const el = find(e);
    if (el) show(e, el); else if (e.pointerType !== 'touch') hide();
  });
  document.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'touch') return;
    touching = true;
    const el = find(e);
    if (el) show(e, el); else hide();
  });
  document.addEventListener('pointerup', () => { touching = false; });
  document.addEventListener('pointerout', e => { if (!e.relatedTarget) hide(); });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('hashchange', hide);
}
