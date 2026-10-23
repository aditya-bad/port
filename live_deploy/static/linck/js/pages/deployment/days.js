// Cycles: an intraday deployment's cycles are its days, a positional one's are its entries to exits.

import { html, mount, actions, $, setText, setTone } from '../../core/dom.js';
import { inr, pct, dShort, WEEKDAYS, ist, dayKey, fromDay, plural, tone } from '../../core/format.js';
import { pnlOf, pick } from '../../core/basis.js';
import { api } from '../../core/api.js';
import { openSession } from './session.js';
import { ui, listen } from '../../core/store.js';
import * as book from '../../data/book.js';
import { upDownBars, centreBar } from '../../ui/charts.js';
import { chips, utabs, empty, loading } from '../../ui/components.js';
import { spanMark } from '../../ui/repairs.js';

const cyst = ui('cycles', { f: 'all', s: 'new' });

// The cycle still running (GET /deployments/{id}/cycles marks it "open"):
// what it has booked so far (legs already closed or rolled, in the P&L
// basis) plus the open legs' live P&L. Null when nothing is running.
export function runningCycle(cycles, legs, unreal, now = Date.now()) {
  const c = (cycles || []).find(x => x.status === 'open');
  if (!c && !legs.length) return null;
  const start = c ? new Date(c.start) : new Date(Math.min(...legs.map(l => +new Date(l.opened_at))));
  const booked = c ? pick(c, 'realized_pnl') || 0 : 0;
  return {
    start, booked, unreal, p: booked + unreal,
    held: Math.max(1, Math.round((now - start) / 864e5)),
    adj: c ? c.adjustments || 0 : 0, legs: legs.length,
  };
}
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
  if (!days.length) return empty('No trading days yet', 'Cycles appear after the first closed trade.');
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
      <div class="sect"><h3 class="t-h3">Every trading day</h3><span class="mute small">newest first · click a day for its chart, legs and orders</span></div>
      <div class="tr th day-grid"><span>Date</span><span>Day</span><span class="right">P&L</span><span class="right">Trades</span><span class="right">Return</span><span>How it ended</span></div>
      ${days.slice(0, 40).map(x => html`<div class="tr day-grid small click" data-act="sess" data-at="${+x.d}" role="button" tabindex="0" aria-label="Open ${fd(x.d)}"><span class="mono">${fd(x.d)}</span><span class="mute">${WEEKDAYS[ist(x.d).wd]}</span>
        <span class="num ${tone(x.p)}">${inr(x.p)}</span><span class="num">${x.n}</span><span class="num ${tone(x.p)}">${pct(x.p / v.cap * 100)}</span><span class="mute">${spanMark(v, dayKey(x.d))} ${[...x.rs].join(', ')}</span></div>`)}
      ${days.length > 40 ? html`<p class="small mute">Showing the latest 40 of ${days.length} days. Export the full list from Trades.</p>` : ''}
    </div>`;
}

function runRow(v, run, n) {
  return html`<div class="tr hover click cyc-grid running" data-act="sess" data-at="${+run.start}" role="button" tabindex="0"
      data-tip="Cycle ${n} · running\nOpened  ${fd(run.start)}\nBooked so far  ${inr(run.booked)}\nOpen legs now  ${inr(run.unreal)}\nAdjustments  ${run.adj}">
    <span class="col" style="gap:0"><b class="mono small">#${n} <span class="run-tag">RUNNING</span></b><span class="tiny mute nowrap">${fd(run.start)} → now</span></span>
    <span class="mono small">${plural(run.held, 'day')}</span>
    <span class="small mute">${plural(run.legs, 'open leg')} · booked ${inr(run.booked)} + open ${inr(run.unreal)}</span>
    <span class="num mute">—</span>
    <span class="row" style="gap:5px;justify-content:center">${run.adj ? Array.from({ length: Math.min(run.adj, 6) }, () => html`<span class="dia sm" style="background:var(--b)"></span>`) : html`<span class="mute">—</span>`}</span>
    <span class="col" style="gap:0;align-items:flex-end"><b class="mono ${tone(run.p)}" data-run-pnl>${inr(run.p)}</b><span class="mono mute" style="font-size:11px">so far · ${pct(run.p / v.cap * 100)}</span></span>
    <span class="small mute">still open</span>
  </div>`;
}

function positional(v, cycles) {
  const run = cycles ? runningCycle(cycles, v.legs, book.live(v).unreal) : null;
  const cyc = v.trades.map((t, i) => {
    const o = new Date(t.opened_at), c = new Date(t.closed_at);
    return { i, t, p: pnlOf(t), o, c, held: Math.max(1, Math.round((c - o) / 864e5)), adj: t.adjustments, worst: t.worst };
  }).reverse();
  if (!cyc.length) {
    return run ? html`<div class="scroll-x">
        <div class="sect"><h3 class="t-h3">The cycle running now</h3><span class="mute small">Results and averages appear once a cycle closes.</span></div>
        <div class="tr th cyc-grid"><span>Cycle</span><span>Held</span><span>So far</span><span class="right">Worst point</span><span class="center">Adj.</span><span class="right">Result</span><span>Exit</span></div>
        ${runRow(v, run, 1)}</div>`
      : empty('No cycles yet', 'A cycle appears here once it opens.');
  }
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
      ['Cycles', String(n), run ? 'closed · 1 more running' : 'entry to final exit'],
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
      <div class="sect"><h3 class="t-h3">Every cycle</h3><span class="mute small">One row per position, from entry to final exit. Click a row for its chart, legs and orders.</span></div>
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
      ${run ? runRow(v, run, n + 1) : ''}
      ${CL.slice(0, 40).map(c => {
        const dl = ((c.worst ?? 0) - lo) / sp * 100, pw = Math.abs(c.p) / sp * 100;
        return html`<div class="tr hover click cyc-grid" data-act="sess" data-at="${+c.o}" role="button" tabindex="0" data-tip="Cycle ${c.i + 1}\nOpened  ${fd(c.o)}\nClosed  ${fd(c.c)}\nWorst point  ${c.worst == null ? '—' : inr(c.worst)}\nResult  ${inr(c.p)}\nAdjustments  ${c.adj}">
          <span class="col" style="gap:0"><b class="mono small">#${c.i + 1} ${spanMark(v, dayKey(c.o), dayKey(c.c))}</b><span class="tiny mute nowrap">${fd(c.o)} → ${fd(c.c)}</span></span>
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

