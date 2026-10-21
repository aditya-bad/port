// Small building blocks shared by the Geek views.

import { html, raw, esc } from '../../core/dom.js';
import { registerScrub } from '../../ui/tooltip.js';
import { inr, dStamp } from '../../core/format.js';

export const head = (title, why, src) => html`<div class="gk-h"><h2>${title}</h2>${why ? html`<span class="why">${why}</span>` : ''}${src ? html`<span class="src">${src}</span>` : ''}</div>`;

export const tiles = (list, n = list.length) => html`<div class="gk-tiles" style="--n:${n}">${list.map(([l, v, s, cls]) => html`
  <div class="gk-tile"><span class="l">${l}</span><span class="v ${cls || ''}">${v}</span>${s ? html`<span class="s">${s}</span>` : ''}</div>`)}</div>`;

export function nextMove(eng, link) {
  if (!eng || !eng.next) return '';
  const n = eng.next;
  return html`<div class="gk-next" data-next><span class="k">NEXT MOVE</span><span class="t">${n.text}</span>
    <span class="c">${n.at ? `at ${n.at} IST` : eng.running ? 'live' : ''}${link ? html` · <a href="${link}">Engine →</a>` : ''}</span>
    ${n.detail ? html`<span class="d">${n.detail}</span>` : ''}</div>`;
}

const pctOf = x => `${Math.max(0, Math.min(100, x * 100)).toFixed(1)}%`;
export function gauge(r) {
  return html`<div class="gauge">
    ${r.band ? html`<s style="left:${pctOf(r.band[0])};width:${pctOf(r.band[1] - r.band[0])}"></s>` : ''}
    <i class="${r.tone === 'pos' ? 'pos' : ''}" style="width:${pctOf(r.fill || 0)}"></i>
    ${r.mark != null ? html`<u style="left:${pctOf(r.mark)}"></u>` : ''}</div>`;
}

const STATE = { idle: 'Idle', ok: 'On track', behind: 'In loss', armed: 'About to act', off: 'Off', done: 'Done today' };
export function stateChip(r) {
  let label = r.chip || STATE[r.state] || r.state;
  if (r.key === 'eod_gap_check' && r.state === 'armed') label = `Fires at ${(r.label.split('·')[1] || '').trim() || 'the check'}`;
  if (r.key === 'checkpoint_target' && r.state === 'ok' && r.pct != null) label = `${r.pct}% there`;
  return html`<span class="st-chip ${r.state}">${label}</span>`;
}

// A grid table: cols = [[label, width, cls]], rows = [[cells...], ...].
// rowAttrs(i) can add attributes to a row (e.g. data-c to make it a link).
export function table(cols, rows, { minw = 560, cls = '', rowCls = () => '', rowAttrs = () => '' } = {}) {
  const tpl = cols.map(c => c[1]).join(' ');
  return html`<div class="gk-tbl ${cls}" style="--minw:${minw}px">
    <div class="tr th" style="grid-template-columns:${tpl}">${cols.map(c => html`<span class="${c[2] || ''}">${c[0]}</span>`)}</div>
    ${rows.map((r, i) => html`<div class="tr ${rowCls(i)}" style="grid-template-columns:${tpl}" ${rowAttrs(i)}>${r.map((cell, j) => html`<span class="${cols[j][2] || ''}">${cell}</span>`)}</div>`)}
  </div>`;
}

export const money = v => (v == null ? '—' : inr(v));
export const tone = v => (v == null ? '' : v > 0.5 ? 'pos' : v < -0.5 ? 'neg' : '');
export const when = t => (t ? dStamp(t) : '—');
export const ratio = r => (r == null ? '—' : `${(r * 100).toFixed(0)}%`);
export const LABEL = {
  checkpoint_target: 'Checkpoint', contract_expiry_backstop: 'Expiry', convergence_stop: 'Convergence stop',
  open: 'Open', other: 'Other', continuous: 'Continuous', eod: 'EOD check', rollout: 'EOD roll-out',
  continuous_50pct_trigger: 'Continuous', eod_gap_check: 'EOD check', eod_rollout: 'EOD roll-out',
  initial_entry: 'Entry', close_everything: 'Close everything', manual_close: 'Closed by hand',
};

// Fetch now and every `ms`; stops on cleanup. fn gets the data or an Error.
export function poll(load, ms, fn) {
  let alive = true;
  const run = () => load().then(d => alive && fn(d), e => alive && fn(e instanceof Error ? e : new Error(String(e))));
  run();
  const iv = ms ? setInterval(run, ms) : null;
  return () => { alive = false; if (iv) clearInterval(iv); };
}

