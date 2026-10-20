// Days (SuperTrend options): each session, how many entries it used of
// the daily cap, the signals the cap turned away, flips while flat, and
// the day's P&L.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, dFull } from '../../core/format.js';
import { upDownBars } from '../../ui/charts.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, tone } from './ui.js';

const day = d => dFull(new Date(`${d}T12:00:00+05:30`));

export default {
  render(el, v, g) {
    let data = null, eng = g.engine();
    function paint() {
      if (data === null) { mount(el, html`${head('Days')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Days')}${empty('Couldn’t load the days', data.message)}`); return; }
      const cap = eng && eng.supertrend ? eng.supertrend.max_trades : null;
      const days = data.days, traded = days.filter(d => d.trades);
      const old = days.slice().reverse().filter(d => d.trades);
      mount(el, html`
        ${head('Days', 'Each session: the entries it used, signals the daily cap turned away, SuperTrend flips while flat, and the day’s P&L.', `${traded.length} days traded`)}
        ${days.length ? html`
        ${tiles([
          ['Days traded', String(traded.length), `${days.length - traded.length} more with checks only`],
          ['Entries a day', traded.length ? (traded.reduce((a, d) => a + d.trades, 0) / traded.length).toFixed(1) : '—', cap ? `cap ${cap}` : 'no cap'],
          ['Cap reached', String(data.summary.cap_days), 'days a signal was turned away'],
          ['Green days', traded.length ? `${traded.filter(d => d.pnl > 0).length} / ${traded.length}` : '—', 'closed P&L'],
          ['Best day', traded.length ? inr(Math.max(...traded.map(d => d.pnl))) : '—', '', 'pos'],
          ['Worst day', traded.length ? inr(Math.min(...traded.map(d => d.pnl))) : '—', '', 'neg'],
        ], 6)}
        ${old.length > 1 ? html`<div class="gk-panel"><h3>Day by day <span class="s">closed P&amp;L, oldest first</span></h3>${upDownBars(old.map(d => d.pnl), old.map(d => `${day(d.day)}  ${inr(d.pnl)}  ·  ${d.trades} trade${d.trades === 1 ? '' : 's'}`), 120)}</div>` : ''}
        <div class="gk-panel"><h3>Every session <span class="s">newest first</span></h3>
          ${table([['Day', 'minmax(140px,1fr)'], ['Entries', '150px'], ['Won', '60px', 'num'], ['Cap turned away', '120px', 'num'], ['Flips while flat', '120px', 'num'], ['Other blocks', '100px', 'num'], ['P&L', '100px', 'num']],
            days.map(d => [day(d.day),
              html`<span class="dots" title="${d.trades} entr${d.trades === 1 ? 'y' : 'ies'}${cap ? ` of ${cap}` : ''}">${Array.from({ length: Math.max(cap || 0, d.trades) }, (_, i) => html`<i class="${i < d.trades ? 'on' : ''}" style="display:inline-block;width:12px;height:12px;margin-right:4px;border:1px solid var(--ink);background:${i < d.trades ? 'var(--ink)' : 'transparent'}"></i>`)}</span>`,
              d.trades ? `${d.wins}` : '—', d.cap_hits ? html`<b class="neg">${d.cap_hits}</b>` : '0', String(d.flips_flat), String(d.blocked),
              html`<span class="${tone(d.pnl)}">${d.trades ? inr(d.pnl) : '—'}</span>`]), { minw: 820, rowCls: i => (days[i].cap_hits ? 'att' : '') })}
          <p class="gk-note">Turned away, flips and blocks come from the decision log, kept from the day this version was deployed.</p></div>`
        : empty('No sessions yet', 'Days appear after its first trade or check.')}`);
    }
    const off = g.onEngine(e => { const had = !!(eng && eng.supertrend); eng = e; if (!had && e && e.supertrend) paint(); });
    const stop = poll(() => api.geek(g.id, 'signals'), 60000, d => { data = d; paint(); });
    return () => { off(); stop(); };
  },
};