// Best and worst mark-to-market reached inside each day (intraday) or
// each cycle (positional), from GET /deployments/{id}/pnl-digest.
function mtmTable(v, rows) {
  const pos = v.modeKey !== 'intraday';
  const title = pos ? 'Recent cycles, best and worst point' : 'Recent days, best and worst point';
  const sub = pos ? 'how far each cycle swung while it was open' : 'how far each day swung before it closed';
  if (rows === null) return html`<div><div class="sect"><h3 class="t-h3">${title}</h3></div>${loading(4)}</div>`;
  if (!rows.length) return '';
  return html`<div class="scroll-x">
    <div class="sect"><h3 class="t-h3">${title}</h3><span class="mute small">${sub}</span></div>
    <div class="tr th mtm-grid"><span>${pos ? 'Cycle' : 'Day'}</span><span class="right">M2M best</span><span class="right">M2M worst</span><span class="right">Realized</span><span class="right hide-phone">Trades</span></div>
    ${rows.slice(0, 30).map(r => { const p = pick(r, 'realized_pnl'); return html`<div class="tr mtm-grid mono small">
      ${pos ? html`<span class="col" style="gap:0"><span>${fd(r.period_start)}</span><span class="tiny mute">→ ${r.period_end ? fd(r.period_end) : 'still open'}</span></span>` : html`<span class="nowrap">${WEEKDAYS[ist(r.period_start).wd]} ${fd(r.period_start)}</span>`}
      <span class="right ${tone(r.max_profit)}">${r.max_profit == null ? '—' : inr(r.max_profit)}</span>
      <span class="right ${tone(r.max_loss)}">${r.max_loss == null ? '—' : inr(r.max_loss)}</span>
      <span class="right ${tone(p)}">${inr(p)}</span>
      <span class="right mute hide-phone">${r.positions_closed}${r.wins + r.losses ? ` · ${r.wins}W ${r.losses}L` : ''}</span></div>`; })}
    <p class="tiny mute" style="margin:8px 0 0">M2M best and worst are gross, from the strategy’s 5-minute marks. Realized follows the P&L basis.</p>
  </div>`;
}
// How many adjustments a day or cycle needed (strategies that adjust).
function adjHist(h) {
  if (!h || !h.supported || !h.buckets.length) return '';
  const mx = Math.max(1, ...h.buckets.map(b => b.units)), unit = h.group_by === 'cycle_id' ? 'cycle' : 'day';
  const total = h.buckets.reduce((a, b) => a + b.units, 0);
  return html`<div>
    <div class="sect"><h3 class="t-h3">Adjustments per ${unit}</h3><span class="mute small">${plural(total, unit)} so far</span></div>
    ${h.buckets.map(b => html`<div class="br-row" data-tip="${b.label}\n${plural(b.units, unit)}  ${Math.round(b.units / total * 100)}%">
      <b class="small">${/^\d+$/.test(b.label) ? plural(+b.label, "adjustment") : b.label}</b><span class="meter"><i class="grow-x" style="width:${b.units / mx * 100}%;background:var(--b)"></i></span>
      <span class="mono tiny right">${plural(b.units, unit)}</span></div>`)}
  </div>`;
}

export default {
  render(el, v0) {
    let digest = null, hist = null, cycles = null;
    function paint() {
      const v = book.find(v0.id) || v0;
      mount(el, html`<div class="pad-x tab-body stack" style="gap:30px">${v.modeKey === 'intraday' ? intraday(v) : positional(v, cycles)}
        <div class="grid-side">${mtmTable(v, digest)}${adjHist(hist)}</div></div>`);
    }
    Promise.all([
      api.digest(v0.id, 'day', 30).then(r => { digest = r || []; }).catch(() => { digest = []; }),
      api.adjustmentHistogram(v0.id).then(r => { hist = r; }).catch(() => { hist = null; }),
      v0.modeKey === 'intraday' ? null : api.cycles(v0.id).then(r => { cycles = r || []; }).catch(() => { cycles = []; }),
    ]).then(paint);
    // The running cycle's P&L moves with the open legs' prices.
    const offTicks = listen('ticks', () => {
      const n = $('[data-run-pnl]', el), v = book.find(v0.id) || v0;
      if (!n || !cycles) return;
      const run = runningCycle(cycles, v.legs, book.live(v).unreal);
      if (run) { setText(n, inr(run.p)); setTone(n, tone(run.p)); }
    });
    // A fill can open or close a cycle: refetch.
    const offData = listen('data', () => { if (v0.modeKey !== 'intraday') api.cycles(v0.id).then(r => { cycles = r || []; paint(); }).catch(() => {}); });
    const off = actions(el, {
      cyf: d => { cyst.set({ f: d.v }); paint(); },
      sess: d => openSession(book.find(v0.id) || v0, d.at),
      cys: d => { cyst.set({ s: d.v }); paint(); },
    });
    paint();
    return () => { off(); offTicks(); offData(); };
  },
};
