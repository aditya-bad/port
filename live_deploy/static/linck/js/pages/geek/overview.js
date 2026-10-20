// Overview: the normal page's figures plus where the engine stands —
// next move, headline numbers, equity with each cycle shaded, the open
// legs live, the latest decisions and daily P&L.

import { api } from '../../core/api.js';
import { html, mount, $, setText, setTone } from '../../core/dom.js';
import { inr, pct, dFull, dStamp, tone as toneOf } from '../../core/format.js';
import { pnlOf } from '../../core/basis.js';
import { listen } from '../../core/store.js';
import * as book from '../../data/book.js';
import { periodMetrics, dailySeries, ddSeries, fmtRatio, verdictRdd } from '../../data/metrics.js';
import { line, upDownBars } from '../../ui/charts.js';
import { legHead, legRow, liveTag, tickLegs } from '../../ui/legs.js';
import { empty } from '../../ui/components.js';
import { head, nextMove, tiles, table, poll, LABEL } from './ui.js';
import { hasView } from './views.js';

const OUT = { acted: 'Acted', no_action: 'No action', blocked: 'Blocked' };

function equity(v, cycles) {
  const days = dailySeries(v.daily, pnlOf, v.created);
  if (days.length < 2) return empty('', 'The equity curve appears after the first closed trade.');
  const cum = days.map(d => d.cum);
  const t0 = days[0].date.getTime(), t1 = days[days.length - 1].date.getTime() + 864e5;
  const fx = t => Math.max(0, Math.min(1, (new Date(t).getTime() - t0) / ((t1 - t0) || 1)));
  const bands = (cycles ? cycles.rows : []).slice().reverse().map((c, i) => ({ c, a: fx(c.start), b: c.end ? fx(c.end) : 1, odd: i % 2 }));
  return html`<div style="position:relative">
    ${bands.map(x => html`<div title="Cycle ${x.c.cycle_id} · ${LABEL[x.c.ended_by] || x.c.ended_by}" style="position:absolute;top:0;bottom:0;left:${(x.a * 100).toFixed(2)}%;width:${((x.b - x.a) * 100).toFixed(2)}%;background:${x.odd ? 'var(--soft)' : 'transparent'};border-left:1px solid var(--line)"></div>`)}
    <div style="position:relative">${line({ series: [{ values: cum, color: 'var(--ink)', fill: 'var(--gs)', fillNeg: 'var(--rs)', fillTo: 0 }], h: 190, lo: Math.min(0, ...cum), hi: Math.max(1, ...cum), zero: true })}</div>
    <div style="position:relative;height:18px">${bands.map(x => html`<span class="mono tiny" style="position:absolute;left:${(((x.a + x.b) / 2) * 100).toFixed(2)}%;transform:translateX(-50%);color:${x.c.ended_by === 'convergence_stop' ? 'var(--r)' : x.c.ended_by === 'contract_expiry_backstop' ? 'var(--b)' : 'var(--mute)'}">${x.c.cycle_id}</span>`)}</div>
  </div>`;
}

