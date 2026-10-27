// LinckLens › Shares: every listed holding (stocks, ETFs, gold, REITs),
// what it cost and is worth, today, its year and where it sits in it; a
// row opens to its year against your cost, and its numbers beside Nifty's.

import { html } from '../../core/dom.js';
import { inr, pct, price, num, plural, tone, dShort, fromDay } from '../../core/format.js';
import { stat, chips } from '../../ui/components.js';
import { line, yOf } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { yearOf } from '../../data/lens.js';
import { pctOr, inr0, signed, yearProgress, rangeBar, spark, ASSET_SHORT } from './parts.js';

export const SORTS = [['value', 'Value'], ['pnl', 'P&L ₹'], ['pnl_pct', 'P&L %'], ['day', 'Today %'], ['y1', '1 year'], ['off_high', 'Off the high'], ['name', 'Name']];
const key = {
  value: r => r.value, pnl: r => r.pnl, pnl_pct: r => r.pnl_pct, day: r => r.day_change_pct,
  y1: r => yearOf(r.symbol)?.ret?.['1Y'], off_high: r => yearOf(r.symbol)?.off_high, name: r => r.symbol,
};

export function shown(rows, ui) {
  const q = (ui.q || '').trim().toUpperCase();
  let out = rows.filter(r => (ui.asset === 'all' || r.asset === ui.asset)
    && (!q || r.symbol.includes(q) || (r.name || '').toUpperCase().includes(q)));
  const k = key[ui.sort] || key.value;
  out = [...out].sort((a, b) => {
    const x = k(a), y = k(b);
    if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;   // not known: last, either way
    const c = typeof x === 'string' ? x.localeCompare(y) : y - x;
    return ui.dir === 'asc' ? -c : c;
  });
  return out;
}

// Nifty on each of the share's days (its last close on or before), scaled
// to start at the share's first price: the same money in the index.
export function niftyAlong(n, days, start) {
  if (!n || !n.spark || n.spark.length < 2 || !days.length) return null;
  const at = d => { let v = null; for (let i = 0; i < n.spark_days.length && n.spark_days[i] <= d; i++) v = n.spark[i]; return v; };
  const xs = days.map(at);
  if (xs[0] == null) return null;
  return xs.map(v => (v ?? xs[0]) / xs[0] * start);
}

