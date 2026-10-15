// Performance — one period at a time (day → all time), with the trend,
// equity, what drove it, and the all-time leaderboard of strategy types.
import { html, mount, $, on, money, num, tone, date, weekday } from '../util.js';
import { api, pick, pnlSuffix } from '../api.js';
import { toastError } from '../ui.js';
import { bars, line } from '../charts.js';
import { pnlText, kpi, empty, skeleton, winRate, pctText } from '../components.js';

const ui = { period: 'month', offset: 0 };
const PERIODS = [['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['year', 'Year'], ['all', 'All time']];
const TREND = { day: ['day', 30, 'Last 30 trading days'], week: ['week', 26, 'Last 26 weeks'], month: ['month', 24, 'Last 24 months'], year: ['year', 10, 'By year'], all: ['month', 36, 'By month'] };
const fmtTrendLabel = (period, t) => period === 'month' ? new Date(t).toLocaleDateString('en-IN', { month: 'short', year: '2-digit', timeZone: 'Asia/Kolkata' })
  : period === 'year' ? new Date(t).toLocaleDateString('en-IN', { year: 'numeric', timeZone: 'Asia/Kolkata' })
    : period === 'week' ? `w/c ${date(t)}` : weekday(t);

function breakdown(rows, nameKey, href) {
  if (!rows.length) return empty('Nothing closed in this period', '');
  const vals = rows.map(r => ({ r, v: pick(r, 'realized_pnl') || 0 })).sort((a, b) => b.v - a.v);
  const max = Math.max(1, ...vals.map(x => Math.abs(x.v)));
  return html`<div class="bar-list">${vals.map(({ r, v }) => html`<div class="bar-row">
    <span class="name" title="${r[nameKey]}">${href ? html`<a href="${href(r)}">${r[nameKey]}</a>` : r[nameKey]}</span>
    <div class="bar-track"><span class="zero"></span><i class="${v >= 0 ? 'pos' : 'neg'}" style="${v >= 0 ? 'left' : 'right'}:50%;width:${Math.abs(v) / max * 50}%"></i></div>
    <span class="nowrap">${pnlText(v)}</span></div>`)}</div>`;
}

async function paint(main, cleanups) {
  const [period, n] = TREND[ui.period];
  const [r, trend, equity, board] = await Promise.all([
    api.report(ui.period, ui.offset), api.portfolioDigest(period, n),
    api.equityCurve().catch(() => []), api.leaderboard().catch(() => []),
  ]);
  const realized = pick(r, 'realized_pnl');
  const prev = r[`prev_realized_pnl${pnlSuffix()}`] ?? r.prev_realized_pnl;
  const delta = prev == null ? null : realized - prev;
  const wr = winRate(r.wins, r.losses);
  const sfx = pnlSuffix();
  const boardRows = board.map(b => {
    const w = b[`wins${sfx}`] ?? b.wins, l = b[`losses${sfx}`] ?? b.losses;
    const gw = b[`gross_win${sfx}`] ?? b.gross_win, gl = b[`gross_loss${sfx}`] ?? b.gross_loss;
    return { b, pnl: pick(b, 'realized_pnl') || 0, wr: winRate(w, l), pf: gl < 0 ? gw / -gl : gw > 0 ? Infinity : null };
  }).sort((a, b) => b.pnl - a.pnl);
  mount(main, html`
    <div class="page-head">
      <div><h1>Performance</h1><div class="sub">Settled, realized results — open positions aren’t counted until they close</div></div>
      <div class="seg" role="group" aria-label="Period">${PERIODS.map(([k, l]) => html`<button data-period="${k}" class="${ui.period === k ? 'on' : ''}">${l}</button>`)}</div>
    </div>
    <div class="actions" style="margin-bottom:16px">
      ${ui.period !== 'all' ? html`<button class="btn sm" data-step="1" aria-label="Previous period">← Previous</button>` : ''}
      <h2 style="min-width:140px;text-align:center">${r.label}</h2>
      ${ui.period !== 'all' ? html`<button class="btn sm" data-step="-1" ${ui.offset === 0 ? 'disabled' : ''} aria-label="Next period">Next →</button>
        ${ui.offset ? html`<button class="btn ghost sm" data-step="latest">Latest</button>` : ''}` : ''}
    </div>
    <div class="grid kpis" style="margin-bottom:18px">
      ${kpi('Realized P&L', pnlText(realized), delta == null ? 'All-time total' : html`<span class="${tone(delta)}">${money(delta, { sign: true })}</span> vs previous`)}
      ${kpi('Positions closed', num(r.positions_closed, 0), `${r.wins} won · ${r.losses} lost`)}
      ${kpi('Win rate', pctText(wr), 'Per whole position')}
      ${kpi('Fills', num(r.fills, 0), 'Entries + exits')}
    </div>
    <section class="card" style="margin-bottom:18px">
      <div class="card-head"><div><h2>Trend</h2><div class="sub">${TREND[ui.period][2]} · realized P&amp;L</div></div></div>
      <div class="card-body"><div class="chart" id="trend"></div></div>
    </section>
    <div class="grid halves" style="margin-bottom:18px">
      <section class="card"><div class="card-head"><div><h2>By strategy type</h2><div class="sub">${r.label}</div></div></div>
        <div class="card-body">${breakdown(r.by_strategy || [], 'strategy_name')}</div></section>
      <section class="card"><div class="card-head"><div><h2>By deployment</h2><div class="sub">${r.label}</div></div></div>
        <div class="card-body">${breakdown(r.by_deployment || [], 'deployment_name', x => `#/s/${x.deployment_id}`)}</div></section>
    </div>
    <section class="card" style="margin-bottom:18px">
      <div class="card-head"><div><h2>Portfolio equity</h2><div class="sub">Combined end-of-day value of every deployment in reports</div></div></div>
      <div class="card-body"><div class="chart" id="equity"></div></div>
    </section>
    <section class="card">
      <div class="card-head"><div><h2>Strategy types, all time</h2><div class="sub">Ranked by realized P&amp;L</div></div></div>
      ${boardRows.length ? html`<div class="table-wrap"><table class="t"><thead><tr><th>#</th><th>Strategy type</th><th class="num">Deployments</th><th class="num">Realized</th><th class="num">Closed</th><th class="num">Win rate</th><th class="num">Profit factor</th></tr></thead>
        <tbody>${boardRows.map((x, i) => html`<tr><td class="muted">${i + 1}</td><td class="cell-title">${x.b.strategy_name}</td><td class="num">${x.b.deployments_count}</td><td class="num">${pnlText(x.pnl)}</td><td class="num">${x.b.positions_closed}</td><td class="num">${pctText(x.wr)}</td><td class="num">${x.pf == null ? '—' : x.pf === Infinity ? '∞' : x.pf.toFixed(2)}</td></tr>`)}</tbody></table></div>`
        : empty('No closed positions yet', '')}
    </section>`);
  cleanups.push(bars($('#trend', main), [...trend].reverse().map(d => ({
    t: d.period_start, value: pick(d, 'realized_pnl') || 0, label: fmtTrendLabel(period, d.period_start),
    detail: `<div class="tip-row"><span>Closed</span><b>${d.positions_closed}</b></div><div class="tip-row"><span>Win rate</span><b>${pctText(winRate(d.wins, d.losses))}</b></div>`,
  })), { height: 210 }));
  const pts = equity.map(e => ({ t: e.bucket_at, v: e.total_value }));
  cleanups.push(line($('#equity', main), pts, { baseline: pts.length ? pts[0].v : null, fill: 'sign', height: 210, label: 'Portfolio equity' }));
}

export default {
  title: 'Performance',
  async render(main) {
    mount(main, skeleton(3));
    const cleanups = [];
    const repaint = async () => {
      try { cleanups.splice(0).forEach(f => f()); await paint(main, cleanups); }
      catch (e) { toastError(e, 'Couldn’t load performance'); }
    };
    this._refresh = repaint;
    await paint(main, cleanups);
    const offs = [
      on(main, 'click', '[data-period]', (e, b) => { ui.period = b.dataset.period; ui.offset = 0; repaint(); }),
      on(main, 'click', '[data-step]', (e, b) => {
        const s = b.dataset.step;
        ui.offset = s === 'latest' ? 0 : Math.max(0, ui.offset + Number(s));
        repaint();
      }),
    ];
    return () => { cleanups.forEach(f => f()); offs.forEach(f => f()); this._refresh = null; };
  },
  onChanged() { if (this._refresh) this._refresh(); },
};
