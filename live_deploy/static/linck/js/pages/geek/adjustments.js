// Adjustments: did rolling pay? Each roll against keeping the leg it
// closed (priced from the stored 1-minute candles), the balance before
// and after, and which kind of roll earns its cost. Charts: the running
// total of what rolling earned, how lopsided things were when it rolled
// against what the roll paid, and rolls by side, direction and hour. A row
// opens the cycle it belongs to.

import { api } from '../../core/api.js';
import { html, mount, on } from '../../core/dom.js';
import { go } from '../../core/router.js';
import { inr, dStamp, ist } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, path, money, tone, ratio, LABEL, yLabels } from './ui.js';

const legs = (xs, verb) => xs.map(f => `${verb} ${f.opt} ${f.strike ?? ''} @${Number(f.price).toFixed(1)}`).join(' · ');

export function arrows(rows, eng) {
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

const RULE_C = { continuous: 'var(--ink)', eod: 'var(--b)', rollout: 'var(--c4)' };

// What rolling has earned so far, roll by roll (scored rolls only).
export function cumulative(rows) {
  const R = rows.filter(r => r.roll_paid != null).reverse();
  if (!R.length) return empty('', 'Appears once a roll can be scored.');
  const W = 520, H = 200;
  let run = 0;
  const pts = R.map(r => (run += r.roll_paid));
  const hi = Math.max(0, ...pts), lo = Math.min(0, ...pts), pad = (hi - lo || 1) * 0.08;
  const y = v => 10 + (hi + pad - v) / (hi - lo + 2 * pad) * (H - 40), x = i => 46 + (R.length === 1 ? 0.5 : i / (R.length - 1)) * (W - 60);
  let s = `<line x1="46" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  s += yLabels([0, lo, hi], y, 40);
  s += `<path d="${path(pts.map((v, i) => [x(i), y(v)]))}" fill="none" stroke="var(--ink)" stroke-width="1.8"/>`;
  R.forEach((r, i) => { s += `<circle cx="${x(i)}" cy="${y(pts[i])}" r="3.5" fill="${RULE_C[r.rule]}"><title>${dStamp(r.t)} · ${LABEL[r.rule]} · ${inr(r.roll_paid)}</title></circle>`; });
  const L = pts[pts.length - 1];
  s += `<text class="lab" x="${x(R.length - 1)}" y="${y(L) - 8}" text-anchor="end" style="fill:${L >= 0 ? 'var(--g)' : 'var(--r)'}">${inr(L)}</text>`;
  s += `<text x="46" y="${H - 8}">${dStamp(R[0].t)}</text><text x="${W - 10}" y="${H - 8}" text-anchor="end">${dStamp(R[R.length - 1].t)}</text>`;
  return svg(W, H, s, 'Running total of what rolls earned against keeping the old leg');
}

// How lopsided it was when it rolled (x) against what the roll paid (y).
export function scatter(rows, eng) {
  const R = rows.filter(r => r.roll_paid != null && r.ratio_before != null);
  if (!R.length) return empty('', 'Appears once a roll can be scored.');
  const W = 520, H = 200, eod = eng && eng.rules ? eng.rules.find(r => r.key === 'eod_gap_check') : null;
  const ys = R.map(r => r.roll_paid), hi = Math.max(0, ...ys), lo = Math.min(0, ...ys), pad = (hi - lo || 1) * 0.1;
  const x = v => 46 + (v - 0.3) / 0.7 * (W - 60), y = v => 10 + (hi + pad - v) / (hi - lo + 2 * pad) * (H - 40);
  let s = `<line x1="46" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  [0.3, 0.5, 0.7, 0.9, 1].forEach(v => { s += `<text x="${x(v)}" y="${H - 8}" text-anchor="middle">${Math.round(v * 100)}%</text>`; });
  s += `<line x1="${x(0.5)}" x2="${x(0.5)}" y1="10" y2="${H - 30}" stroke="var(--r)" stroke-dasharray="3 3"/><text class="lab" x="${x(0.5) + 4}" y="20" style="fill:var(--r);font-size:10px">roll now</text>`;
  const fl = eod && eod.band ? eod.band[0] : 0.8;
  s += `<line x1="${x(fl)}" x2="${x(fl)}" y1="10" y2="${H - 30}" stroke="var(--wr)" stroke-dasharray="3 3"/><text class="lab" x="${x(fl) + 4}" y="20" style="fill:var(--wr);font-size:10px">EOD floor</text>`;
  s += yLabels([0, lo, hi], y, 40);
  R.forEach(r => { s += `<circle cx="${x(Math.max(0.3, r.ratio_before))}" cy="${y(r.roll_paid)}" r="5" fill="${RULE_C[r.rule]}" opacity=".8"><title>${dStamp(r.t)} · balance ${ratio(r.ratio_before)} · ${inr(r.roll_paid)}</title></circle>`; });
  return svg(W, H, s, 'Balance when the roll fired against what it paid');
}

// Rolls by side and direction: in = towards the market, out = away.
export function bySide(rows) {
  const keys = [['CE', 'in'], ['CE', 'out'], ['PE', 'in'], ['PE', 'out']];
  const B = keys.map(([sd, d]) => { const L = rows.filter(r => r.side === sd && r.direction === d), S = L.filter(r => r.roll_paid != null);
    return { l: `${sd} ${d}`, n: L.length, net: S.reduce((a, r) => a + r.roll_paid, 0), scored: S.length }; });
  const mx = Math.max(1, ...B.map(b => b.n));
  return html`<div class="col" style="gap:9px">${B.map(b => html`
    <div style="display:grid;grid-template-columns:70px minmax(0,1fr) 140px;gap:10px;align-items:center" class="small">
      <b>${b.l}</b><span class="meter" style="height:14px"><i style="width:${b.n / mx * 100}%;background:var(--ink)"></i></span>
      <span class="mono tiny right">${b.n} roll${b.n === 1 ? '' : 's'} · <span class="${tone(b.net)}">${b.scored ? inr(b.net) : '—'}</span></span></div>`)}
    <p class="gk-note">In: the new strike is closer to the market (more premium); out: further away. The amount is what those rolls paid against keeping the old leg.</p></div>`;
}

// When in the day rolls happen (IST hour), split by rule.
export function byHour(rows) {
  if (!rows.length) return empty('', 'Appears after the first roll.');
  const H = 160, W = 520, hours = [9, 10, 11, 12, 13, 14, 15];
  const cnt = hours.map(h => Object.fromEntries(['continuous', 'eod', 'rollout'].map(k => [k, rows.filter(r => r.rule === k && ist(r.t).h === h).length])));
  const mx = Math.max(1, ...cnt.map(c => c.continuous + c.eod + c.rollout)), bw = (W - 60) / hours.length - 12;
  let s = '';
  hours.forEach((h, i) => {
    let base = H - 26;
    const X = 46 + i * (bw + 12);
    ['continuous', 'eod', 'rollout'].forEach(k => {
      const hgt = cnt[i][k] / mx * (H - 46);
      if (hgt) { s += `<rect x="${X}" y="${base - hgt}" width="${bw}" height="${hgt}" fill="${RULE_C[k]}"/>`; base -= hgt; }
    });
    const tot = cnt[i].continuous + cnt[i].eod + cnt[i].rollout;
    if (tot) s += `<text class="lab" x="${X + bw / 2}" y="${base - 4}" text-anchor="middle">${tot}</text>`;
    s += `<text x="${X + bw / 2}" y="${H - 8}" text-anchor="middle">${h}:00</text>`;
  });
  s += `<line x1="40" x2="${W - 10}" y1="${H - 26}" y2="${H - 26}" stroke="var(--ink)"/>`;
  return svg(W, H, s, 'Rolls by hour of day');
}

export const legend = () => html`<div class="row wrap tiny" style="gap:14px">${['continuous', 'eod', 'rollout'].map(k => html`<span class="row" style="gap:6px"><i style="width:10px;height:10px;border-radius:50%;background:${RULE_C[k]}"></i>${LABEL[k]}</span>`)}</div>`;

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
            html`<b class="${tone(r.roll_paid)}">${money(r.roll_paid)}</b>`]), { minw: 980, rowCls: () => 'click', rowAttrs: i => html`data-c="${data.rows[i].cycle_id ?? ''}" role="link" tabindex="0" title="Open cycle ${data.rows[i].cycle_id ?? ''}"` })}
        <p class="gk-note">${data.method} While the new leg is open, its P&L is marked to its live price and the comparison moves with the market. Click a roll to open its cycle.</p>
        <div class="gk-two even">
          <div class="gk-panel"><h3>What rolling has earned <span class="s">running total, roll by roll</span></h3>${cumulative(data.rows)}${legend()}</div>
          <div class="gk-panel"><h3>Balance when it rolled vs what it paid <span class="s">does rolling earlier or later pay?</span></h3>${scatter(data.rows, g.engine())}${legend()}</div>
        </div>
        <div class="gk-two even">
          <div class="gk-panel"><h3>By side and direction <span class="s">which way rolls went, and what they paid</span></h3>${bySide(data.rows)}</div>
          <div class="gk-panel"><h3>By hour of day <span class="s">IST, split by rule</span></h3>${byHour(data.rows)}${legend()}</div>
        </div>
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
    const open = (e, t) => { if (t.dataset.c) go(`d/${g.id}/geek/cycle?c=${t.dataset.c}`); };
    const offs = [on(el, 'click', '.tr[data-c]', open), on(el, 'keydown', '.tr[data-c]', (e, t) => { if (e.key === 'Enter') open(e, t); })];
    return () => { stop(); offs.forEach(f => f()); };
  },
};
