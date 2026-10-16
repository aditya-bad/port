// Daily stats (intraday) or Cycles (positional).

import { html, mount, actions } from '../../core/dom.js';
import { inr, pct, dShort, WEEKDAYS, ist, fromDay, plural, tone } from '../../core/format.js';
import { pnlOf } from '../../core/basis.js';
import { ui } from '../../core/store.js';
import * as book from '../../data/book.js';
import { upDownBars, centreBar } from '../../ui/charts.js';
import { chips, utabs, empty } from '../../ui/components.js';

const cyst = ui('cycles', { f: 'all', s: 'new' });
const fd = d => dShort(d);

function statsGrid(stats) {
  return html`<div class="stats" style="grid-template-columns:var(--g-6)">${stats.map(([l, v, s, c]) => html`
    <div class="stat top"><span class="l">${l}</span><span class="v ${c || ''}" style="font-size:28px">${v}</span><span class="s">${s}</span></div>`)}</div>`;
}
function breakdown(title, sub, rows, note) {
  const mx = Math.max(1, ...rows.map(r => Math.abs(r.p)));
  return html`<div>
    <h3 class="t-h3" style="padding-bottom:4px">${title}</h3><p class="mute small" style="margin:0 0 10px">${sub}</p>
    ${rows.map(r => html`<div class="br-row" data-tip="${r.l}\nAverage  ${inr(r.p)}\nSample  ${r.n}">
      <b class="small">${r.l}</b>${centreBar(r.p, mx)}<span class="mono tiny right ${tone(r.p)}">${inr(r.p)} · ${r.n}</span></div>`)}
    <p class="small pretty" style="margin:12px 0 0">${note}</p></div>`;
}

function intraday(v) {
  const byD = {};
  v.trades.forEach(t => {
    const k = ist(t.closed_at).key;
    byD[k] = byD[k] || { d: fromDay(k), p: 0, n: 0, rs: new Set() };
    byD[k].p += pnlOf(t); byD[k].n++; if (t.exit_reason) byD[k].rs.add(t.exit_reason);
  });
  const days = Object.values(byD).sort((a, b) => b.d - a.d);
  if (!days.length) return empty('No trading days yet', 'Daily stats appear after the first closed trade.');
  const green = days.filter(x => x.p > 0), red = days.filter(x => x.p <= 0);
  let streak = 0, cur = 0;
  [...days].reverse().forEach(x => { cur = x.p <= 0 ? cur + 1 : 0; streak = Math.max(streak, cur); });
  const best = days.reduce((a, x) => (x.p > a.p ? x : a), days[0]), worst = days.reduce((a, x) => (x.p < a.p ? x : a), days[0]);
  const dow = [1, 2, 3, 4, 5].map(w => { const L = days.filter(x => ist(x.d).wd === w); return { l: WEEKDAYS[w], w, p: L.length ? L.reduce((a, x) => a + x.p, 0) / L.length : 0, n: `${L.length}d` }; });
  const bestDow = dow.reduce((a, x) => (x.p > a.p ? x : a), dow[0]), worstDow = dow.reduce((a, x) => (x.p < a.p ? x : a), dow[0]);
  const last = days.slice(0, 60).reverse();
  const avg = days.reduce((a, x) => a + x.p, 0) / days.length;
  return html`
    ${statsGrid([
      ['Trading days', String(days.length), 'days with at least one trade'],
      ['Green days', `${Math.round(green.length / days.length * 100)}%`, `${green.length} up · ${red.length} down`],
      ['Average day', inr(avg), `avg green ${inr(green.length ? green.reduce((a, x) => a + x.p, 0) / green.length : 0)}`],
      ['Best day', inr(best.p), fd(best.d), 'pos'], ['Worst day', inr(worst.p), fd(worst.d), 'neg'],
      ['Longest red run', `${streak} days`, 'consecutive losing days'],
    ])}
    <div class="grid-side">
      <div>
        <div class="sect"><h3 class="t-h3">Last ${last.length} trading days</h3><span class="mute small">P&L per day</span></div>
        ${upDownBars(last.map(x => x.p), last.map(x => `${WEEKDAYS[ist(x.d).wd]} ${fd(x.d)}\nP&L  ${inr(x.p)}\nTrades  ${x.n}\nOf capital  ${pct(x.p / v.cap * 100)}`))}
        <div class="axis-row"><span>${fd(last[0].d)}</span><span>${fd(last[last.length - 1].d)}</span></div>
      </div>
      ${breakdown('By weekday', 'average P&L per trading day', dow,
        `${bestDow.l} is the strongest day on average and ${worstDow.l} the weakest${worstDow.p < 0 ? `, losing ${inr(worstDow.p)} on a typical ${worstDow.l}.` : '.'}`)}
    </div>
    <div class="scroll-x">
      <div class="sect"><h3 class="t-h3">Every trading day</h3><span class="mute small">newest first</span></div>
      <div class="tr th day-grid"><span>Date</span><span>Day</span><span class="right">P&L</span><span class="right">Trades</span><span class="right">Return</span><span>How it ended</span></div>
      ${days.slice(0, 40).map(x => html`<div class="tr day-grid small"><span class="mono">${fd(x.d)}</span><span class="mute">${WEEKDAYS[ist(x.d).wd]}</span>
        <span class="num ${tone(x.p)}">${inr(x.p)}</span><span class="num">${x.n}</span><span class="num ${tone(x.p)}">${pct(x.p / v.cap * 100)}</span><span class="mute">${[...x.rs].join(', ')}</span></div>`)}
      ${days.length > 40 ? html`<p class="small mute">Showing the latest 40 of ${days.length} days. Export the full list from Trades.</p>` : ''}
    </div>`;
}

