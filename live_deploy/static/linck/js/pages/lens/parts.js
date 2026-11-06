// Pieces LinckLens's pages share: figures, the year's progress, notes,
// the "needs a look" cards, bars, a 52-week range, a little line.

import { html } from '../../core/dom.js';
import { inr, pct, tone, num, price, dShort, fromDay } from '../../core/format.js';

export const pctOr = (v, dp = 2) => (v == null ? '—' : pct(v, dp));
export const inrOr = (v, o) => (v == null ? '—' : inr(v, o));
export const inr0 = v => inr(v, { sign: false });
export const signed = (v, text) => html`<span class="${tone(v)}">${text}</span>`;
export const PERIOD_LABEL = { '1W': '1 week', '1M': '1 month', '3M': '3 months', '6M': '6 months', '1Y': '1 year' };

// A tradebook upload's result (data/lensimport.js), until dismissed.
export function importBanner(n) {
  if (!n) return '';
  return html`<div class="lens-upnote ${n.tone}" role="${n.tone === 'bad' ? 'alert' : 'status'}" data-import-note="${n.tone}">
    <span class="ic" aria-hidden="true">${n.tone === 'good' ? '✓' : '!'}</span>
    <div><b data-import-title>${n.title}</b>${n.lines.length ? html`<ul class="small" data-imported>${n.lines.map(l => html`<li>${l}</li>`)}</ul>` : ''}</div>
    <button type="button" class="x" data-act="dismissImport" aria-label="Dismiss">×</button></div>`;
}

// The upload button: Console tradebook CSVs, several at once.
export function importButton(busy, label = 'Import tradebook files', cls = 'btn btn-sm') {
  return html`<label class="${cls} ${busy ? 'disabled' : ''}" data-import-btn>
    <input type="file" accept=".csv,.tsv,.txt,text/csv,text/plain" multiple data-f="files" aria-label="Upload tradebook files" ${busy ? 'disabled' : ''} hidden>
    ${busy ? html`<i class="spin" aria-hidden="true"></i> Uploading…` : label}</label>`;
}

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