export default {
  render(el, v0, g) {
    let cycles = null, adj = null, dec = null;
    const own = hasView(v0.strategy, 'cycles');
    function paint() {
      const v = book.find(v0.id) || v0;
      const x = book.live(v);
      const m = periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf });
      const cum = dailySeries(v.daily, pnlOf, v.created).map(d => d.cum);
      const maxDd = Math.min(0, ...ddSeries(cum));
      const s = cycles && cycles.summary;
      const daily = dailySeries(v.daily, pnlOf, v.created).filter(d => d.has).slice(-60);
      mount(el, html`
        ${head('Overview', 'Everything from the normal page, plus where the engine stands.')}
        ${g.engine() ? nextMove(g.engine(), g.href('engine')) : html`<div class="skel" style="height:52px"></div>`}
        ${tiles([
          ['Today', html`<span data-ov="today">${v.rawStatus === 'stopped' ? '—' : inr(x.today)}</span>`, 'live', toneOf(x.today)],
          ['Overall', html`<span data-ov="overall">${inr(x.overall)}</span>`, `${v.trades.length} trades`, toneOf(x.overall)],
          ['Unrealized', html`<span data-ov="unreal">${inr(x.unreal)}</span>`, `${v.legs.length} legs`, toneOf(x.unreal)],
          ['Return', pct(x.ret), `on ${inr(v.cap, { sign: false })}`, toneOf(x.ret)],
          ['Return / drawdown', m.ddp < 0 ? fmtRatio(m.rdd) : '—', m.ddp < 0 ? verdictRdd(m.rdd) : 'no drawdown yet'],
          ['Max drawdown', inr(maxDd), `${pct(maxDd / v.cap * 100)} of capital`, maxDd < 0 ? 'neg' : ''],
          ...(own ? [
            ['Checkpoints', s ? `${s.ended_by.checkpoint_target || 0} / ${s.closed}` : '—', s && s.median_days_to_checkpoint != null ? `median ${s.median_days_to_checkpoint}d` : 'cycles closed'],
            ['Rolls paid', adj && adj.summary.net != null ? inr(adj.summary.net) : '—', adj ? (adj.summary.scored ? `${adj.summary.helped} of ${adj.summary.scored} helped` : `${adj.summary.rolls} rolls`) : '', adj && adj.summary.net != null ? toneOf(adj.summary.net) : ''],
          ] : [
            ['Win rate', m.n ? `${Math.round(m.win)}%` : '—', m.n ? `avg win ${inr(m.aw)} · loss ${inr(m.al)}` : 'no closed trades'],
            ['Checks noted', dec ? String(dec.counts.no_action + dec.counts.blocked) : '—', dec ? `${dec.counts.blocked} blocked · ${dec.counts.acted} acted` : ''],
          ]),
        ], 8)}
        <div class="gk-two">
          <div class="gk-panel"><h3>${own ? html`Equity &amp; cycles <span class="s">cumulative realized · shading alternates by cycle, numbered below</span>` : html`Equity <span class="s">cumulative realized</span>`}<a href="${g.href('performance')}">Performance →</a></h3>${equity(v, cycles)}</div>
          <div class="gk-panel"><h3>Open legs ${liveTag(v.legs)}<a href="${g.href('positions')}">Positions →</a></h3>
            ${v.legs.length ? html`<div class="scroll-x">${legHead()}${v.legs.map(legRow)}</div>` : empty('', 'Flat.')}
            <h3 style="margin-top:14px">Latest decisions <a href="${g.href('decisions')}">Decisions →</a></h3>
            ${dec === null ? html`<div class="skel" style="height:90px"></div>` : dec.rows.length
              ? table([['', '96px'], ['', '90px'], ['', 'minmax(0,1fr)']], dec.rows.slice(0, 4).map(r => [html`<span class="mono tiny">${dStamp(r.t)}</span>`, html`<span class="oc ${r.outcome}">${OUT[r.outcome]}</span>`, html`<span class="small">${r.label}: ${r.summary}</span>`]), { minw: 0 })
              : empty('', 'No decisions yet.')}
          </div>
        </div>
        <div class="gk-panel"><h3>Daily P&L <span class="s">last ${daily.length} trading days with a close</span>${own ? html`<a href="${g.href('cycles')}">Cycles →</a>` : ''}</h3>
          ${daily.length ? upDownBars(daily.map(d => d.day), daily.map(d => `${dFull(d.date)}  ${inr(d.day)}`), 110) : empty('', 'No closed trades yet.')}</div>`);
    }
    const stops = [
      ...(own ? [
        poll(() => api.geek(g.id, 'cycles'), 120000, d => { cycles = d instanceof Error ? null : d; paint(); }),
        poll(() => api.geek(g.id, 'adjustments'), 120000, d => { adj = d instanceof Error ? null : d; paint(); }),
      ] : []),
      poll(() => api.geek(g.id, 'decisions'), 30000, d => { dec = d instanceof Error ? { rows: [], counts: { acted: 0, no_action: 0, blocked: 0 } } : d; paint(); }),
    ];
    let lastNext = '';
    const off = g.onEngine(e => {
      const t = e && e.next ? e.next.text : '';
      if (t !== lastNext) { lastNext = t; paint(); }
    });
    const offs = [listen('data', paint), listen('ticks', () => {
      const v = book.find(v0.id);
      if (!v) return;
      tickLegs(el, v.legs);
      const x = book.live(v);
      [['today', x.today], ['overall', x.overall], ['unreal', x.unreal]].forEach(([k, val]) => {
        const e = $(`[data-ov="${k}"]`, el);
        if (e && !(k === 'today' && v.rawStatus === 'stopped')) { setText(e, inr(val)); setTone(e.parentElement, toneOf(val)); }
      });
    })];
    paint();
    return () => { stops.forEach(f => f()); off(); offs.forEach(f => f()); };
  },
};
