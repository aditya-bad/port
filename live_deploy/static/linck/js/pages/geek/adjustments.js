// Adjustments: did rolling pay? Each roll against keeping the leg it
// closed (priced from the stored 1-minute candles), the balance before
// and after, and which kind of roll earns its cost.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, dStamp } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, money, tone, ratio, LABEL } from './ui.js';

const legs = (xs, verb) => xs.map(f => `${verb} ${f.opt} ${f.strike ?? ''} @${Number(f.price).toFixed(1)}`).join(' · ');

function arrows(rows, eng) {
  const pts = rows.filter(r => r.ratio_before != null && r.ratio_after != null).reverse();
  if (!pts.length) return empty('', 'Appears after the first roll.');
  const eod = eng && eng.rules ? eng.rules.find(r => r.key === 'eod_gap_check') : null;
  const band = eod && eod.band ? eod.band : [0.8, 0.95];
  const W = 520, H = 190, lo = Math.min(0.35, ...pts.map(p => p.ratio_before)) - 0.02, hi = Math.max(1, ...pts.map(p => p.ratio_after));
  const y = r => 12 + (hi - r) / (hi - lo) * 150, x = i => 44 + (pts.length === 1 ? 0.5 : i / (pts.length - 1)) * (W - 64);
  let s = `<rect x="34" y="${y(band[1])}" width="${W - 44}" height="${y(band[0]) - y(band[1])}" fill="var(--gs)"/>`;
  [0.4, 0.6, 0.8, 1].filter(v => v >= lo && v <= hi).forEach(v => { s += `<line x1="34" x2="${W - 10}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="28" y="${y(v) + 3}" text-anchor="end">${v * 100}%</text>`; });
  pts.forEach((p, i) => {
    const c = p.rule === 'continuous' ? 'var(--ink)' : 'var(--b)', X = x(i), a = y(p.ratio_after), b = y(p.ratio_before);
    const dir = a < b ? 6 : -6;
    s += `<line x1="${X}" x2="${X}" y1="${b}" y2="${a + dir}" stroke="${c}" stroke-width="1.5"/><circle cx="${X}" cy="${b}" r="3" fill="${c}"/>`;
    s += `<path d="M${X - 4} ${a + dir} L${X} ${a} L${X + 4} ${a + dir}" fill="none" stroke="${c}" stroke-width="1.5"/>`;
  });
  s += `<g transform="translate(34,${H - 12})"><rect width="9" height="9" fill="var(--ink)"/><text x="13" y="8">continuous</text><rect x="96" width="9" height="9" fill="var(--b)"/><text x="109" y="8">EOD / roll-out</text></g>`;
  return svg(W, H, s, 'Balance before and after each roll');
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Adjustments')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Adjustments')}${empty('Couldn’t load adjustments', data.message)}`); return; }
      const s = data.summary;
      if (!data.rows.length) { mount(el, html`${head('Adjustments')}${empty('No rolls yet', 'Rolls show here with what each one earned against keeping the old leg.')}`); return; }
      const kinds = ['continuous', 'eod', 'rollout'].map(k => `${LABEL[k].toLowerCase()} ${data.by_rule[k].rolls}`).join(' · ');
      mount(el, html`
        ${head('Adjustments', 'Did rolling pay? Each roll against keeping the leg it closed, priced from the stored candles of that leg.', 'fills · candles_1m · charges')}
        ${tiles([
          ['Rolls', s.rolls, kinds],
          ['Rolls that helped', s.scored ? `${s.helped} of ${s.scored}` : '—', s.scored < s.rolls ? `${s.rolls - s.scored} without price history` : 'against keeping the old leg'],
          ['Net effect of rolling', s.net != null ? html`<span class="${tone(s.net)}">${inr(s.net)}</span>` : '—', 'after the new legs’ costs'],
          ['Avg cost per roll', s.avg_cost != null ? inr(s.avg_cost, { sign: false }) : '—', 'charges + slippage of the new leg'],
        ])}
        ${table([['When', '110px'], ['Rule', '110px'], ['Balance', '104px', 'num'], ['Closed → opened', 'minmax(220px,1.6fr)'], ['Booked', '92px', 'num'],
          ['If held', '92px', 'num'], ['Costs', '76px', 'num'], ['Roll paid', '100px', 'num']],
          data.rows.map(r => [html`<span class="mono tiny">${dStamp(r.t)}</span>`, html`<span class="oc">${LABEL[r.rule]}</span>`,
            `${ratio(r.ratio_before)} → ${ratio(r.ratio_after)}`,
            html`<span class="mono tiny">${legs(r.closed, 'closed')}${r.opened.length ? ' → ' : ''}${legs(r.opened, 'sold')}${r.note ? html`<br><span class="mute">${r.note}</span>` : ''}</span>`,
            html`<span class="${tone(r.closed[0] && r.closed[0].pnl)}">${money(r.closed[0] && r.closed[0].pnl)}</span>`,
            html`<span class="${tone(r.if_held)}">${money(r.if_held)}</span>`, r.costs != null ? inr(r.costs, { sign: false }) : html`<span class="mute">${r.new_leg_open ? 'open' : '—'}</span>`,
            html`<b class="${tone(r.roll_paid)}">${money(r.roll_paid)}</b>`]), { minw: 980 })}
        <p class="gk-note">${data.method} While the new leg is open, its P&L is marked to its live price and the comparison moves with the market.</p>
        <div class="gk-two even">
          <div class="gk-panel"><h3>Balance before → after each roll <span class="s">rolls should land in the shaded band</span></h3>${arrows(data.rows, g.engine())}</div>
          <div class="gk-panel"><h3>By rule <span class="s">which kind of roll earns its cost</span></h3>
            ${table([['Rule', 'minmax(0,1fr)'], ['Rolls', '60px', 'num'], ['Helped', '70px', 'num'], ['Net vs held', '110px', 'num']],
              [...['continuous', 'eod', 'rollout'].map(k => { const b = data.by_rule[k]; return [LABEL[k], b.rolls, b.scored ? `${b.helped} of ${b.scored}` : '—', html`<span class="${tone(b.net)}">${b.scored ? inr(b.net) : '—'}</span>`]; }),
               [html`<b>All rolls</b>`, html`<b>${s.rolls}</b>`, html`<b>${s.scored ? `${s.helped} of ${s.scored}` : '—'}</b>`, html`<b class="${tone(s.net)}">${s.net != null ? inr(s.net) : '—'}</b>`]], { minw: 0 })}
          </div>
        </div>`);
    }
    const stop = poll(() => api.geek(g.id, 'adjustments'), 60000, d => { data = d; paint(); });
    return stop;
  },
};