// HOLDING DEEP DIVE: one holding as your uploaded tradebook has it — how
// much is long term now and what it's made, the next lot to turn long
// term, each open lot, and every buy and sell as uploaded. `p`: the
// holding's row in the trades report (data/lens.js tradesOf); `t`: that
// report (null while it loads); `dp`: decimals of a quantity.
export function dive(p, t, dp = 0) {
  if (!t) return html`<div class="ls-dive"><p class="small mute">Reading your tradebook…</p></div>`;
  if (!t.imports || !t.imports.length) return html`<div class="ls-dive"><p class="small mute">Import your Console tradebook on the Trades tab to see every buy and sell of it here, and how much is long term.</p></div>`;
  if (!p) return html`<div class="ls-dive"><p class="small mute">No trades for it in the tradebook you uploaded: it may have come from an IPO, a bonus, a transfer, or a year not uploaded.</p></div>`;
  const q = v => num(v, dp);
  const cell = (l, v, s) => html`<div><span class="l">${l}</span><b class="mono">${v}</b>${s ? html`<span class="s">${s}</span>` : ''}</div>`;
  const next = p.next_long_term && p.lots.filter(l => l.long_term_on === p.next_long_term).reduce((a, l) => a + l.qty, 0);
  const off = p.held_qty != null && Math.abs(p.held_qty - p.open_qty) > 0.001;
  const hist = [...p.history].reverse();
  const SHOWN = 12;
  const row = x => html`<div class="tr trd-row" role="row">
      <span data-l="Date">${dShort(fromDay(x.day))}</span>
      <span data-l="Trade"><span class="tag ${x.type === 'buy' ? '' : 'warn'}">${x.type === 'buy' ? 'Bought' : 'Sold'}</span></span>
      <span class="num" data-l="Qty">${q(x.qty)}</span><span class="num" data-l="At">${price(x.price)}</span>
      <span class="num" data-l="Value">${inr0(x.value)}</span><span data-l="Where" class="tiny mute">${x.exchange || ''}</span></div>`;
  return html`<div class="ls-dive" data-dive="${p.isin || p.symbol}">
    <div class="ls-cells">
      ${cell('Long term', q(p.long_qty), p.long_gain == null ? 'held over a year' : html`<span class="${tone(p.long_gain)}">${inr(p.long_gain)}</span> on paper`)}
      ${cell('Short term', q(p.short_qty), p.short_gain == null ? 'held a year or less' : html`<span class="${tone(p.short_gain)}">${inr(p.short_gain)}</span> on paper`)}
      ${p.slab_qty ? cell('Slab rate', q(p.slab_qty), 'debt: taxed at your rate however long') : ''}
      ${cell('Next to turn long term', p.next_long_term ? dShort(fromDay(p.next_long_term)) : '—', p.next_long_term ? `${q(next)} from then` : 'none waiting')}
      ${cell('Booked', p.realized ? html`<span class="${tone(p.realized)}">${inr(p.realized)}</span>` : '—', 'from what you’ve sold')}
      ${cell('Return', p.xirr != null ? pct(p.xirr, 1) : p.return_pct != null ? pct(p.return_pct, 1) : '—', p.xirr != null ? 'a year (XIRR)' : p.return_pct != null ? `in ${p.days} days` : '')}
      ${cell('Trades', String(p.trades), `${dShort(fromDay(p.first_day))} – ${dShort(fromDay(p.last_day))}`)}
      ${cell('Put in / taken out', `${inr0(p.bought)}`, `${inr0(p.sold)} out`)}
    </div>
    ${off ? html`<p class="tiny warn" style="margin:8px 0 0">Your tradebook leaves ${q(p.open_qty)}; Kite holds ${q(p.held_qty)}. A bonus, split, IPO, transfer or a year not uploaded makes up the difference: long and short term above count only what the tradebook shows.</p>` : ''}
    ${p.lots.length ? html`<h4 class="ls-sub">What you hold, lot by lot</h4><div class="tbl" role="table">
      <div class="tr th lot-row" role="row"><span>Bought</span><span class="num">Qty</span><span class="num">At</span><span class="num">Held</span><span>Term</span><span class="num">On paper</span></div>
      ${p.lots.map(l => html`<div class="tr lot-row" role="row">
        <span data-l="Bought">${dShort(fromDay(l.day))}</span><span class="num" data-l="Qty">${q(l.qty)}</span>
        <span class="num" data-l="At">${price(l.price)}</span><span class="num" data-l="Held">${l.days} days</span>
        <span data-l="Term">${l.term === 'long' ? html`<span class="tag">Long term</span>` : l.term === 'slab' ? html`<span class="tag">Slab rate</span>`
          : html`<span class="tag">Short term</span> <span class="tiny mute">long from ${dShort(fromDay(l.long_term_on))}</span>`}</span>
        <span class="num ${tone(l.gain)}" data-l="On paper">${l.gain == null ? '—' : inr(l.gain)}</span></div>`)}
    </div>` : ''}
    <h4 class="ls-sub">Every trade, newest first</h4>
    <div class="tbl" role="table" data-trades="${p.isin || p.symbol}">
      <div class="tr th trd-row" role="row"><span>Date</span><span>Trade</span><span class="num">Qty</span><span class="num">At</span><span class="num">Value</span><span>Where</span></div>
      ${hist.slice(0, SHOWN).map(row)}
    </div>
    ${hist.length > SHOWN ? html`<details class="ls-more"><summary class="small">${hist.length - SHOWN} older trade${hist.length - SHOWN === 1 ? '' : 's'}</summary><div class="tbl" role="table">${hist.slice(SHOWN).map(row)}</div></details>` : ''}
  </div>`;
}

// A holding's long-term quantity, for the line under its quantity: from the
// tradebook, never more than Kite holds.
export function longLine(p, held, dp = 0) {
  if (!p || !p.long_qty) return '';
  const lt = Math.min(p.long_qty, held);
  return html`<small data-tip="${`From your tradebook: ${num(lt, dp)} held over a year (long term), ${num(Math.max(held - lt, 0), dp)} a year or less`}">${num(lt, dp)} LT</small>`;
}