// A plain SVG chart in a fixed viewBox (text stays undistorted).
//
// EVERY chart is interactable (tests/e2e/charts-hover.e2e.mjs checks each
// one): a line over time passes `hover` and gets a guide that follows the
// pointer with every series' value at that point; a chart of separate
// marks (dots, bars, levels) puts tipAt(...) on each mark.
//   hover = { series: [{ name, color, pts: [[X, Y, value text, title?]] }],
//             title: (X, point) => text, extra: X => [lines] }
//   (X, Y in viewBox units; pts sorted by X)
let hoverN = 0;
export function svg(w, h, body, label = '', hover = null) {
  let key = '';
  if (hover && (hover.series.some(s => s.pts && s.pts.length) || (hover.title && hover.extra))) {
    key = `gkh${++hoverN}`;
    registerScrub(key, scrubber(w, h, hover));
  }
  return html`<svg class="gk-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${label}" ${key ? html`data-scrub="${key}"` : ''}>${raw(body)}</svg>`;
}

// The point of sorted pts nearest to X.
export function nearest(pts, X) {
  if (!pts || !pts.length) return null;
  let lo = 0, hi = pts.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (pts[m][0] < X) lo = m; else hi = m; }
  return Math.abs(pts[lo][0] - X) <= Math.abs(pts[hi][0] - X) ? pts[lo] : pts[hi];
}

// f (0..1 across the chart) -> the tooltip: a title, a line per series
// with its value nearest the pointer, and where each series' dot sits.
export function scrubber(w, h, { series, title, extra }) {
  return f => {
    const X = f * w, lines = [], ys = [], cs = [];
    let first = null;
    for (const s of series) {
      const p = nearest(s.pts, X);
      const far = p && s.reach != null && Math.abs(p[0] - X) > s.reach;
      if (!p || far) { ys.push(null); cs.push(s.color); continue; }
      first = first || p;
      lines.push(`${s.name}  ${p[2]}`);
      ys.push(p[1] / h);
      cs.push(s.color);
    }
    if (!first && !(title && extra)) return null;
    const head = title ? title(X, first) : first[3] || '';
    return { t: [head, ...lines, ...(extra ? extra(X) : [])].join('\n'), ys, cs };
  };
}

// A tip on one mark of an SVG chart (a dot, a bar, a level): lines
// "Title\nLabel  value", as an attribute for an SVG string.
export const tipAt = text => ` data-tip="${esc(text)}"`;
// An invisible column (or box) that carries a tip: for bars and for marks
// too thin to point at.
export const hit = (x, y, w, h, text) => `<rect class="hit" x="${x}" y="${y}" width="${Math.max(1, w)}" height="${Math.max(1, h)}" fill="transparent"${tipAt(text)}/>`;
// A wide chart (1200-unit viewbox): on a phone it keeps a readable
// minimum width and scrolls sideways inside its panel.
export const wide = chart => html`<div class="gk-wide">${chart}</div>`;
export const path = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join('');
// A line that doesn't pretend to know a stretch with no prices (Kite
// offline, a holiday): it breaks before each index in `gaps`, and the
// breaks come back separately to be drawn dotted.
export function gapped(pts, gaps = []) {
  const at = new Set(gaps), solid = [], dots = [];
  let cur = [];
  pts.forEach((p, i) => {
    if (i && at.has(i)) { solid.push(cur); dots.push([pts[i - 1], p]); cur = []; }
    cur.push(p);
  });
  solid.push(cur);
  return { solid: solid.filter(s => s.length > 1).map(path).join(''), gaps: dots.map(path).join('') };
}
// Booked-only P&L stays flat until each booking: a step line.
export const steps = pts => pts.flatMap((p, i) => (i ? [[p[0], pts[i - 1][1]], p] : [p]));

// Signed bars around a centre line: [[label, ₹ or null], ...].
export function bars(items) {
  const mx = Math.max(1, ...items.map(([, v]) => Math.abs(v || 0)));
  return html`<div class="col" style="gap:10px">${items.map(([l, v, tip]) => html`
    <div style="display:grid;grid-template-columns:150px minmax(0,1fr) 100px;gap:10px;align-items:center" class="small" data-tip="${tip || `${l}\nP&L  ${v == null ? 'none' : inr(v)}`}">
      <b>${l}</b>
      <div style="position:relative;height:16px;background:var(--soft)"><i style="position:absolute;top:0;bottom:0;${v >= 0 ? 'left:50%' : `right:50%`};width:${(Math.abs(v || 0) / mx * 50).toFixed(1)}%;background:${v >= 0 ? 'var(--g)' : 'var(--r)'}"></i><i style="position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--ink)"></i></div>
      <span class="mono tiny right ${tone(v)}">${v == null ? 'none' : inr(v)}</span></div>`)}</div>`;
}

// Y-axis labels at x for the given values, skipping any closer than 12px
// to one already drawn (0 and a small max would overlap). Rounded ₹.
export function yLabels(vals, y, x, fmt = v => inr(Math.round(v))) {
  const done = [];
  let s = '';
  for (const v of vals) {
    const Y = y(v);
    if (done.some(d => Math.abs(d - Y) < 16)) continue;
    done.push(Y);
    s += `<text x="${x}" y="${Y + 3}" text-anchor="end">${fmt(v)}</text>`;
  }
  return s;
}
