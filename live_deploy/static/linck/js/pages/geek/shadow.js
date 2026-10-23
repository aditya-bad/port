// Live-ready: shadow mode's record for this deployment (app/shadow.py).
// Would Kite have taken each fill, and at what price: the "ready for
// live?" card, day by day what fills cost against paper and what Kite
// would have refused, and every checked fill.

import { api } from '../../core/api.js';
import { html, mount, actions } from '../../core/dom.js';
import { inr, price, dStamp, dShort } from '../../core/format.js';
import { ui } from '../../core/store.js';
import { empty, loading } from '../../ui/components.js';
import { readyCard, shadowChip } from '../../ui/shadow.js';
import { head, table, svg, hit, wide } from './ui.js';

const st = ui('geekShadow', { v: 'all' });
const F = [['all', 'All'], ['reject', 'Would be rejected'], ['worse', 'Worse than paper'], ['partial', 'Partly'], ['no_fill', 'Wouldn’t fill'], ['fill', 'Would fill'], ['unknown', 'Couldn’t check']];
const dayOf = t => new Date(t).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });

// Per IST day: rupees worse than paper (bars) and orders Kite would refuse (marks).
export function byDay(checks) {
  const m = new Map();
  for (const c of checks) {
    const d = dayOf(c.executed_at);
    const r = m.get(d) || { day: d, short: 0, rejects: 0, n: 0 };
    r.n += 1;
    r.short += Number(c.shortfall || 0);
    if (c.verdict === 'reject') r.rejects += 1;
    m.set(d, r);
  }
  return [...m.values()].sort((a, b) => (a.day < b.day ? -1 : 1));
}

function dayChart(days) {
  if (!days.length) return '';
  const W = 1200, H = 160, bw = W / days.length, mx = Math.max(1, ...days.map(d => d.short));
  const body = days.map((d, i) => {
    const h = d.short / mx * (H - 24), x = i * bw;
    const tip = `${dShort(`${d.day}T12:00:00+05:30`)}\nFills checked  ${d.n}\nWorse than paper  ${inr(d.short, { sign: false })}${d.rejects ? `\nKite would refuse  ${d.rejects}` : ''}`;
    return `<rect x="${(x + bw * 0.15).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${h.toFixed(1)}" fill="var(--ink)" opacity=".75"/>`
      + (d.rejects ? `<circle cx="${(x + bw / 2).toFixed(1)}" cy="10" r="6" fill="var(--wr)"/>` : '')
      + hit(x.toFixed(1), 0, bw.toFixed(1), H, tip);
  }).join('');
  return wide(svg(W, H, body, 'Worse than paper, day by day'));
}

export default {
  render(el, v) {
    let data = null;
    function paint() {
      const h = head('Live-ready', 'Would Kite have taken each fill, and at what price? Each paper fill is checked as the limit order live would send.', 'shadow checks');
      if (data === null) { mount(el, html`${h}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${h}${empty('Couldn’t load shadow checks', data.message)}`); return; }
      const s = st.get();
      const n = k => data.checks.filter(c => k === 'all' || c.verdict === k).length;
      const rows = data.checks.filter(c => s.v === 'all' || c.verdict === s.v);
      const days = byDay(data.checks);
      mount(el, html`${h}
        ${readyCard(data.card)}
        ${days.length ? html`<div class="gk-panel"><h3>Worse than paper, day by day</h3>
          <p class="small mute" style="margin:0 0 8px">Bars: rupees the fills would have cost against the paper price that day. A red mark: Kite would have refused an order.</p>
          ${dayChart(days)}</div>` : ''}
        <div class="chips">${F.filter(([k]) => k === 'all' || n(k)).map(([k, l]) => html`
          <button type="button" class="chip ${s.v === k ? 'on' : ''}" data-act="v" data-k="${k}">${l} <span class="n">${n(k)}</span></button>`)}</div>
        ${rows.length ? table([['When', '130px'], ['Order', 'minmax(0,1fr)'], ['Limit', '90px'], ['Est.', '90px'], ['Verdict', '220px']],
          rows.map(c => [html`<span class="mono tiny">${dStamp(c.executed_at)}</span>`,
            html`<span class="mono tiny">${c.action === 'sell' ? 'Sell' : 'Buy'} ${Number(c.qty)} ${c.symbol} @ ${price(c.paper_price)}</span>`,
            html`<span class="mono tiny right">${c.limit_price == null ? '—' : price(c.limit_price)}</span>`,
            html`<span class="mono tiny right">${c.est_price == null ? '—' : price(c.est_price)}</span>`,
            shadowChip(c)]), { minw: 720 })
          : empty('', data.checks.length ? 'Nothing with that verdict.' : 'No fills checked yet. Each fill is checked a few seconds after it happens.')}`);
    }
    const load = () => api.shadow(v.id, 500).then(r => { data = r; paint(); }).catch(e => { data = e; paint(); });
    const off = actions(el, { v: d => { st.set({ v: d.k }); paint(); } });
    paint();
    load();
    const iv = setInterval(load, 30000);
    return () => { off(); clearInterval(iv); };
  },
};
