// Signals (SuperTrend options): every option sold, with what its entry
// saw (the level it broke, how far past it, how far from the SuperTrend
// line) and how it ended; the same by level, by side and by hour.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, dStamp } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, bars, tone } from './ui.js';
import { exitLabel, pts, mins, scatter, groupRow, GROUP_COLS } from './st.js';

const LEVELS = ['R1', 'R2', 'R3', 'S1', 'S2', 'S3'];
// One trade, for a tooltip.
export const tradeTip = t => `${t.symbol} · ${dStamp(t.entry_t)}\nBroke  ${t.level || '—'}\nPast the level  ${pts(t.past_level)} pts\nFrom the ST line  ${pts(t.from_st)} pts\nEnded by  ${exitLabel(t.exit_reason)}\nHeld  ${mins(t.hold_min)}\nP&L  ${t.pnl == null ? '—' : inr(t.pnl)}`;
const LEGEND = html`<b style="color:var(--c4)">● PE</b> <b style="color:var(--c5)">● CE</b>`;

export function tradeTable(rows, g) {
  return table([['Sold', '128px'], ['Option', 'minmax(190px,1.4fr)'], ['Broke', '110px'], ['Past it', '76px', 'num'], ['From ST', '80px', 'num'],
    ['Premium', '156px', 'num'], ['Ended by', '124px'], ['Held', '72px', 'num'], ['P&L', '96px', 'num']],
  rows.map(t => [html`<a href="${g.href('trade')}?p=${t.id}" class="mono tiny">${dStamp(t.entry_t)}</a>`,
    html`<b class="${t.opt === 'CE' ? 'leg-ce' : 'leg-pe'}">${t.symbol}</b>`,
    t.level ? `${t.level} ${Math.round(t.level_px).toLocaleString('en-IN')}` : '—', pts(t.past_level), pts(t.from_st),
    html`<span class="mono small">${price(t.entry_px)} → ${t.exit_px == null ? 'open' : price(t.exit_px)}</span>`,
    t.open ? html`<span class="oc acted">Open</span>` : exitLabel(t.exit_reason), mins(t.hold_min),
    html`<span class="${tone(t.pnl)}">${t.pnl == null ? '—' : inr(t.pnl)}</span>`]), { minw: 1000 });
}

export default {
  render(el, v, g) {
    let data = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Signals')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Signals')}${empty('Couldn’t load the signals', data.message)}`); return; }
      const s = data.summary, closed = data.trades.filter(t => !t.open);
      const side = data.by_side;
      mount(el, html`
        ${head('Signals', 'Every option it sold: the level the index broke, how far past it the candle closed, how far that was from the SuperTrend line, and how it ended.', `${s.trades} trades`)}
        ${s.trades ? html`
        ${tiles([
          ['Trades', String(s.trades), s.open ? `${s.open} open` : `over ${s.days_traded} day${s.days_traded === 1 ? '' : 's'}`],
          ['Won', s.win_rate == null ? '—' : `${Math.round(s.win_rate)}%`, `${s.wins} of ${s.closed}`],
          ['P&L', inr(s.pnl), s.avg == null ? '' : `avg ${inr(s.avg)}`, tone(s.pnl)],
          ['Best / worst', s.best == null ? '—' : inr(s.best), s.worst == null ? '' : `worst ${inr(s.worst)}`, tone(s.best)],
          ['Past the level', s.median_past_level == null ? '—' : `${pts(s.median_past_level)} pts`, 'median, at entry'],
          ['From the ST line', s.median_from_st == null ? '—' : `${pts(s.median_from_st)} pts`, 'median, at entry'],
          ['Held', mins(s.median_hold), 'median'],
        ], 7)}
        <div class="gk-two even">
          <div class="gk-panel"><h3>PE or CE <span class="s">a PE is sold when the trend is up, a CE when it’s down</span></h3>
            ${table(GROUP_COLS, ['PE', 'CE'].filter(k => side[k]).map(k => groupRow(k === 'PE' ? 'Sold PEs (trend up)' : 'Sold CEs (trend down)', side[k])), { minw: 0 })}
            <h3 style="margin-top:16px">By level broken <span class="s">R breaks sell PEs, S breaks sell CEs</span></h3>
            ${table(GROUP_COLS, LEVELS.filter(k => data.by_level[k]).map(k => groupRow(k, data.by_level[k])), { minw: 0 })}
          </div>
          <div class="gk-panel"><h3>P&amp;L by hour of entry <span class="s">all trades entered in that hour</span></h3>
            ${data.by_hour.length ? bars(data.by_hour.map(h => [`${String(h.hour).padStart(2, '0')}:00 · ${h.n} trade${h.n === 1 ? '' : 's'}`, h.pnl, `Entered ${String(h.hour).padStart(2, '0')}:00–${String(h.hour + 1).padStart(2, '0')}:00\nTrades  ${h.n}\nWon  ${h.wins} (${Math.round(h.win_rate)}%)\nP&L  ${inr(h.pnl)}\nAverage  ${inr(h.avg)}`])) : empty('', 'No closed trades yet.')}
          </div>
        </div>
        <div class="gk-two even">
          <div class="gk-panel"><h3>Did breaking further pay? <span class="s">points past the level at entry, against P&amp;L · ${LEGEND}</span></h3>
            ${closed.length ? scatter(closed.filter(t => t.past_level != null).map(t => ({ x: t.past_level, y: t.pnl, c: t.opt === 'CE' ? 'var(--c5)' : 'var(--c4)', tip: tradeTip(t) })), { xLabel: 'points past the level' }) : empty('', 'No closed trades yet.')}</div>
          <div class="gk-panel"><h3>Did room to the line pay? <span class="s">points from the SuperTrend line at entry, against P&amp;L · ${LEGEND}</span></h3>
            ${closed.length ? scatter(closed.filter(t => t.from_st != null).map(t => ({ x: t.from_st, y: t.pnl, c: t.opt === 'CE' ? 'var(--c5)' : 'var(--c4)', tip: tradeTip(t) })), { xLabel: 'points from the SuperTrend line' }) : empty('', 'No closed trades yet.')}</div>
        </div>
        <div class="gk-panel"><h3>Every trade <span class="s">newest first · open one end to end</span></h3>${tradeTable(data.trades, g)}</div>`
        : empty('No trades yet', 'Signals appear after its first entry.')}`);
    }
    return poll(() => api.geek(g.id, 'signals'), 60000, d => { data = d; paint(); });
  },
};