// One holding's year, opened: the price over the year against your average
// cost, and its numbers beside Nifty's.
function detail(r, st) {
  const y = yearOf(r.symbol), n = st.y && st.y.state === 'ready' ? st.y.insights.nifty : null;
  const H = 160;
  let chart = '';
  if (y && y.spark && y.spark.length > 1) {
    const vals = y.spark, nv = niftyAlong(n, y.spark_days, vals[0]);
    const all = [...vals, r.avg, ...(nv || [])];
    const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.08 || 1;
    const L = lo - pad, Hh = hi + pad, k = `lens-sh-${r.symbol}`;
    registerScrub(k, f => {
      const i = Math.round(f * (vals.length - 1));
      return { t: `${r.symbol} · ${dShort(fromDay(y.spark_days[i]))}\nPrice  ₹${price(vals[i])}  (${pct((vals[i] / vals[0] - 1) * 100, 1)})`
          + `${nv ? `\nNifty 50  ${pct((nv[i] / nv[0] - 1) * 100, 1)}` : ''}\nAgainst your cost  ${pct((vals[i] / r.avg - 1) * 100)}`,
        ys: [yOf(vals[i], H, L, Hh) / H, ...(nv ? [yOf(nv[i], H, L, Hh) / H] : [])], cs: ['var(--ink)', 'var(--b)'] };
    });
    chart = html`<div class="lens-plot">
      <div class="lens-legend tiny"><span><i class="key ink"></i>${r.symbol}</span>${nv ? html`<span><i class="key b"></i>Nifty 50, from the same start</span>` : ''}<span><i class="key dash"></i>your average cost ₹${price(r.avg)}</span></div>
      ${line({ series: [...(nv ? [{ values: nv, color: 'var(--b)', width: 1.5, label: 'Nifty 50' }] : []), { values: vals, color: 'var(--ink)', width: 2, label: r.symbol }], h: H, key: k, lo: L, hi: Hh, refs: [r.avg] })}
      <div class="lens-axis tiny mute mono"><span>${dShort(fromDay(y.spark_days[0]))}</span><span>${dShort(fromDay(y.spark_days[y.spark_days.length - 1]))}</span></div></div>`;
  }
  const cell = (l, v, s) => html`<div><span class="l">${l}</span><b class="mono">${v}</b>${s ? html`<span class="s">${s}</span>` : ''}</div>`;
  return html`<div class="ls-detail" data-detail="${r.symbol}">
    ${chart || html`<p class="small mute">${st.y && st.y.state === 'ready' ? 'No price history from Kite for it.' : 'Its year comes in with the rest of the prices.'}</p>`}
    <div class="ls-cells">
      ${y ? ['1W', '1M', '3M', '6M', '1Y'].map(k => cell(k, html`<span class="${tone(y.ret[k])}">${pctOr(y.ret[k], 1)}</span>`,
        n && n.ret[k] != null ? `Nifty ${pct(n.ret[k], 1)}` : '')) : ''}
      ${y ? cell('52-week high', `₹${price(y.hi52)}`, `${pct(y.off_high, 1)} from it`) : ''}
      ${y ? cell('52-week low', `₹${price(y.lo52)}`, `${pct((r.ltp / y.lo52 - 1) * 100, 1)} above it`) : ''}
      ${y && y.vol != null ? cell('Swings', `${y.vol.toFixed(0)}%`, `a year · Nifty ${n && n.vol != null ? `${n.vol.toFixed(0)}%` : '—'}`) : ''}
      ${y && y.beta != null ? cell('Beta', y.beta.toFixed(2), y.beta > 1.1 ? 'moves more than Nifty' : y.beta < 0.9 ? 'moves less than Nifty' : 'moves with Nifty') : ''}
      ${cell('Bought at', `₹${price(r.avg)}`, `${num(r.qty, 0)} × = ${inr0(r.invested)}`)}
      ${cell('Breakeven', r.pnl < 0 ? `${pct((r.avg / r.ltp - 1) * 100, 1)}` : 'above it', r.pnl < 0 ? 'rise needed to get back to cost' : `${pct((r.ltp / r.avg - 1) * 100, 1)} over cost`)}
      ${r.t1_qty ? cell('Not yet delivered', num(r.t1_qty, 0), 'T1: in your demat next settlement day') : ''}
      ${r.pledged_qty ? cell('Pledged', num(r.pledged_qty, 0), 'backing margin') : ''}
      ${cell('ISIN', r.isin || '—', r.exchange || '')}
    </div></div>`;
}

