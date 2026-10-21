// Adjustments (DTT adjusted / advanced): every leg it added, rolled or
// unwound — what it saw, the strike and premium, and how that leg did —
// with the running total of what the extra legs earned.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, dStamp } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, tone, svg, path, hit, tipAt, yLabels, wide } from './ui.js';
import { mins } from './st.js';
import { MOVE, ROLE } from './adj.js';

const KC = { adjustment: 'var(--ink)', roll: 'var(--b)', unwind: 'var(--g)', roll_close: 'var(--c4)' };
const moveTip = r => `${MOVE[r.kind]} · ${dStamp(r.t)}\n${r.symbol}  ${ROLE(r.role)}\n${r.kind === 'adjustment' || r.kind === 'roll' ? `Sold at  ${price(r.premium)}${r.target_premium != null ? ` (aimed ${price(r.target_premium)})` : ''}` : `Bought back at  ${price(r.premium)}`}${r.ratio != null ? `\n${r.side} side ÷ other  ${Math.round(r.ratio * 100)}%` : ''}${r.held_min != null ? `\nHeld  ${mins(r.held_min)}` : ''}${r.pnl != null ? `\nLeg P&L  ${inr(r.pnl)}` : ''}${r.closed_by && (r.kind === 'adjustment' || r.kind === 'roll') ? `\nClosed by  ${r.closed_by.replace(/_/g, ' ')}` : ''}`;

function running(cum) {
  if (!cum.length) return '';
  const W = 1200, H = 220, vs = cum.map(c => c.total), hi = Math.max(0, ...vs), lo = Math.min(0, ...vs), pad = (hi - lo || 1) * 0.08;
  const x = i => 70 + (cum.length === 1 ? 0.5 : i / (cum.length - 1)) * (W - 90), y = v => 12 + (hi + pad - v) / (hi - lo + 2 * pad) * (H - 40);
  let s = `<line x1="64" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>` + yLabels([0, lo, hi], y, 58);
  s += `<path d="${path(cum.map((c, i) => [x(i), y(c.total)]))}" fill="none" stroke="var(--ink)" stroke-width="1.8"/>`;
  cum.forEach((c, i) => { s += `<circle cx="${x(i)}" cy="${y(c.total)}" r="4" fill="${c.pnl >= 0 ? 'var(--g)' : 'var(--r)'}"${tipAt(`${MOVE[c.kind]} · ${dStamp(c.t)}\n${c.symbol}\nThis leg  ${inr(c.pnl)}\nRunning total  ${inr(c.total)}`)}/>`; });
  s += `<text x="70" y="${H - 6}">${dStamp(cum[0].t)}</text><text x="${W - 20}" y="${H - 6}" text-anchor="end">${dStamp(cum[cum.length - 1].t)}</text>`;
  return wide(svg(W, H, s, 'Running total of what the extra legs earned', {
    series: [{ name: 'Running total', color: 'var(--ink)', pts: cum.map((c, i) => [x(i), y(c.total), inr(c.total), `${MOVE[c.kind]} · ${dStamp(c.t)}`]) }],
  }));
}

