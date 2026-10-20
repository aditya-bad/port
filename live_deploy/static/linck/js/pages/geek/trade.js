// One trade end to end (SuperTrend options, #/d/:id/geek/trade?p=<id>):
// what the entry saw, the index through the trade against the pivot
// levels it was taken on, the option's premium, how it ended, and every
// check the strategy noted meanwhile.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, dStamp, hhmm } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, path, tone, wide } from './ui.js';
import { exitLabel, pts, mins, lvl, spread } from './st.js';

const OUT = { acted: 'Acted', no_action: 'No action', blocked: 'Blocked' };

// Price over time with horizontal levels and the entry/exit marked.
// series: [[iso, value]]; levels: [{key, value, color, dash}].
export function priceChart(series, { levels = [], marks = [], label = '', fmt = v => Math.round(v).toLocaleString('en-IN') } = {}) {
  if (series.length < 2) return '';
  const W = 1200, H = 260, L = 70, R = 70, T = 14, B = 26;
  const ts = series.map(p => new Date(p[0]).getTime()), vs = series.map(p => p[1]);
  const lv = levels.map(l => l.value);
  const lo0 = Math.min(...vs), hi0 = Math.max(...vs), span = hi0 - lo0 || hi0 * 0.002 || 1;
  // levels close to the path are drawn; far ones would squash it flat
  const near = levels.filter(l => l.value >= lo0 - span * 0.6 && l.value <= hi0 + span * 0.6);
  const lo = Math.min(lo0, ...near.map(l => l.value)) - span * 0.06, hi = Math.max(hi0, ...near.map(l => l.value)) + span * 0.06;
  const t0 = ts[0], t1 = ts[ts.length - 1];
  const x = t => L + (t - t0) / ((t1 - t0) || 1) * (W - L - R), y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let s = '';
  for (const l of near) s += `<line x1="${L}" x2="${W - R}" y1="${y(l.value)}" y2="${y(l.value)}" stroke="${l.color}" stroke-width="${l.bold ? 2 : 1}" ${l.dash ? 'stroke-dasharray="6 4"' : ''}/>`;
  const ly = spread(near.map(l => y(l.value)), 13);
  near.forEach((l, i) => { s += `<text class="lab" x="${W - R + 6}" y="${ly[i] + 4}" style="fill:${l.color}">${l.key}</text>`; });
  s += `<text x="${L - 8}" y="${y(hi0) + 4}" text-anchor="end">${fmt(hi0)}</text><text x="${L - 8}" y="${y(lo0) + 4}" text-anchor="end">${fmt(lo0)}</text>`;
  for (const m of marks) {
    const mt = new Date(m.t).getTime();
    if (mt < t0 || mt > t1) continue;
    s += `<line x1="${x(mt)}" x2="${x(mt)}" y1="${T}" y2="${H - B}" stroke="${m.color}" stroke-width="2"/>`;
    s += `<text class="lab" x="${x(mt) + 5}" y="${T + 10}" style="fill:${m.color}">${m.label}</text>`;
  }
  s += `<path d="${path(series.map((p, i) => [x(ts[i]), y(p[1])]))}" fill="none" stroke="var(--ink)" stroke-width="1.8"/>`;
  s += `<text x="${L}" y="${H - 6}">${hhmm(series[0][0])} ${new Date(series[0][0]).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })}</text>`;
  s += `<text x="${W - R}" y="${H - 6}" text-anchor="end">${hhmm(series[series.length - 1][0])}</text>`;
  return wide(svg(W, H, s, label));
}

const saw = vals => (vals ? Object.entries(vals).filter(([, v]) => v != null && typeof v !== 'object')
  .map(([k, v]) => html`<span class="kv-i"><span class="mute">${k.replace(/_/g, ' ')}</span> <b class="mono">${typeof v === 'number' ? v.toLocaleString('en-IN') : String(v)}</b></span>`) : []);

