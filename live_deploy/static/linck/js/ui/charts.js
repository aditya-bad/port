// SVG chart builders. All drawn in a 1000-wide viewBox stretched to the
// container (preserveAspectRatio="none" + non-scaling strokes), so they
// fit any width. Colours are tokens, so every theme just works.

import { html, raw } from '../core/dom.js';

// values → "M x y L x y …" across width w, height h, between lo and hi.
export function path(values, h, lo, hi, w = 1000, pad = 2) {
  const n = values.length;
  if (!n) return '';
  const span = (hi - lo) || 1;
  return values.map((v, i) => `${i ? 'L' : 'M'}${(n === 1 ? w / 2 : i / (n - 1) * w).toFixed(1)} ${(h - pad - (v - lo) / span * (h - pad * 2)).toFixed(1)}`).join(' ');
}
// Points with their own x (0–1).
export function pathXY(points, h, lo, hi, w = 1000, pad = 2) {
  const span = (hi - lo) || 1;
  return points.map(([x, v], i) => `${i ? 'L' : 'M'}${(x * w).toFixed(1)} ${(h - pad - (v - lo) / span * (h - pad * 2)).toFixed(1)}`).join(' ');
}
export const yOf = (v, h, lo, hi, pad = 2) => h - pad - (v - lo) / ((hi - lo) || 1) * (h - pad * 2);

// A line chart with optional area fill and zero line. A series' fill runs
// from the line to fillTo; with fillNeg, the part below fillTo (a loss,
// when fillTo is 0) is filled with fillNeg instead.
let clipN = 0;
export function line({ series, h = 200, key = '', lo, hi, zero = false, rule = true, cls = '', refs = [] }) {
  const all = series.flatMap(s => s.values);
  const L = lo ?? Math.min(0, ...all), H = hi ?? Math.max(1, ...all);
  return html`<svg class="${cls}" ${key ? html`data-scrub="${key}"` : ''} viewBox="0 0 1000 ${h}" preserveAspectRatio="none"
      style="width:100%;height:${h}px;${rule ? 'border-bottom:1.5px solid var(--ink);' : ''}overflow:visible" role="img" aria-label="${series.map(s => s.label || '').join(', ')}">
    ${refs.map(r => raw(`<line x1="0" x2="1000" y1="${yOf(r, h, L, H).toFixed(1)}" y2="${yOf(r, h, L, H).toFixed(1)}" stroke="var(--line2)" stroke-dasharray="4 5" vector-effect="non-scaling-stroke"/>`))}
    ${zero && L < 0 && H > 0 ? raw(`<line x1="0" x2="1000" y1="${yOf(0, h, L, H).toFixed(1)}" y2="${yOf(0, h, L, H).toFixed(1)}" stroke="var(--line2)" stroke-dasharray="4 5" vector-effect="non-scaling-stroke"/>`) : ''}
    ${series.map(s => {
      const d = s.xy ? pathXY(s.xy, h, L, H) : path(s.values, h, L, H);
      if (!d) return '';
      const base = yOf(Math.max(L, Math.min(H, s.fillTo ?? L)), h, L, H).toFixed(1);
      const area = `${d} L1000 ${base} L0 ${base} Z`;
      let fill = s.fill ? `<path d="${area}" fill="${s.fill}"/>` : '';
      if (s.fill && s.fillNeg) {
        const id = `lc${++clipN}`;
        fill = `<defs><clipPath id="${id}u"><rect x="0" y="-1" width="1000" height="${(+base + 1).toFixed(1)}"/></clipPath>`
          + `<clipPath id="${id}d"><rect x="0" y="${base}" width="1000" height="${(h - base + 1).toFixed(1)}"/></clipPath></defs>`
          + `<path d="${area}" fill="${s.fill}" clip-path="url(#${id}u)" data-fill="up"/>`
          + `<path d="${area}" fill="${s.fillNeg}" clip-path="url(#${id}d)" data-fill="down"/>`;
      }
      return raw(`${fill}<path d="${d}" fill="none" stroke="${s.color || 'var(--ink)'}" stroke-width="${s.width || 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ''} vector-effect="non-scaling-stroke"/>`);
    })}
  </svg>`;
}

// Bars growing up for gains and down for losses around a centre line.
export function upDownBars(values, tips, h = 200) {
  const mx = Math.max(1, ...values.map(Math.abs));
  return html`<div class="ud-bars" style="height:${h}px">${values.map((p, i) => html`
    <div class="ud" data-tip="${tips[i]}"><div class="up"><i class="grow-y" style="height:${p > 0 ? p / mx * 100 : 0}%"></i></div><div class="dn"><i class="grow-y" style="height:${p < 0 ? -p / mx * 100 : 0}%"></i></div></div>`)}</div>`;
}

// Histogram from analytics().hist
export const histogram = (bins, h = 160) => html`<div class="hist" style="height:${h}px">${bins.map((b, i) => html`
  <div class="grow-y" data-tip="${b.tip}" style="height:${b.h}%;background:${b.pos ? 'var(--pos)' : 'var(--r)'};animation-delay:${i * 15}ms"></div>`)}</div>`;

// Monthly heatmap from analytics(): rows of 12 cells + year + running.
export function heatmap(a) {
  return html`<div class="heat">
    <div></div>${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'].map(m => html`<div class="hh">${m}</div>`)}<div class="hh b">Year</div><div class="hh b right">Running total</div>
    ${a.yRows.map(r => html`<div class="hy mono">${r.y}</div>${r.cells.map(c => html`<div class="hc" data-tip="${c.tip}" style="background:${c.bg};color:${c.fg}">${c.v}</div>`)}
      <div class="hc ht" style="background:${r.tbg};color:${r.tfg}">${r.tot}</div><div class="hc hcum" style="color:${r.cc}">${r.cum}</div>`)}
    <div class="ha">Avg</div>${a.avg.map(c => html`<div class="hc havg" data-tip="${c.tip}" style="color:${c.fg}">${c.v}</div>`)}<div class="havg"></div><div class="havg"></div>
  </div>`;
}

// GitHub-style weekday calendar from analytics().weeks
export function calendar(a) {
  return html`<div class="cal">
    <div class="cal-days"><span></span><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span></div>
    ${a.weeks.map(w => html`<div class="cal-w"><span class="cal-m">${w.m}</span>${w.cells.map(c => html`<i data-tip="${c.tip}" style="background:${c.bg};border:${c.bd}"></i>`)}</div>`)}
  </div>
  <div class="row wrap end" style="gap:22px;padding-top:14px">
    <div class="cal-legend"><span>Loss</span>${a.legend.map(c => html`<i style="background:${c}"></i>`)}<span>Profit</span><span class="row" style="gap:4px;padding-left:12px"><i style="border:1px solid var(--line)"></i>not running</span></div>
    <span class="grow"></span>
    ${a.calStats.map(s => html`<div class="col" style="gap:0"><span class="tiny mute">${s.l}</span><b class="mono small" style="color:${s.c}">${s.v}</b></div>`)}
  </div>`;
}

// Horizontal bar from a centre line (contribution, weekday averages).
export function centreBar(v, mx, { split = 50 } = {}) {
  const share = v > 0 ? 100 - split : split;
  const w = Math.abs(v) / (mx || 1) * share;
  return html`<div class="cbar"><i class="zero" style="left:${split}%"></i><i class="grow-x" style="left:${v >= 0 ? split : split - w}%;width:${w}%;background:${v >= 0 ? 'var(--pos)' : 'var(--r)'}"></i></div>`;
}
