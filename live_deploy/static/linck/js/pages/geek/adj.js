// Pieces the adjusting DTT views share (intraday_dtt_adjusted and
// intraday_dtt_advanced): names, the Engine panel (each side's legs, the
// comparison the adjustment rule makes, the index in its break-even
// band), and the legs of a day drawn by strike over time.

import { html } from '../../core/dom.js';
import { inr, price, hhmm } from '../../core/format.js';
import { svg, tiles, table, tone, hit, tipAt, wide } from './ui.js';
import { mins } from './st.js';

export const EXIT = { profit_target_total: 'Profit target', breakeven_fallback: 'Break-even', force_exit: 'Square-off' };
export const exitName = k => (k == null ? 'Open' : EXIT[k] || k.replace(/_/g, ' '));
export const EXIT_COLOR = { profit_target_total: 'var(--g)', breakeven_fallback: 'var(--r)', force_exit: 'var(--b)' };
export const MOVE = { adjustment: 'Adjustment', roll: 'Roll · sold', roll_close: 'Roll · closed', unwind: 'Reversal unwind' };
export const ROLE = r => (r === 'original' ? 'original' : r ? r.replace('adjustment_', 'adj ') : '—');
const sideC = s => (s === 'CE' ? 'var(--c5)' : 'var(--c4)');
const day = d => new Date(`${d}T12:00:00+05:30`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
export const dayName = day;

// One session, for a tooltip.
export const sessionTip = s => `${day(s.day)} · ${s.strike ? s.strike.toLocaleString('en-IN') : ''} straddle\nCombined sold  ${s.combined_entry == null ? '—' : price(s.combined_entry)}\nAdjustments  ${s.adjustments}${s.rolls ? ` · rolls ${s.rolls}` : ''} · unwinds ${s.unwinds}${s.adjusted_side ? `\nAdjusted side  ${s.adjusted_side}` : ''}\nEnded by  ${exitName(s.reason)}${s.exit_t ? ` at ${s.exit_t.slice(11, 16)}` : ''}\nOriginal legs  ${s.pnl_original == null ? '—' : inr(s.pnl_original)}\nExtra legs  ${s.pnl_extra == null ? '—' : inr(s.pnl_extra)}\nP&L  ${s.pnl == null ? '—' : inr(s.pnl)}`;

// The index against the day's break-even band.
function band(be, spot) {
  if (!be) return '';
  const W = 1000, H = 92, span = be[1] - be[0], lo = be[0] - span * 0.15, hi = be[1] + span * 0.15;
  const x = v => 20 + (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (W - 40);
  let s = `<rect x="${x(be[0])}" y="30" width="${x(be[1]) - x(be[0])}" height="24" fill="var(--gs)"${tipAt(`Break-even band\nLower  ${Math.round(be[0]).toLocaleString('en-IN')}\nUpper  ${Math.round(be[1]).toLocaleString('en-IN')}\nWidth  ${Math.round(span).toLocaleString('en-IN')} pts`)}/>`;
  for (const [v, l] of [[be[0], 'lower'], [be[1], 'upper']]) {
    s += `<line x1="${x(v)}" x2="${x(v)}" y1="22" y2="62" stroke="var(--r)" stroke-width="2"/><text class="lab" x="${x(v)}" y="80" text-anchor="middle" style="fill:var(--r)">${l} ${Math.round(v).toLocaleString('en-IN')}</text>`;
    s += hit(x(v) - 7, 18, 14, 48, `Break-even ${l}  ${Math.round(v).toLocaleString('en-IN')}${spot != null ? `\nFrom the index  ${Math.round(Math.abs(spot - v)).toLocaleString('en-IN')} pts` : ''}\nThe index reaching it flattens everything`);
  }
  if (spot != null) {
    s += `<path d="M${x(spot)} 34 l-7 -12 h14z" fill="var(--ink)"/><line x1="${x(spot)}" x2="${x(spot)}" y1="34" y2="54" stroke="var(--ink)" stroke-width="2"/><text class="lab" x="${x(spot)}" y="16" text-anchor="middle">index ${Math.round(spot).toLocaleString('en-IN')}</text>`;
    s += hit(x(spot) - 8, 4, 16, 52, `Index  ${Math.round(spot).toLocaleString('en-IN')}\nTo the lower edge  ${Math.round(spot - be[0]).toLocaleString('en-IN')} pts\nTo the upper edge  ${Math.round(be[1] - spot).toLocaleString('en-IN')} pts`);
  }
  return svg(W, H, s, 'The index inside its break-even band');
}

// The smaller side as a share of the bigger, against the trigger and the unwind line.
function balance(a) {
  if (a.ratio == null) return '';
  const f = Math.max(0, Math.min(1.2, a.ratio)) / 1.2, t = a.trigger_ratio / 1.2;
  return html`<div class="dd-bar" data-tip="${`${a.smaller} side as a share of ${a.bigger}\nNow  ${Math.round(a.ratio * 100)}%\nAdjusts at  ${Math.round(a.trigger_ratio * 100)}%\nUnwinds at  100% (with extra legs open)`}">
    <div class="row" style="gap:8px;align-items:baseline"><b class="small">${a.smaller} side ÷ ${a.bigger}</b><span class="grow"></span><span class="mono small">${Math.round(a.ratio * 100)}%</span></div>
    <div style="position:relative;height:14px;background:var(--soft);margin:6px 0 4px">
      <i style="position:absolute;left:0;top:0;bottom:0;width:${(f * 100).toFixed(1)}%;background:${a.ratio <= a.trigger_ratio * 1.1 ? 'var(--r)' : 'var(--ink)'}"></i>
      <u style="position:absolute;top:-4px;bottom:-4px;left:${(t * 100).toFixed(1)}%;border-left:2px solid var(--r)"></u>
      <u style="position:absolute;top:-4px;bottom:-4px;left:${(100 / 1.2).toFixed(1)}%;border-left:2px dashed var(--ink)"></u></div>
    <div class="row mono tiny mute"><span>0</span><span class="grow"></span><span>adjust ${Math.round(a.trigger_ratio * 100)}% · even 100%</span></div></div>`;
}

// The Engine view's panel.
export function adPanel(a) {
  const legs = ['CE', 'PE'].flatMap(k => a.legs[k].map(l => ({ ...l, side: k })));
  if (!legs.length) {
    const last = a.last;
    return html`<div class="gk-panel" data-adj><h3>The day <span class="s">nothing open</span></h3>
      <p class="small">Sells the ATM straddle at ${a.entry_time}; ${a.rolling ? 'rolls' : 'adds up to ' + a.max_adjustments} leg${a.rolling ? 's' : a.max_adjustments === 1 ? '' : 's'} on a side that falls to ${Math.round(a.trigger_ratio * 100)}% of the other; flattens at ${Math.round(a.profit_pct * 100)}% of the straddle's premium, a break-even breach or ${a.force_exit}.</p>
      ${last ? html`<p class="gk-note">Last: ${dayName(last.day)} · ${exitName(last.reason)} · ${last.adjustments + last.rolls} move${last.adjustments + last.rolls === 1 ? '' : 's'}${last.pnl != null ? ` · ${inr(last.pnl)}` : ''}</p>` : ''}</div>`;
  }
  return html`<div class="gk-panel" data-adj><h3>The day <span class="s">${a.adjusted_side ? `${a.adjusted_side} side adjusted · ${a.legs[a.adjusted_side].length} legs` : 'no side adjusted yet'}</span></h3>
    ${tiles([
      ['Day P&L', a.day_pnl == null ? '—' : inr(a.day_pnl), a.target ? `target ${inr(a.target, { sign: false })}` : '', tone(a.day_pnl)],
      ['Booked today', a.realized_today == null ? '—' : inr(a.realized_today), 'legs already closed', tone(a.realized_today)],
      ['CE side', a.totals.CE == null ? '—' : price(a.totals.CE), `${a.legs.CE.length} leg${a.legs.CE.length === 1 ? '' : 's'}`],
      ['PE side', a.totals.PE == null ? '—' : price(a.totals.PE), `${a.legs.PE.length} leg${a.legs.PE.length === 1 ? '' : 's'}`],
    ], 4)}
    ${balance(a)}
    ${band(a.breakeven, a.spot)}
    ${table([['Leg', 'minmax(170px,1.4fr)'], ['Role', '90px'], ['Sold', '80px', 'num'], ['Now', '80px', 'num'], ['Per unit', '90px', 'num']],
      legs.map(l => [html`<b class="${l.side === 'CE' ? 'leg-ce' : 'leg-pe'}">${l.symbol}</b>`, ROLE(l.role), price(l.entry), l.now == null ? '—' : price(l.now),
        html`<span class="${tone(l.now == null ? null : l.entry - l.now)}">${l.now == null ? '—' : (l.entry - l.now).toFixed(2)}</span>`]), { minw: 520 })}</div>`;
}

// Every leg of a day as a bar at its strike, from sold to bought back.
export function legsLadder(s) {
  const L = s.legs.filter(l => l.strike != null);
  if (!L.length) return '';
  const ms = t => new Date(t).getTime();
  const t0 = Math.min(...L.map(l => ms(l.opened))), t1 = Math.max(...L.map(l => (l.closed ? ms(l.closed) : Date.now())));
  const W = 1200, left = 80, right = W - 150, ks = [...new Set(L.map(l => `${l.side} ${l.strike}`))].sort((a, b) => b.localeCompare(a));
  const H = 36 + ks.length * 26, y = k => 12 + ks.indexOf(k) * 26, x = t => left + (t - t0) / ((t1 - t0) || 1) * (right - left);
  let s0 = '';
  ks.forEach(k => { s0 += `<line x1="${left}" x2="${W - 12}" y1="${y(k) + 7}" y2="${y(k) + 7}" stroke="var(--line)"/><text x="${left - 6}" y="${y(k) + 11}" text-anchor="end">${k}</text>`; });
  for (const l of L) {
    const k = `${l.side} ${l.strike}`, a = x(ms(l.opened)), b = x(l.closed ? ms(l.closed) : t1);
    s0 += `<rect x="${a}" y="${y(k)}" width="${Math.max(3, b - a)}" height="14" fill="${sideC(l.side)}" opacity="${l.role === 'original' ? 1 : 0.6}"/>`;
    s0 += `<text class="lab" x="${b + 5}" y="${y(k) + 11}" style="font-size:10px">${ROLE(l.role)}${l.pnl != null ? ` · ${inr(l.pnl)}` : l.closed ? '' : ' · open'}</text>`;
    s0 += hit(a, y(k) - 4, b - a, 22, `${l.symbol} · ${ROLE(l.role)}\nSold  ${price(l.entry)} at ${hhmm(l.opened)}${l.opened_by && l.opened_by !== 'entry_time_reached' ? ` (${MOVE[l.opened_by === 'roll_open' ? 'roll' : l.opened_by] || l.opened_by})` : ''}\n${l.closed ? `Bought back  ${price(l.exit)} at ${hhmm(l.closed)} (${exitName(l.closed_by) === 'Open' ? '' : l.closed_by === 'reversal_unwind' ? 'reversal unwind' : l.closed_by === 'roll_close' ? 'roll' : exitName(l.closed_by)})\nHeld  ${mins((ms(l.closed) - ms(l.opened)) / 60000)}` : 'Still open'}${l.pnl != null ? `\nP&L  ${inr(l.pnl)}` : ''}`);
  }
  for (let t = Math.ceil(t0 / 3600000) * 3600000; t <= t1; t += 3600000) s0 += `<text x="${x(t)}" y="${H - 6}" text-anchor="middle">${hhmm(new Date(t))}</text>`;
  return wide(svg(W, H, s0, 'Every leg of the day at its strike, from sold to bought back'));
}
