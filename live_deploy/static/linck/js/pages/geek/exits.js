// Exits (SuperTrend options): what closed each trade, a SuperTrend flip
// or the square-off, how long it was held and how much of the premium
// it kept.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, svg, tone, wide, tipAt } from './ui.js';
import { exitLabel, mins } from './st.js';
import { tradeTable, tradeTip } from './signals.js';

// Each trade's premium kept (%), one dot per trade on a line per exit.
function keptStrip(closed) {
  const reasons = [...new Set(closed.map(t => t.exit_reason))];
  const ks = closed.map(t => t.kept_pct).filter(v => v != null);
  if (!ks.length) return '';
  const lo = Math.min(-20, ...ks), hi = Math.max(20, ...ks);
  const W = 1200, L = 150, R = 16, rowH = 34, H = reasons.length * rowH + 30;
  const x = v => L + (v - lo) / (hi - lo) * (W - L - R);
  let s = `<line x1="${x(0)}" x2="${x(0)}" y1="4" y2="${H - 22}" stroke="var(--ink)"/>`;
  s += `<text x="${x(lo)}" y="${H - 6}">${Math.round(lo)}%</text><text x="${x(0)}" y="${H - 6}" text-anchor="middle">0</text><text x="${x(hi)}" y="${H - 6}" text-anchor="end">${Math.round(hi)}%</text>`;
  reasons.forEach((r, i) => {
    const y = 16 + i * rowH;
    s += `<text class="lab" x="0" y="${y + 4}">${exitLabel(r)}</text><line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="var(--line)"/>`;
    for (const t of closed.filter(c => c.exit_reason === r && c.kept_pct != null)) {
      s += `<circle cx="${x(t.kept_pct).toFixed(1)}" cy="${y}" r="5" fill="${t.pnl >= 0 ? 'var(--g)' : 'var(--r)'}" fill-opacity=".75"${tipAt(`${tradeTip(t)}\nPremium kept  ${t.kept_pct}%`)}/>`;
    }
  });
  return wide(svg(W, H, s, 'Premium kept by each trade, by what closed it'));
}

function holdBars(hold) {
  const mx = Math.max(1, ...hold.map(h => h.n));
  return html`<div class="col" style="gap:8px">${hold.map(h => html`
    <div style="display:grid;grid-template-columns:80px minmax(0,1fr) 120px;gap:10px;align-items:center" class="small" data-tip="${`Held ${h.label}\nTrades  ${h.n}\nWon  ${h.wins}\nP&L  ${inr(h.pnl)}`}">
      <b>${h.label}</b>
      <div style="height:16px;background:var(--soft)"><i style="display:block;height:100%;width:${(h.n / mx * 100).toFixed(1)}%;background:var(--ink)"></i></div>
      <span class="mono tiny right">${h.n} · <span class="${tone(h.pnl)}">${inr(h.pnl)}</span></span></div>`)}</div>`;
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Exits')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Exits')}${empty('Couldn’t load the exits', data.message)}`); return; }
      const closed = data.trades.filter(t => !t.open), ex = data.by_exit;
      const keys = Object.keys(ex).sort((a, b) => ex[b].n - ex[a].n);
      mount(el, html`
        ${head('Exits', 'What closed each trade, a SuperTrend flip or the square-off, how long it was held and how much of the premium it kept.', `${closed.length} closed`)}
        ${closed.length ? html`
        ${tiles(keys.map(k => [ex[k].label, `${ex[k].n}`, `${ex[k].win_rate == null ? '—' : Math.round(ex[k].win_rate)}% won · ${inr(ex[k].pnl)} · held ${mins(ex[k].median_hold)}`, tone(ex[k].pnl)]).concat([
          ['Premium kept', data.summary.closed ? `${Math.round(closed.reduce((a, t) => a + (t.kept_pct || 0), 0) / closed.length)}%` : '—', 'average, entry to exit'],
        ]), keys.length + 1)}
        <div class="gk-two even">
          <div class="gk-panel"><h3>By what closed it</h3>
            ${table([['', 'minmax(110px,1fr)'], ['Trades', '56px', 'num'], ['Won', '52px', 'num'], ['P&L', '92px', 'num'], ['Held', '64px', 'num'], ['Kept', '56px', 'num']],
              keys.map(k => [ex[k].label, String(ex[k].n), ex[k].win_rate == null ? '—' : `${Math.round(ex[k].win_rate)}%`,
                html`<span class="${tone(ex[k].pnl)}">${inr(ex[k].pnl)}</span>`, mins(ex[k].median_hold), ex[k].median_kept == null ? '—' : `${ex[k].median_kept}%`]), { minw: 460 })}
            <p class="gk-note">Held and Kept are medians. Kept: the share of the premium sold that it didn’t pay back.</p></div>
          <div class="gk-panel"><h3>How long it held <span class="s">trades · P&amp;L in each</span></h3>${holdBars(data.hold)}</div>
        </div>
        <div class="gk-panel"><h3>Premium kept, trade by trade <span class="s">green won, red lost · hover a dot</span></h3>${keptStrip(closed)}</div>
        <div class="gk-panel"><h3>Every closed trade</h3>${tradeTable(closed, g)}</div>`
        : empty('No closed trades yet', 'Exits appear once a trade has been bought back.')}`);
    }
    return poll(() => api.geek(g.id, 'signals'), 60000, d => { data = d; paint(); });
  },
};
