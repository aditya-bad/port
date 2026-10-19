// Premium & decay: what was sold, what was kept, where it leaked, and
// the open cycle's call and put side totals over time.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, hhmm } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, poll, svg, path, tone } from './ui.js';

function sides(series) {
  const W = 560, H = 200, n = series.length;
  const all = series.flatMap(p => [p.ce, p.pe]), lo = Math.min(...all) * 0.9, hi = Math.max(...all) * 1.05;
  const y = v => 10 + (hi - v) / ((hi - lo) || 1) * 160, x = i => 40 + (n === 1 ? 0.5 : i / (n - 1)) * (W - 50);
  let s = '';
  for (let k = 0; k <= 3; k++) { const v = lo + (hi - lo) * k / 3; s += `<line x1="40" x2="${W - 10}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="34" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`; }
  s += `<path d="${path(series.map((p, i) => [x(i), y(p.ce)]))}" fill="none" stroke="var(--c5)" stroke-width="1.8"/>`;
  s += `<path d="${path(series.map((p, i) => [x(i), y(p.pe)]))}" fill="none" stroke="var(--c4)" stroke-width="1.8"/>`;
  const L = series[n - 1];
  s += `<text class="lab" x="${x(n - 1) - 4}" y="${y(L.ce) - 6}" text-anchor="end" style="fill:var(--c5)">CE ${L.ce.toFixed(1)}</text>`;
  s += `<text class="lab" x="${x(n - 1) - 4}" y="${y(L.pe) + 14}" text-anchor="end" style="fill:var(--c4)">PE ${L.pe.toFixed(1)}</text>`;
  s += `<text x="40" y="${H - 6}">${new Date(series[0].t).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })} ${hhmm(series[0].t)}</text><text x="${W - 10}" y="${H - 6}" text-anchor="end">${hhmm(L.t)}</text>`;
  return svg(W, H, s, 'Call and put side premium through the open cycle');
}

function bars(items) {
  const mx = Math.max(1, ...items.map(([, v]) => Math.abs(v || 0)));
  return html`<div class="col" style="gap:10px">${items.map(([l, v]) => html`
    <div style="display:grid;grid-template-columns:150px minmax(0,1fr) 100px;gap:10px;align-items:center" class="small">
      <b>${l}</b>
      <div style="position:relative;height:16px;background:var(--soft)"><i style="position:absolute;top:0;bottom:0;${v >= 0 ? 'left:50%' : `right:50%`};width:${(Math.abs(v || 0) / mx * 50).toFixed(1)}%;background:${v >= 0 ? 'var(--g)' : 'var(--r)'}"></i><i style="position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--ink)"></i></div>
      <span class="mono tiny right ${tone(v)}">${v == null ? 'none' : inr(v)}</span></div>`)}</div>`;
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Premium & decay')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Premium & decay')}${empty('Couldn’t load premium', data.message)}`); return; }
      const d = data;
      const r = d.by_role, c = d.by_close;
      mount(el, html`
        ${head('Premium & decay', 'A strangle earns from time. How much of what was sold is being kept, and where does it leak?', 'fills · 5-minute price marks')}
        ${tiles([
          ['Premium sold', inr(d.premium_sold, { sign: false }), 'every short leg opened'],
          ['Kept, net', html`<span class="${tone(d.kept)}">${inr(d.kept)}</span>`, d.premium_sold ? `${(d.kept / d.premium_sold * 100).toFixed(1)}% of premium sold` : ''],
          ['Legs closed by rolls', html`<span class="${tone(c.roll)}">${inr(c.roll || 0)}</span>`, 'P&L of the legs rolls closed'],
          ['Charges + slippage', inr(-(d.charges + d.slippage)), `charges ${inr(d.charges, { sign: false })} · slippage ${inr(d.slippage, { sign: false })}`],
        ])}
        <div class="gk-two even">
          <div class="gk-panel"><h3>Open premium, cycle ${d.open_cycle ?? '—'} <span class="s">call side and put side, every 5 minutes</span></h3>
            ${d.series.length ? sides(d.series) : empty('', d.open_cycle ? 'Appears after the first 5-minute snapshot of this cycle.' : 'No open cycle.')}</div>
          <div class="gk-panel"><h3>Where the P&L came from <span class="s">closed legs, gross</span></h3>
            ${bars([['Original legs', r.original ?? null], ['Adjustment legs', r.adjustment ?? null], ['Hedges', r.hedge ?? null],
              ['Closed by rolls', c.roll ?? null], ['Closed at cycle end', c['cycle end'] ?? null], ['Charges', -d.charges], ['Slippage', -d.slippage]])}
            <p class="gk-note">“Original” and “adjustment” split the same closed legs by how they were opened; “closed by rolls” and “at cycle end” split them by how they were closed.</p>
          </div>
        </div>`);
    }
    const stop = poll(() => api.geek(g.id, 'premium'), 60000, d => { data = d; paint(); });
    return stop;
  },
};