function positional(v) {
  const cyc = v.trades.map((t, i) => {
    const o = new Date(t.opened_at), c = new Date(t.closed_at);
    return { i, t, p: pnlOf(t), o, c, held: Math.max(1, Math.round((c - o) / 864e5)), adj: t.adjustments, worst: t.worst };
  }).reverse();
  if (!cyc.length) return empty('No cycles yet', 'A cycle appears here once it closes.');
  const n = cyc.length, adjd = cyc.filter(c => c.adj), un = cyc.filter(c => !c.adj);
  const wr = L => (L.length ? `${Math.round(L.filter(c => c.p > 0).length / L.length * 100)}%` : '—');
  const withWorst = cyc.filter(c => c.worst != null);
  const avgWorst = withWorst.length ? withWorst.reduce((a, c) => a + c.worst, 0) / withWorst.length : null;
  const BK = [['≤ 5 days', 0, 5], ['6–10', 6, 10], ['11–15', 11, 15], ['16+', 16, 1e9]].map(([l, a, b]) => {
    const L = cyc.filter(c => c.held >= a && c.held <= b);
    return { l, p: L.length ? L.reduce((x, c) => x + c.p, 0) / L.length : 0, n: L.length };
  });
  const last = cyc.slice(0, 40).reverse();
  const s = cyst.get();
  const ok = (c, f) => f === 'all' || (f === 'win' ? c.p > 0 : f === 'loss' ? c.p <= 0 : c.adj > 0);
  const CL = cyc.filter(c => ok(c, s.f)).sort((a, b) => (s.s === 'best' ? b.p - a.p : s.s === 'worst' ? a.p - b.p : s.s === 'long' ? b.held - a.held : b.i - a.i));
  const lo = Math.min(0, ...cyc.map(c => Math.min(c.worst ?? 0, c.p))), hi = Math.max(1, ...cyc.map(c => c.p)), sp = hi - lo, z = -lo / sp * 100;
  const hmx = Math.max(1, ...cyc.map(c => c.held));
  return html`
    ${statsGrid([
      ['Cycles', String(n), 'entry to final exit'],
      ['Average hold', `${(cyc.reduce((x, c) => x + c.held, 0) / n).toFixed(1)} days`, 'per cycle'],
      ['Average cycle', inr(cyc.reduce((x, c) => x + c.p, 0) / n), 'P&L per cycle'],
      ['Adjusted cycles', `${Math.round(adjd.length / n * 100)}%`, `${adjd.reduce((x, c) => x + c.adj, 0)} adjustments in total`],
      ['Win rate', `${wr(adjd)} / ${wr(un)}`, 'adjusted / not adjusted'],
      ['Average worst point', avgWorst == null ? '—' : inr(avgWorst), 'deepest point inside a cycle', 'neg'],
    ])}
    <div class="grid-side">
      <div>
        <div class="sect"><h3 class="t-h3">Last ${last.length} cycles</h3><span class="mute small">P&L per cycle</span></div>
        ${upDownBars(last.map(c => c.p), last.map(c => `Cycle ${c.i + 1}\nResult  ${inr(c.p)}\nHeld  ${plural(c.held, 'day')}\nWorst point  ${c.worst == null ? '—' : inr(c.worst)}\nAdjustments  ${c.adj}\nExit  ${c.t.exit_reason || '—'}`))}
        <div class="axis-row"><span>cycle ${last[0].i + 1}</span><span>cycle ${last[last.length - 1].i + 1}</span></div>
      </div>
      ${breakdown('By holding time', 'average P&L per cycle', BK,
        adjd.length && un.length ? `Adjusted cycles win ${wr(adjd)} of the time against ${wr(un)} for untouched ones.${avgWorst != null ? ` The average cycle dips to ${inr(avgWorst)} before it closes.` : ''}` : '')}
    </div>
    <div class="scroll-x">
      <div class="sect"><h3 class="t-h3">Every cycle</h3><span class="mute small">One row per position, from entry to final exit. Hover a row for the numbers.</span></div>
      <div class="row wrap" style="gap:14px;padding-bottom:12px">
        <div class="chips">${chips([['all', 'All'], ['win', 'Winners'], ['loss', 'Losers'], ['adj', 'Adjusted']].map(([k, l]) => [k, l, cyc.filter(c => ok(c, k)).length]), s.f, 'cyf')}</div>
        <span class="grow"></span><span class="tiny mute">Sort</span>${utabs([['new', 'Newest'], ['best', 'Best'], ['worst', 'Worst'], ['long', 'Longest']], s.s, 'cys')}
      </div>
      <div class="row wrap tiny mute" style="gap:18px;padding-bottom:10px">
        <span class="row" style="gap:6px"><i style="width:18px;height:12px;background:var(--rs)"></i>How far it fell while open</span>
        <span class="row" style="gap:6px"><i style="width:18px;height:6px;background:var(--pos)"></i>Closed in profit</span>
        <span class="row" style="gap:6px"><i style="width:18px;height:6px;background:var(--r)"></i>Closed at a loss</span>
        <span class="row" style="gap:6px"><i style="width:2px;height:12px;background:var(--ink)"></i>₹0</span>
        <span class="row" style="gap:6px"><span class="dia sm" style="background:var(--b)"></span>One adjustment</span>
      </div>
      <div class="tr th cyc-grid"><span>Cycle</span><span>Held</span><span>Journey: worst point, then close</span><span class="right">Worst point</span><span class="center">Adj.</span><span class="right">Result</span><span>Exit</span></div>
      ${CL.slice(0, 40).map(c => {
        const dl = ((c.worst ?? 0) - lo) / sp * 100, pw = Math.abs(c.p) / sp * 100;
        return html`<div class="tr hover cyc-grid" data-tip="Cycle ${c.i + 1}\nOpened  ${fd(c.o)}\nClosed  ${fd(c.c)}\nWorst point  ${c.worst == null ? '—' : inr(c.worst)}\nResult  ${inr(c.p)}\nAdjustments  ${c.adj}">
          <span class="col" style="gap:0"><b class="mono small">#${c.i + 1}</b><span class="tiny mute nowrap">${fd(c.o)} → ${fd(c.c)}</span></span>
          <span class="col" style="gap:5px"><span class="mono small">${plural(c.held, 'day')}</span><span class="meter" style="height:3px"><i style="width:${c.held / hmx * 100}%;background:var(--mute)"></i></span></span>
          <span class="journey">
            ${c.worst != null ? html`<i style="left:${dl}%;width:${Math.max(0, z - dl)}%;background:var(--rs);top:0;bottom:0"></i>` : ''}
            <i style="top:9px;height:6px;left:${c.p >= 0 ? z : z - pw}%;width:${pw}%;background:${c.p >= 0 ? 'var(--pos)' : 'var(--r)'}"></i>
            <i style="top:-4px;bottom:-4px;left:${z}%;width:2px;background:var(--ink)"></i>
          </span>
          <span class="num neg">${c.worst == null ? '—' : inr(c.worst)}</span>
          <span class="row" style="gap:5px;justify-content:center">${c.adj ? Array.from({ length: Math.min(c.adj, 6) }, () => html`<span class="dia sm" style="background:var(--b)"></span>`) : html`<span class="mute">—</span>`}</span>
          <span class="col" style="gap:0;align-items:flex-end"><b class="mono ${tone(c.p)}">${inr(c.p)}</b><span class="mono mute" style="font-size:11px">${pct(c.p / v.cap * 100)} of capital</span></span>
          <span class="small mute">${c.t.exit_reason || '—'}</span>
        </div>`;
      })}
      <p class="small mute">${CL.length > 40 ? `Showing 40 of ${CL.length} cycles.` : CL.length ? '' : 'No cycles match this filter.'}</p>
    </div>`;
}

export default {
  render(el, v0) {
    function paint() {
      const v = book.find(v0.id) || v0;
      mount(el, html`<div class="pad-x tab-body stack" style="gap:30px">${v.modeKey === 'intraday' ? intraday(v) : positional(v)}</div>`);
    }
    const off = actions(el, {
      cyf: d => { cyst.set({ f: d.v }); paint(); },
      cys: d => { cyst.set({ s: d.v }); paint(); },
    });
    paint();
    return () => off();
  },
};