export default {
  render(el, v, g) {
    let data = null;
    const p = g.params && g.params.p;
    function paint() {
      if (data === null) { mount(el, html`${head('Trade')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Trade')}${empty('Couldn’t load this trade', data.message)}`); return; }
      const t = data.trade;
      if (!t) {
        mount(el, html`${head('Trade')}${empty(data.n ? 'There’s no such trade' : 'No trades yet', data.n ? 'It may be from another deployment.' : 'A trade appears here after the first entry.')}
          ${data.n ? html`<p><a href="${g.href('signals')}">← All trades</a></p>` : ''}`);
        return;
      }
      const piv = t.pivots || {};
      const levels = Object.entries(piv).map(([k, val]) => ({ key: k, value: val, bold: k === t.level,
        color: k === t.level ? 'var(--acc)' : k === 'P' ? 'var(--mute)' : k[0] === 'R' ? 'var(--c5)' : 'var(--c4)' }));
      if (t.st_value != null) levels.push({ key: 'ST', value: t.st_value, color: 'var(--r)', dash: true });
      const marks = [{ t: t.entry_t, label: `Sold ${t.opt}`, color: 'var(--acc)' }];
      if (t.exit_t) marks.push({ t: t.exit_t, label: exitLabel(t.exit_reason), color: t.pnl >= 0 ? 'var(--g)' : 'var(--r)' });
      const ex = t.exit_values || {};
      mount(el, html`
        <div class="row wrap" style="gap:10px;align-items:baseline">
          <a href="${g.href('signals')}" class="small">← All trades</a><span class="grow"></span>
          ${data.prev ? html`<a class="btn btn-sm" href="${g.href('trade')}?p=${data.prev}">← Older</a>` : ''}
          <span class="mono small">${data.no} of ${data.n}</span>
          ${data.next ? html`<a class="btn btn-sm" href="${g.href('trade')}?p=${data.next}">Newer →</a>` : ''}
        </div>
        ${head(`Trade ${data.no}`, `${t.symbol}: sold ${dStamp(t.entry_t)}${t.exit_t ? `, bought back ${dStamp(t.exit_t)}` : ', still open'}.`, t.open ? 'open' : exitLabel(t.exit_reason))}
        ${tiles([
          ['Sold', t.opt === 'PE' ? 'PE · trend up' : 'CE · trend down', t.strike ? `strike ${t.strike.toLocaleString('en-IN')}${t.expiry ? ` · ${t.expiry}` : ''}` : ''],
          ['Broke', t.level ? `${t.level} ${lvl(t.level_px)}` : '—', t.past_level != null ? `close ${pts(t.past_level)} pts past it` : ''],
          ['From the ST line', t.from_st == null ? '—' : `${pts(t.from_st)} pts`, t.st_value != null ? `line ${lvl(t.st_value)}` : ''],
          ['Premium sold', price(t.entry_px), t.exit_px == null ? 'still open' : `bought back ${price(t.exit_px)} · kept ${t.kept_pct}%`],
          ['Held', mins(t.hold_min), t.price_source ? `exit price from ${t.price_source.replace(/_/g, ' ')}` : ''],
          ['P&L', t.pnl == null ? '—' : inr(t.pnl), t.qty ? `${t.qty} qty` : '', tone(t.pnl)],
        ], 6)}
        <div class="gk-panel"><h3>The index through the trade <span class="s">against the levels it was taken on · dashed: the SuperTrend line at entry</span></h3>
          ${data.path.index.length > 1 ? priceChart(data.path.index, { levels, marks, label: 'The index through the trade' }) : empty('', 'No index prices stored for this stretch.')}</div>
        <div class="gk-panel"><h3>The option’s premium <span class="s">what it was sold at, and what buying it back would have cost</span></h3>
          ${data.path.option.length > 1 ? priceChart(data.path.option, { levels: [{ key: 'sold', value: t.entry_px, color: 'var(--acc)', dash: true }], marks, label: 'The option premium through the trade', fmt: v => price(v) }) : empty('', 'No premium prices stored for this option.')}</div>
        <div class="gk-two even">
          <div class="gk-panel"><h3>What the entry saw</h3>
            <p class="small" style="display:flex;flex-wrap:wrap;gap:6px 16px">${saw({ close: t.close, level: t.level, level_price: t.level_px, supertrend: t.st_value, pivot: t.pivot_point })}</p>
            ${t.switched_to_next_week ? html`<p class="gk-note">The contract expired that day, so next week’s was sold instead.</p>` : ''}</div>
          <div class="gk-panel"><h3>${t.open ? 'Still open' : `What ended it: ${exitLabel(t.exit_reason)}`}</h3>
            ${t.open ? html`<p class="small">Bought back on a SuperTrend flip or at the square-off.</p>` : html`<p class="small" style="display:flex;flex-wrap:wrap;gap:6px 16px">${saw(ex)}</p>`}</div>
        </div>
        <div class="gk-panel"><h3>Checks meanwhile <span class="s">what the strategy noted from 45 minutes before the entry</span></h3>
          ${data.decisions.length ? table([['When', '120px'], ['Rule', '150px'], ['Outcome', '110px'], ['What it saw', 'minmax(0,1fr)']],
            data.decisions.map(d => [html`<span class="mono tiny">${dStamp(d.t)}</span>`, html`<b>${d.label}</b>`, html`<span class="oc ${d.outcome}">${OUT[d.outcome] || d.outcome}</span>`,
              html`<span class="col" style="gap:2px"><span>${d.summary}</span>${d.why ? html`<span class="mono tiny mute">${d.why}</span>` : ''}</span>`]), { minw: 640 })
            : empty('', 'Nothing noted around this trade.')}</div>`);
    }
    return poll(() => api.geek(g.id, `trade${p ? `?p=${encodeURIComponent(p)}` : ''}`), 60000, d => { data = d; paint(); });
  },
};
