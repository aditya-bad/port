// Performance: the six numbers that matter, equity with drawdown beneath
// it, then the shared performance stack and how trades ended.

import { html, mount, actions } from '../../core/dom.js';
import { inr, inrS, pct, dFull, dMonthYear, plural, tone } from '../../core/format.js';
import { pnlOf } from '../../core/basis.js';
import { ui } from '../../core/store.js';
import * as book from '../../data/book.js';
import { tradeMetrics, dailySeries, analytics, ddSeries, verdictPf } from '../../data/metrics.js';
import { line } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { perfStack, insightsList } from '../../ui/perfstack.js';
import { empty } from '../../ui/components.js';

const st = ui('perf', { hm: {}, cal: {} });

export default {
  render(el, v0) {
    function paint() {
      const v = book.find(v0.id) || v0;
      if (!v.trades.length && !v.daily.length) {
        mount(el, html`<div class="pad-x tab-body">${empty('No closed trades yet', 'Performance appears once the first cycle closes.')}</div>`);
        return;
      }
      const s = st.get(), key = `dep:${v.id}`;
      const hm = s.hm[key] || 'pct', cal = s.cal[key] || 'last';
      const pnls = v.trades.map(pnlOf);
      const m = tradeMetrics(pnls);
      const days = dailySeries(v.daily, pnlOf, v.created);
      const cum = days.map(d => d.cum), dd = ddSeries(cum);
      const top = Math.max(1, ...cum), low = Math.min(0, ...cum), ddLow = Math.min(-1, ...dd);
      const maxDd = Math.min(0, ...dd);
      registerScrub('eq', f => {
        const i = Math.round(f * (days.length - 1)), q = days[i];
        return q ? { t: `${dFull(q.date)}\nEquity  ${inr(q.cum)}\nThat day  ${inr(q.day)}\nBelow peak  ${inr(dd[i])}\nReturn on capital  ${pct(q.cum / v.cap * 100)}`, ys: [1 - (q.cum - low) / ((top - low) || 1)] } : null;
      });
      registerScrub('dd', f => {
        const i = Math.round(f * (days.length - 1)), q = days[i];
        return q ? { t: `${dFull(q.date)}\nBelow peak  ${inr(dd[i])}\nOf capital  ${pct(dd[i] / v.cap * 100)}`, ys: [dd[i] / ddLow], cs: ['var(--r)'] } : null;
      });
      const a = analytics(days, v.trades.map(t => ({ date: new Date(t.closed_at), pnl: pnlOf(t) })), { cap: v.cap, pctMode: hm === 'pct', calRange: cal });
      const er = {};
      v.trades.forEach(t => { const r = t.exit_reason || 'unrecorded'; er[r] = er[r] || { n: 0, p: 0 }; er[r].n++; er[r].p += pnlOf(t); });
      const emax = Math.max(1, ...Object.values(er).map(q => Math.abs(q.p)));
      const stats = [
        ['Win rate', `${m.win.toFixed(1)}%`, plural(m.n, 'trade'), ''],
        ['Profit factor', m.pf >= 99 ? '—' : m.pf.toFixed(2), verdictPf(m.pf), ''],
        ['Average win', inr(m.aw), m.al ? `${(m.aw / -m.al).toFixed(1)}× average loss` : '', 'pos'],
        ['Average loss', inr(m.al), 'per losing trade', 'neg'],
        ['Max drawdown', inr(maxDd), `${pct(maxDd / v.cap * 100)} of capital`, 'neg'],
        ['Best / worst trade', inrS(m.best), `worst ${inr(m.worst)}`, ''],
      ];
      mount(el, html`
        <div class="pad-x tab-body stack" style="gap:30px">
          <div class="stats" style="grid-template-columns:var(--g-6)">${stats.map(([l, val, sub, c]) => html`
            <div class="stat top"><span class="l">${l}</span><span class="v ${c}">${val}</span><span class="s">${sub}</span></div>`)}</div>
          <div>
            <div class="sect"><h3 class="t-h3">Equity, since start</h3><span class="mute small">cumulative realized · peak ${inrS(top, { sign: false })}</span></div>
            ${line({ series: [{ values: cum, color: 'var(--ink)', fill: 'var(--gs)', fillTo: 0 }], h: 200, key: 'eq', lo: low, hi: top, zero: true })}
            ${line({ series: [{ values: dd, color: 'var(--r)', fill: 'var(--rs)', fillTo: 0, width: 1.5 }], h: 70, key: 'dd', lo: ddLow, hi: 0, rule: false })}
            <div class="axis-row"><span>${days.length ? dMonthYear(days[0].date) : ''}</span><span>drawdown from peak, low ${inr(ddLow)}</span><span>${dMonthYear(new Date())}</span></div>
          </div>
          ${perfStack(a, { key, hm, cal })}
          <div class="grid-two">
            <div>
              <div class="sect"><h3 class="t-h3">How trades ended</h3></div>
              ${Object.entries(er).sort((p, q) => q[1].p - p[1].p).map(([r, q]) => html`
                <div class="exit-row" data-tip="${r}\nTrades  ${q.n} (${Math.round(q.n / (v.trades.length || 1) * 100)}%)\nTotal  ${inr(q.p)}\nAverage  ${inr(q.p / q.n)}">
                  <div class="row"><b class="grow">${r} <span class="mono mute" style="font-size:11px;font-weight:400">${plural(q.n, 'trade')}</span></b><span class="mono ${tone(q.p)}">${inr(q.p)}</span></div>
                  <div class="meter"><i class="grow-x" style="width:${Math.abs(q.p) / emax * 100}%;background:${q.p >= 0 ? 'var(--pos)' : 'var(--r)'}"></i></div>
                </div>`)}
            </div>
            <div><div class="sect"><h3 class="t-h3">What stands out</h3></div>${insightsList(a.insights)}</div>
          </div>
        </div>`);
    }
    const off = actions(el, {
      hm: d => { const s = st.get(); st.set({ hm: { ...s.hm, [`dep:${v0.id}`]: d.v } }); paint(); },
      cal: d => { const s = st.get(); st.set({ cal: { ...s.cal, [`dep:${v0.id}`]: d.v } }); paint(); },
    });
    paint();
    return () => off();
  },
};