export function render(st, ui) {
  const h = st.h, rows = h.equity, t = h.totals.equity;
  if (!rows.length) return html`<p class="small mute">No shares in this account.</p>`;
  const list = shown(rows, ui);
  const kinds = [['all', 'All', rows.length], ...Object.entries(ASSET_SHORT).map(([k, l]) => [k, l, rows.filter(r => r.asset === k).length]).filter(x => x[2])];
  const ready = st.y && st.y.state === 'ready';
  const y1 = ready && st.y.insights.curve ? st.y.insights.curve.periods.find(p => p.k === '1Y') : null;
  const mxw = Math.max(...rows.map(r => r.weight || 0), 1);
  return html`
    <div class="strip lens-strip">
      ${stat('In shares', inr0(t.value), plural(t.count, 'holding'))}
      ${stat('Gain on what you put in', signed(t.pnl, inr(t.pnl)), `${pctOr(t.pnl_pct)} on ${inr0(t.invested)}`)}
      ${stat('Today', t.day_change == null ? '—' : signed(t.day_change, inr(t.day_change)),
        t.day_change == null ? 'no previous close' : `${pctOr(t.day_change_pct)} · ${t.advancing} up, ${t.declining} down`)}
      ${stat('Over a year', y1 ? signed(y1.portfolio, pct(y1.portfolio, 1)) : '…',
        y1 ? (y1.nifty == null ? '' : `Nifty 50 ${pct(y1.nifty, 1)} · today’s shares`) : 'once the year of prices is in')}
    </div>
    ${ready ? '' : yearProgress(st.y, st.yErr)}
    <div class="lens-tools">
      <input class="input search" type="search" placeholder="Find a share" value="${ui.q || ''}" data-f="q" aria-label="Find a share">
      <div class="row wrap" style="gap:6px">${kinds.length > 2 ? chips(kinds, ui.asset, 'asset') : ''}</div>
      <span class="grow"></span>
      <label class="row small" style="gap:6px">Sort <select class="input" data-f="sort" style="height:34px">${SORTS.map(([k, l]) => html`<option value="${k}" ${k === ui.sort ? 'selected' : ''}>${l}</option>`)}</select></label>
      <button type="button" class="btn btn-sm" data-act="dir" aria-label="Reverse the order">${ui.dir === 'asc' ? '↑' : '↓'}</button>
    </div>
    <div class="tbl ls-tbl" role="table" data-lens="equity">
      <div class="tr th ls-row" role="row"><span>Share</span><span>Weight</span><span class="num">Qty</span><span class="num">Avg cost</span><span class="num">Price</span>
        <span class="num">Value</span><span class="num">P&L</span><span class="num">Today</span><span class="num">1 year</span><span>52-week range</span><span>Year</span></div>
      ${list.map(r => {
        const y = yearOf(r.symbol), open = ui.open === r.symbol;
        return html`<div class="tr ls-row click ${open ? 'sel' : ''}" role="row" data-symbol="${r.symbol}" data-act="open" data-s="${r.symbol}" aria-expanded="${open}">
          <span class="cell-name"><b>${r.symbol}${r.asset !== 'stock' ? html` <span class="tag">${ASSET_SHORT[r.asset]}</span>` : ''}</b><span>${r.name || r.exchange || ''}</span></span>
          <span class="ls-w" data-l="Weight" data-tip="${`${r.symbol}\nShare of everything  ${(r.weight || 0).toFixed(2)}%`}"><i style="width:${(r.weight || 0) / mxw * 100}%"></i><b class="mono tiny">${(r.weight || 0).toFixed(1)}%</b></span>
          <span class="num" data-l="Qty">${num(r.qty, 0)}${r.t1_qty ? html`<small>${num(r.t1_qty, 0)} T1</small>` : ''}</span>
          <span class="num" data-l="Avg cost">${price(r.avg)}</span>
          <span class="num" data-l="Price">${price(r.ltp)}</span>
          <span class="num" data-l="Value">${inr0(r.value)}</span>
          <span class="num ${tone(r.pnl)}" data-l="P&L">${inr(r.pnl)}<small>${pctOr(r.pnl_pct)}</small></span>
          <span class="num ${tone(r.day_change)}" data-l="Today">${pctOr(r.day_change_pct)}<small>${r.day_change == null ? '' : inr(r.day_change)}</small></span>
          <span class="num ${tone(y && y.ret['1Y'])}" data-l="1 year">${y ? pctOr(y.ret['1Y'], 1) : '…'}</span>
          <span data-l="52-week range">${y ? rangeBar(y, r.avg) : html`<span class="mute tiny">…</span>`}</span>
          <span data-l="Year">${y ? spark(y.spark) : ''}</span>
        </div>${open ? detail(r, st) : ''}`;
      })}
      ${list.length ? html`<div class="tr tot ls-row" role="row"><span>${list.length === rows.length ? plural(t.count, 'share') : `${list.length} of ${rows.length} shown`}</span><span></span><span></span><span></span><span></span>
        <span class="num" data-l="Value">${inr0(list.reduce((a, r) => a + r.value, 0))}</span>
        <span class="num ${tone(list.reduce((a, r) => a + r.pnl, 0))}" data-l="P&L">${inr(list.reduce((a, r) => a + r.pnl, 0))}</span>
        <span class="num ${tone(t.day_change)}" data-l="Today">${list.length === rows.length && t.day_change != null ? inr(t.day_change) : ''}</span><span></span><span></span><span></span></div>`
        : html`<p class="small mute" style="margin:10px 0">No share matches.</p>`}
    </div>
    <p class="tiny mute lens-note">Click a share for its year against what you paid. The range bar runs from the 52-week low to the high: the dot is the price now, the tick your average cost.</p>`;
}
