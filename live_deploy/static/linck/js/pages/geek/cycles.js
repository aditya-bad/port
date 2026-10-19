// Cycles: one row per checkpoint cycle — how it was born, lived and ended,
// the worst dip and best peak it went through (from the 5-minute price
// marks), its P&L — and how the months compare with the monthly target.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, dShort } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, money, tone, LABEL } from './ui.js';

const endTag = r => html`<span class="oc ${r.ended_by === 'checkpoint_target' ? '' : r.ended_by === 'open' ? 'no_action' : 'blocked'}">${LABEL[r.ended_by] || r.ended_by.replace(/_/g, ' ')}</span>`;

function daysChart(rows) {
  const d = rows.filter(r => r.ended_by === 'checkpoint_target').map(r => r.days);
  if (!d.length) return empty('', 'Appears once a cycle reaches its checkpoint.');
  const W = 520, mx = Math.max(6, Math.ceil(Math.max(...d) + 1)), x = v => 30 + v / mx * (W - 50);
  let s = `<line x1="30" x2="${W - 20}" y1="120" y2="120" stroke="var(--ink)"/>`;
  for (let v = 0; v <= mx; v += Math.max(1, Math.round(mx / 6))) s += `<text x="${x(v)}" y="136" text-anchor="middle">${v}d</text>`;
  const stack = {};
  d.forEach(v => { const b = Math.round(v); stack[b] = (stack[b] || 0) + 1; s += `<rect x="${x(b) - 8}" y="${120 - stack[b] * 20}" width="16" height="16" fill="var(--ink)"/>`; });
  const med = [...d].sort((a, b) => a - b)[Math.floor(d.length / 2)];
  s += `<line x1="${x(med)}" x2="${x(med)}" y1="16" y2="120" stroke="var(--g)" stroke-dasharray="4 3"/><text class="lab" x="${x(med) + 6}" y="26" style="fill:var(--g)">median ${med}d</text>`;
  return svg(W, 144, s, 'Days each cycle took to reach its checkpoint');
}

function monthChart(m) {
  if (!m.months.length) return empty('', 'Appears after the first closed trade.');
  const W = 520, top = Math.max(m.target * 1.1, ...m.months.map(x => x.pnl_net)), low = Math.min(0, ...m.months.map(x => x.pnl_net));
  const y = v => 14 + (top - v) / ((top - low) || 1) * 106, bw = Math.min(90, (W - 60) / m.months.length - 16);
  let s = `<line x1="30" x2="${W - 10}" y1="${y(m.target)}" y2="${y(m.target)}" stroke="var(--g)" stroke-dasharray="5 3"/><text class="lab" x="${W - 10}" y="${y(m.target) - 5}" text-anchor="end" style="fill:var(--g)">target ${inr(m.target, { sign: false })}</text>`;
  s += `<line x1="30" x2="${W - 10}" y1="${y(0)}" y2="${y(0)}" stroke="var(--ink)"/>`;
  m.months.forEach((x, i) => {
    const cx = 50 + i * (bw + 16), v = x.pnl_net;
    s += `<rect x="${cx}" y="${Math.min(y(0), y(v))}" width="${bw}" height="${Math.abs(y(v) - y(0))}" fill="${v < 0 ? 'var(--r)' : 'var(--ink)'}"/>`;
    s += `<text class="lab" x="${cx + bw / 2}" y="${Math.min(y(0), y(v)) - 5}" text-anchor="middle">${inr(v)}</text>`;
    const [yy, mm] = x.month.split('-');
    s += `<text x="${cx + bw / 2}" y="138" text-anchor="middle">${new Date(+yy, +mm - 1, 1).toLocaleString('en-IN', { month: 'short' })} · ${Math.round(v / m.target * 100)}%</text>`;
  });
  return svg(W, 146, s, 'Net P&L per month against the monthly target');
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Cycles')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Cycles')}${empty('Couldn’t load cycles', data.message)}`); return; }
      const s = data.summary;
      if (!data.rows.length) { mount(el, html`${head('Cycles')}${empty('No cycles yet', 'A cycle starts at the first entry.')}`); return; }
      const ck = s.ended_by.checkpoint_target || 0;
      const others = Object.entries(s.ended_by).filter(([k]) => k !== 'checkpoint_target').map(([k, n]) => `${(LABEL[k] || k).toLowerCase()} ${n}`).join(' · ');
      mount(el, html`
        ${head('Cycles', 'How each cycle was born, lived and ended: is the checkpoint doing the work, or are expiries and stops?', 'fills · 5-minute price marks')}
        ${tiles([
          ['Cycles', s.cycles, `${s.closed} closed · ${s.open} open`],
          ['Ended by checkpoint', s.closed ? `${ck} of ${s.closed}` : '—', others || (s.closed ? 'every closed cycle' : '')],
          ['Median days to checkpoint', s.median_days_to_checkpoint ?? '—', s.fastest_checkpoint_days != null ? `fastest ${s.fastest_checkpoint_days} · slowest ${s.slowest_checkpoint_days}` : ''],
          ['Avg worst dip / best peak', s.avg_mae != null ? html`<span class="neg">${inr(s.avg_mae)}</span> / <span class="pos">${inr(s.avg_mfe)}</span>` : '—', s.worst_mae != null ? `worst dip ${inr(s.worst_mae)}` : 'from price marks'],
        ])}
        ${table([['#', '44px'], ['Opened → closed', 'minmax(150px,1.2fr)'], ['Expiry', '96px'], ['Days', '56px', 'num'], ['Ended by', '130px'],
          ['Rolls', '60px', 'num'], ['EOD · no-op', '90px', 'num'], ['Worst dip', '96px', 'num'], ['Best peak', '96px', 'num'], ['P&L', '110px', 'num']],
          data.rows.map(r => [html`<b class="mono">${r.cycle_id}</b>`, `${dShort(r.start)} → ${r.end ? dShort(r.end) : 'now'}`, r.contract_expiry || '—', r.days,
            endTag(r), r.rolls, `${r.rolls_by_kind.eod + r.rolls_by_kind.rollout} · ${r.eod_checks_no_action}`,
            html`<span class="neg">${money(r.mae)}</span>`, html`<span class="pos">${money(r.mfe)}</span>`,
            html`<span class="${tone(r.pnl)} b">${money(r.pnl)}</span>${r.status === 'open' ? html` <span class="tiny mute">open</span>` : ''}`]),
          { minw: 960, rowCls: i => (data.rows[i].ended_by === 'convergence_stop' ? 'att' : '') })}
        <p class="gk-note">P&L is gross (before charges) for closed cycles and the live cycle P&L for the open one. Worst dip and best peak come from the 5-minute price marks, so a spike between two marks isn’t seen.</p>
        <div class="gk-two even">
          <div class="gk-panel"><h3>Days to checkpoint <span class="s">each cycle that reached it</span></h3>${daysChart(data.rows)}</div>
          <div class="gk-panel"><h3>Monthly target <span class="s">net P&L per month · informational</span></h3>${monthChart(data.monthly)}</div>
        </div>`);
    }
    const stop = poll(() => api.geek(g.id, 'cycles'), 60000, d => { data = d; paint(); });
    return stop;
  },
};
