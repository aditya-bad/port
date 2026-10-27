// Pieces LinckLens's pages share: figures, the year's progress, notes,
// the "needs a look" cards, bars, a 52-week range, a little line.

import { html } from '../../core/dom.js';
import { inr, pct, tone } from '../../core/format.js';

export const pctOr = (v, dp = 2) => (v == null ? '—' : pct(v, dp));
export const inrOr = (v, o) => (v == null ? '—' : inr(v, o));
export const inr0 = v => inr(v, { sign: false });
export const signed = (v, text) => html`<span class="${tone(v)}">${text}</span>`;
export const PERIOD_LABEL = { '1W': '1 week', '1M': '1 month', '3M': '3 months', '6M': '6 months', '1Y': '1 year' };

// The year of prices, while the server fetches it a share at a time.
export function yearProgress(y, yErr) {
  if (yErr) return html`<p class="small mute lens-note" data-year="error">The year of prices couldn’t be read: ${yErr.message}</p>`;
  if (!y || y.state === 'loading') {
    const done = y ? y.done : 0, total = y ? y.total : 0;
    const w = total ? Math.round(done / total * 100) : 4;
    return html`<div class="lens-progress" data-year="loading" role="status" aria-live="polite">
      <span class="small"><i class="spin" aria-hidden="true"></i> Reading a year of prices from Kite${total ? html`: <b class="mono">${done} of ${total}</b>` : '…'}</span>
      <div class="bar"><i style="width:${w}%"></i></div>
      <span class="tiny mute">One request a share, through the app’s own Kite limit. Kept in the database: after this, only each new day is read, at 17:00.</span></div>`;
  }
  return '';
}

// "Needs a look": each with why, and which holdings.
const TONE = { warn: ['Look', 'warn'], good: ['Good', 'good'], info: ['Know', 'info'] };
export function attentionList(items) {
  if (!items || !items.length) return html`<p class="small mute" data-attn="none">Nothing stands out: no Regular plans, no single company over a fifth, nothing near its yearly low, nothing waiting on delivery.</p>`;
  return html`<div class="lens-attn">${items.map(a => html`<article class="lens-a ${TONE[a.tone][1]}" data-attn="${a.key}">
    <span class="lens-a-tone">${TONE[a.tone][0]}</span>
    <div class="col" style="gap:4px;min-width:0"><b>${a.title}</b>${a.detail ? html`<span class="small mute">${a.detail}</span>` : ''}
      ${a.items && a.items.length ? html`<div class="lens-a-items">${a.items.map(x => html`<span class="tag mono" title="${x}">${x}</span>`)}</div>` : ''}</div>
  </article>`)}</div>`;
}

// Horizontal bars, one colour: a label, the bar, the value and share.
export function bars(rows, { tip } = {}) {
  const mx = Math.max(...rows.map(r => r.weight || 0), 1);
  return html`<div class="lens-bars">${rows.map(r => html`<div class="lens-lb" data-tip="${tip ? tip(r) : `${r.label}\nValue  ${inr0(r.value)}\nShare  ${(r.weight || 0).toFixed(1)}%`}">
    <span class="lens-lb-l">${r.label}${r.count != null ? html` <span class="tiny mute">${r.count}</span>` : ''}</span>
    <span class="lens-lb-b"><i style="width:${(r.weight || 0) / mx * 100}%"></i></span>
    <span class="lens-lb-v mono">${inr0(r.value)}</span><span class="lens-lb-p mono">${(r.weight || 0).toFixed(1)}%</span></div>`)}</div>`;
}

// Where the price sits in the year's range: low on the left, high on the
// right, your average cost marked when it's inside the range.
export function rangeBar(y, cost) {
  if (!y || y.range_pos == null) return html`<span class="mute">—</span>`;
  const c = cost && y.hi52 > y.lo52 ? (cost - y.lo52) / (y.hi52 - y.lo52) * 100 : null;
  return html`<span class="lens-range" data-tip="${`52-week range\nLow  ₹${y.lo52.toLocaleString('en-IN')}\nHigh  ₹${y.hi52.toLocaleString('en-IN')}\nNow  ${y.range_pos.toFixed(0)}% of the way up\nOff the high  ${pct(y.off_high)}${cost ? `\nYour average cost  ₹${cost.toLocaleString('en-IN')}` : ''}`}">
    <i class="track"></i>${c != null && c >= 0 && c <= 100 ? html`<i class="cost" style="left:${c}%"></i>` : ''}<i class="now" style="left:${y.range_pos}%"></i></span>`;
}

// A little line of the year (no axes: it's for its shape).
export function spark(values, { w = 96, h = 26 } = {}) {
  if (!values || values.length < 2) return html`<span class="mute">—</span>`;
  const lo = Math.min(...values), hi = Math.max(...values), span = (hi - lo) || 1;
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${(i / (values.length - 1) * w).toFixed(1)} ${(h - 2 - (v - lo) / span * (h - 4)).toFixed(1)}`).join(' ');
  const up = values[values.length - 1] >= values[0];
  return html`<svg class="lens-spark ${up ? 'up' : 'down'}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><path d="${d}"/></svg>`;
}

export const ASSET_SHORT = { stock: 'Stock', etf: 'ETF', gold: 'Gold', silver: 'Silver', debt: 'Debt', reit: 'REIT/InvIT' };