function byHour(hours) {
  if (!hours.length) return '';
  const W = 1200, H = 170, ks = ['adjustment', 'roll', 'unwind', 'roll_close'];
  const mx = Math.max(1, ...hours.map(h => ks.reduce((a, k) => a + h[k], 0))), bw = Math.min(80, (W - 80) / hours.length - 20);
  let s = `<line x1="40" x2="${W - 10}" y1="${H - 26}" y2="${H - 26}" stroke="var(--ink)"/>`;
  hours.forEach((h, i) => {
    const X = 60 + i * (bw + 20);
    let base = H - 26;
    for (const k of ks) { const hh = h[k] / mx * (H - 50); if (hh) { s += `<rect x="${X}" y="${base - hh}" width="${bw}" height="${hh}" fill="${KC[k]}"/>`; base -= hh; } }
    s += `<text x="${X + bw / 2}" y="${H - 8}" text-anchor="middle">${String(h.hour).padStart(2, '0')}:00</text>`;
    s += hit(X - 6, 6, bw + 12, H - 32, `${String(h.hour).padStart(2, '0')}:00–${String(h.hour + 1).padStart(2, '0')}:00\nAdjustments  ${h.adjustment}\nRolls  ${h.roll}\nUnwinds  ${h.unwind}`);
  });
  return wide(svg(W, H, s, 'Moves by hour of day'));
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Adjustments')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Adjustments')}${empty('Couldn’t load the adjustments', data.message)}`); return; }
      const k = data.by_kind, z = { n: 0, pnl: 0, scored: 0, wins: 0 };
      const adj = k.adjustment || z, roll = k.roll || z, unw = k.unwind || z;
      const sold = adj.scored + roll.scored, earned = adj.pnl + roll.pnl;
      mount(el, html`
        ${head('Adjustments', `Every leg it added${data.rolling ? ' or rolled in' : ''} when a side fell, and every one it unwound when that side recovered: what it saw, the strike and premium, and how the leg did.`, `${data.rows.length} moves`)}
        ${data.rows.length ? html`
        ${tiles([
          ['Adjustments', String(adj.n), adj.scored ? `${adj.wins} of ${adj.scored} legs made money` : 'none closed yet'],
          ...(data.rolling ? [['Rolls', String(roll.n), roll.scored ? `${roll.wins} of ${roll.scored} legs made money` : 'none closed yet']] : []),
          ['Reversal unwinds', String(unw.n), 'cheapest leg closed as a side recovered'],
          ['Extra legs earned', sold ? inr(earned) : '—', sold ? `avg ${inr(earned / sold)} a leg` : '', tone(earned)],
        ], data.rolling ? 4 : 3)}
        <div class="gk-panel"><h3>What the extra legs earned, leg by leg <span class="s">running total · green dot a leg that made money</span></h3>${running(data.cumulative) || empty('', 'Appears once an extra leg has closed.')}</div>
        <div class="gk-panel"><h3>When in the day <span class="s"><b style="color:var(--ink)">■ adjustment</b> <b style="color:var(--b)">■ roll</b> <b style="color:var(--g)">■ unwind</b></span></h3>${byHour(data.by_hour)}</div>
        <div class="gk-panel"><h3>Every move <span class="s">newest first · open its day</span></h3>
          ${table([['When', '128px'], ['Move', '130px'], ['Leg', 'minmax(170px,1.4fr)'], ['Premium', '160px', 'num'], ['Side ÷ other', '96px', 'num'], ['Held', '70px', 'num'], ['Leg P&L', '90px', 'num']],
            data.rows.map(r => [html`<a href="${g.href('session')}?d=${r.day}" class="mono tiny">${dStamp(r.t)}</a>`, MOVE[r.kind],
              html`<b class="${r.side === 'CE' ? 'leg-ce' : 'leg-pe'}">${r.symbol}</b>`,
              html`<span class="mono small">${price(r.premium)}${r.target_premium != null ? html` <span class="mute">/ ${price(r.target_premium)}</span>` : ''}</span>`,
              r.ratio == null ? '—' : `${Math.round(r.ratio * 100)}%`, mins(r.held_min),
              html`<span class="${tone(r.pnl)}">${r.pnl == null ? '—' : inr(r.pnl)}</span>`]), { minw: 900, rowAttrs: i => html`data-tip="${moveTip(data.rows[i])}"` })}
          <p class="gk-note">Premium: what the leg was sold or bought back at; for a leg sold, the price it aimed for after the slash.</p></div>`
        : empty('No adjustments yet', 'They appear once a side falls to the trigger.')}`);
    }
    return poll(() => api.geek(g.id, 'moves'), 60000, d => { data = d; paint(); });
  },
};
