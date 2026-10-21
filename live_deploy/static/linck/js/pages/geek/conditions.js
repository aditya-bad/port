// Conditions (every strategy): the market each trade was opened into —
// today's gap, the range and move so far, the time, the days to expiry —
// how far it went against and for it on the way (MAE / MFE, from the
// 5-minute marks), and its result; then the results added up by each
// condition, to see which markets pay and which don't.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, dStamp } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, tone, tipAt } from './ui.js';
import { mins } from './st.js';

const pct = v => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`);
// How far out of the money (− in it), % of the index, in words.
export const moneyness = v => (Math.abs(v) <= 0.25 ? `at the money (${pct(v)})` : `${Math.abs(v).toFixed(2)}% ${v > 0 ? 'out of' : 'in'} the money`);

export function conditionTip(r) {
  const c = r.context || {};
  return [`${r.label} · ${dStamp(r.t)}`, `Result  ${r.pnl == null ? 'open' : inr(r.pnl)}`,
    ...(r.path === false ? ['No prices recorded on the way'] : [`Worst on the way (MAE)  ${r.mae == null ? '—' : inr(r.mae)}`, `Best on the way (MFE)  ${r.mfe == null ? '—' : inr(r.mfe)}`]),
    ...(r.held_min != null ? [`Held  ${mins(r.held_min)}`] : []),
    ...(c.gap_pct != null ? [`Gap  ${pct(c.gap_pct)}`] : []),
    ...(c.from_open_pct != null ? [`Index since the open  ${pct(c.from_open_pct)}${c.partial ? ' (since a restart)' : ''}`] : []),
    ...(c.dte != null ? [`Days to expiry  ${c.dte}`] : []), ...(r.otm_pct != null ? [`Strike  ${moneyness(r.otm_pct)}`] : []),
    ...(r.atr != null ? [`ATR  ${r.atr}`] : []), ...(r.candle_pts != null ? [`Signal candle  ${r.candle_pts} points`] : []),
    r.symbols.join(', ')].join('\n');
}

// Each closed trade: how far it went one way (x) against what it ended at (y).
export function excursionChart(rows, key, { diagonal = false } = {}) {
  const pts = rows.filter(r => r.pnl != null && r[key] != null && r.path !== false);
  if (!pts.length) return '';
  const W = 560, H = 300, L = 70, R = 12, T = 12, B = 30;
  const xs = pts.map(r => r[key]), ys = pts.map(r => r.pnl);
  const x0 = Math.min(0, ...xs), x1 = Math.max(0, ...xs), y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const sx = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), sy = v => T + (y1 - v) / ((y1 - y0) || 1) * (H - T - B);
  let s = `<line x1="${L}" x2="${W - R}" y1="${sy(0)}" y2="${sy(0)}" stroke="var(--line2)"/><line x1="${sx(0)}" x2="${sx(0)}" y1="${T}" y2="${H - B}" stroke="var(--line2)"/>`;
  if (diagonal) {   // on the line: it ended at its best
    const a = Math.max(x0, y0), b = Math.min(x1, y1);
    if (b > a) s += `<line x1="${sx(a)}" y1="${sy(a)}" x2="${sx(b)}" y2="${sy(b)}" stroke="var(--g)" stroke-dasharray="4 4"/>`;
  }
  s += `<text x="${L - 6}" y="${sy(y1) + 4}" text-anchor="end">${inr(Math.round(y1))}</text><text x="${L - 6}" y="${sy(y0)}" text-anchor="end">${inr(Math.round(y0))}</text>`;
  s += `<text x="${sx(x0)}" y="${H - 8}">${inr(Math.round(x0))}</text><text x="${sx(x1)}" y="${H - 8}" text-anchor="end">${inr(Math.round(x1))}</text>`;
  for (const r of pts) {
    s += `<circle cx="${sx(r[key]).toFixed(1)}" cy="${sy(r.pnl).toFixed(1)}" r="5" fill="${r.pnl >= 0 ? 'var(--g)' : 'var(--r)'}" fill-opacity=".75"${tipAt(conditionTip(r))}/>`;
  }
  return svg(W, H, s, key === 'mae' ? 'Worst point on the way against the result' : 'Best point on the way against the result');
}

// One condition's buckets: the average trade in each, as bars either side of zero.
export function bucketBars(g) {
  const mx = Math.max(1, ...g.buckets.map(b => Math.abs(b.avg)));
  return html`<div class="col" style="gap:6px">${g.buckets.map(b => html`
    <div class="cd-row" data-tip="${`${g.label}: ${b.label}\nTrades  ${b.n}\nWon  ${b.wins} (${Math.round(b.win_rate)}%)\nP&L  ${inr(b.pnl)}\nAverage trade  ${inr(b.avg)}`}">
      <b>${b.label}</b><span class="mono tiny mute">${b.n} · ${Math.round(b.win_rate)}%</span>
      <span class="bar"><i style="${b.avg >= 0 ? 'left:50%' : 'right:50%'};width:${(Math.abs(b.avg) / mx * 50).toFixed(1)}%;background:${b.avg >= 0 ? 'var(--g)' : 'var(--r)'}"></i><u></u></span>
      <span class="mono tiny right ${tone(b.avg)}">${inr(b.avg)}</span></div>`)}</div>`;
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Conditions')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Conditions')}${empty('Couldn’t load the conditions', data.message)}`); return; }
      const s = data.summary, rows = data.rows, closed = rows.filter(r => r.pnl != null);
      if (!rows.length) {
        mount(el, html`${head('Conditions')}${empty('No trades yet', 'Each trade appears here with the market it was opened into.')}`);
        return;
      }
      mount(el, html`
        ${head('Conditions', 'The market each trade was opened into, how far it went against and for it on the way, and which conditions paid.', `${s.trades} trade${s.trades === 1 ? '' : 's'}`)}
        ${tiles([
          ['Trades', String(s.trades), `${s.closed} closed${s.open ? ` · ${s.open} open` : ''}`],
          ['Won', s.closed ? `${Math.round(s.wins / s.closed * 100)}%` : '—', `${s.wins} of ${s.closed}`],
          ['Worst on the way', s.avg_mae == null ? '—' : inr(s.avg_mae), `average MAE · ${s.with_path} trades`, 'neg'],
          ['Best on the way', s.avg_mfe == null ? '—' : inr(s.avg_mfe), 'average MFE', 'pos'],
          ['Gave back', s.gave_back == null ? '—' : inr(s.gave_back), 'winners: best point less the result'],
          ['Losers once up', String(s.losers_were_up), 'losing trades that were in profit first'],
        ], 6)}
        ${s.with_path < s.closed ? html`<p class="gk-note">${s.closed - s.with_path} closed trade${s.closed - s.with_path === 1 ? '' : 's'} have no prices recorded between entry and exit (no 5-minute mark or stored candle), so they sit out the MAE and MFE.</p>` : ''}
        ${rows.some(r => r.context.partial) ? html`<p class="gk-note">${rows.filter(r => r.context.partial).length} trade${rows.filter(r => r.context.partial).length === 1 ? ' was' : 's were'} opened on a day the app restarted mid-session without the exchange’s day figures, so ${rows.filter(r => r.context.partial).length === 1 ? 'it sits' : 'they sit'} out the range and move-since-open buckets.</p>` : ''}
        ${s.with_context < s.trades ? html`<p class="gk-note">${s.trades - s.with_context} older trade${s.trades - s.with_context === 1 ? '' : 's'} have no market recorded: the gap, range and move are recorded on every fill from now on. Time of day, weekday and days to expiry are known for all.</p>` : ''}
        ${closed.length ? html`<div class="gk-two even">
          <div class="gk-panel"><h3>Worst point against the result <span class="s">MAE → P&amp;L · hover a dot</span></h3>${excursionChart(rows, 'mae') || empty('', 'Needs the 5-minute marks.')}
            <p class="gk-note">Winners that went deep against first: a wider stop held; losers that never went far against: the stop was tight.</p></div>
          <div class="gk-panel"><h3>Best point against the result <span class="s">MFE → P&amp;L · dashed: ended at its best</span></h3>${excursionChart(rows, 'mfe', { diagonal: true }) || empty('', 'Needs the 5-minute marks.')}
            <p class="gk-note">Far below the dashed line: profit given back. Losers on the right: a target would have caught them.</p></div>
        </div>
        <div class="cd-grid">${data.groups.map(gr => html`<div class="gk-panel"><h3>${gr.label} <span class="s">average trade · ${gr.covered} of ${closed.length}</span></h3>${bucketBars(gr)}</div>`)}</div>` : ''}
        <div class="gk-panel"><h3>Every trade <span class="s">newest first</span></h3>
          ${table([['Opened', '120px'], ['Rule', 'minmax(120px,1fr)'], ['Legs', '110px'], ['Gap', '70px', 'num'], ['Since open', '80px', 'num'], ['Expiry', '60px', 'num'], ['Held', '70px', 'num'], ['MAE', '90px', 'num'], ['MFE', '90px', 'num'], ['P&L', '90px', 'num']],
            rows.map(r => [html`<span class="mono tiny">${dStamp(r.t)}</span>`, html`<b>${r.label}</b>${r.adds ? html` <span class="mono tiny mute">+${r.adds}</span>` : ''}`,
              html`<span class="small">${r.opt}${r.n_legs > 1 ? ` · ${r.n_legs}` : ''}</span>`, pct(r.context.gap_pct), pct(r.context.from_open_pct),
              r.context.dte == null ? '—' : `${r.context.dte}d`, r.held_min == null ? 'open' : mins(r.held_min),
              html`<span class="neg">${r.mae == null || r.path === false ? '—' : inr(r.mae)}</span>`, html`<span class="pos">${r.mfe == null || r.path === false ? '—' : inr(r.mfe)}</span>`,
              html`<span class="${tone(r.pnl)}">${r.pnl == null ? 'open' : inr(r.pnl)}</span>`]),
            { minw: 960, rowAttrs: i => html`data-tip="${conditionTip(rows[i])}"` })}</div>`);
    }
    return poll(() => api.geek(g.id, 'conditions'), 60000, d => { data = d; paint(); });
  },
};
