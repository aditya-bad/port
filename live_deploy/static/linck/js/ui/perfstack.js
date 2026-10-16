// The performance stack shared by a deployment's Performance tab and
// Reports: risk and quality, monthly returns heatmap, trading calendar,
// trade distribution and rolling win rate. Same pieces, same rules.

import { html } from '../core/dom.js';
import { MONTHS, ist, inr } from '../core/format.js';
import { line, histogram, heatmap, calendar } from './charts.js';
import { registerScrub } from './tooltip.js';
import { seg } from './components.js';

// opts: { key, hm: 'pct'|'abs', cal: 'last'|year, title: 'Risk and quality' }
export function perfStack(a, { key, hm, cal, title = 'Risk and quality' }) {
  const rollKey = `roll:${key}`;
  const R = a.roll, rw = a.rollW;
  registerScrub(rollKey, f => {
    if (!R.length) return null;
    const i = Math.min(R.length - 1, Math.max(0, Math.round(f * (R.length - 1)))), j = i + rw - 1, td = a.rollTrades[j];
    const d = td ? ist(td.date) : null;
    return {
      t: `Trades ${j - rw + 2}–${j + 1}${d ? ` · to ${d.d} ${MONTHS[d.mo]} ${d.y}` : ''}\nWin rate  ${R[i].toFixed(0)}%\nWins  ${Math.round(R[i] * rw / 100)} of ${rw}\nThat trade  ${td ? inr(td.pnl) : '—'}`,
      ys: [1 - R[i] / 100],
    };
  });
  return html`
    <div>
      <div class="sect"><h3 class="t-h3">${title}</h3></div>
      <div class="stats" style="grid-template-columns:var(--g-5);gap:18px 20px">${a.keyNums.map(k => html`
        <div class="stat top2"><span class="l">${k.l}</span><span class="v">${k.v}</span><span class="s">${k.s}</span></div>`)}</div>
    </div>
    <div class="scroll-x">
      <div class="sect"><h3 class="t-h3">Monthly returns</h3><span class="mute small">${hm === 'pct' ? 'return on capital · year total · running total' : 'realized ₹ · year total · running total'}</span>
        <span class="grow"></span>${seg([['pct', '% of capital'], ['abs', '₹ absolute']], hm, 'hm')}</div>
      ${a.yRows.length ? heatmap(a) : html`<p class="mute">No closed trades yet.</p>`}
      <p class="small pretty" style="margin:10px 0 0">${a.hNote}</p>
    </div>
    <div class="scroll-x">
      <div class="sect"><h3 class="t-h3">Trading calendar</h3><span class="mute small">every weekday, shaded by that day’s realized P&L</span>
        <span class="grow"></span><div class="chips">${a.yOpts.map(o => html`<button type="button" class="chip ${cal === o ? 'on' : ''}" data-act="cal" data-v="${o}">${o === 'last' ? 'Last 12 months' : o}</button>`)}</div></div>
      ${calendar(a)}
    </div>
    <div class="grid-two">
      <div>
        <div class="sect"><h3 class="t-h3">Trade P&L distribution</h3><span class="mute small">${a.histSub}</span></div>
        ${histogram(a.hist)}
        <div class="axis-row"><span>${a.hx0}</span><span>size of each trade’s result</span><span>${a.hx1}</span></div>
      </div>
      <div>
        <div class="sect"><h3 class="t-h3">Rolling win rate</h3><span class="mute small">last ${rw} trades at each point · now ${a.rollNow}</span></div>
        ${R.length > 1 ? line({ series: [{ values: R, color: 'var(--ink)', fill: 'var(--gs)' }], h: 160, key: rollKey, lo: 0, hi: 100, refs: [50] })
          : html`<p class="mute" style="height:160px">Needs at least ${rw} closed trades.</p>`}
        <div class="axis-row"><span>oldest</span><span>dashed line = 50%</span><span>latest</span></div>
      </div>
    </div>`;
}
export const insightsList = ins => html`${ins.map(i => html`
  <div class="insight"><span class="dia sm" style="background:${i.c};margin-top:7px"></span><span class="pretty">${i.t}</span></div>`)}`;
