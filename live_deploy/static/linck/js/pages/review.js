// Week in review: one market week across every deployment, set against
// the weeks before it — what it made, day by day and deployment by
// deployment, the best and worst trades, rules that fired more than
// usual, health alerts and the week's journal notes (app/v2/review.py).
// #/review?w=YYYY-MM-DD picks the week; it arrives as a push on Saturday.

import { api } from '../core/api.js';
import { html, raw, mount, actions } from '../core/dom.js';
import { inr, tone, toneVar, plural, fromDay, dDay, dStamp, MONTHS } from '../core/format.js';
import { pick, current as basisNow } from '../core/basis.js';
import { go, href } from '../core/router.js';
import { empty, loading, stat, fixTag } from '../ui/components.js';
import { cycleName } from '../ui/repairs.js';

const p = row => pick(row, 'pnl') || 0;
// "leg_stop" -> "Leg stop" (a rule's key, as the strategies write it)
export const ruleLabel = k => { const t = String(k || 'trade').replace(/[_-]+/g, ' ').trim(); return t.charAt(0).toUpperCase() + t.slice(1); };
const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

// "10–14 Mar 2025", "28 Sep–2 Oct 2026"
export function weekName(a, b) {
  const x = fromDay(a), y = fromDay(b);
  const [dx, mx, dy, my, yy] = [x.getUTCDate(), x.getUTCMonth(), y.getUTCDate(), y.getUTCMonth(), y.getUTCFullYear()];
  return mx === my ? `${dx}–${dy} ${MONTHS[my]} ${yy}` : `${dx} ${MONTHS[mx]}–${dy} ${MONTHS[my]} ${yy}`;
}

// "+₹1,250 vs usual" / "first week" — this week against the usual one.
export function vsUsual(now, usual) {
  if (usual == null) return 'no earlier weeks to compare';
  const d = now - usual;
  return Math.abs(d) < 0.5 ? 'same as a usual week' : `${inr(d)} vs a usual week (${inr(usual)})`;
}

// Bars either side of zero, one per item, each with its tip:
// items = [[label, value, tip]].
export function bars(items, { h = 160, w = 600 } = {}) {
  if (typeof matchMedia === 'function' && matchMedia('(max-width:900px)').matches) { w = 360; h = 170; }   // a phone: the same marks, readable labels
  if (!items.length) return '';
  const mx = Math.max(1, ...items.map(i => Math.abs(i[1])));
  const bw = w / items.length, mid = h / 2 - 8;
  let s = `<line x1="0" x2="${w}" y1="${mid}" y2="${mid}" stroke="var(--line2)"/>`;
  items.forEach(([l, v, tip], i) => {
    const bh = Math.abs(v) / mx * (mid - 6), x = i * bw + bw * 0.18;
    const y = v >= 0 ? mid - bh : mid;
    s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" height="${Math.max(1, bh).toFixed(1)}" fill="${toneVar(v)}"/>`;
    s += `<rect class="hit" x="${(i * bw).toFixed(1)}" y="0" width="${bw.toFixed(1)}" height="${h}" fill="transparent" data-tip="${esc(tip)}"/>`;
    const every = Math.ceil(52 / bw);   // labels need ~52 units: on a narrow chart, every other one
    if (i % every === (items.length - 1) % every) s += `<text x="${(i * bw + bw / 2).toFixed(1)}" y="${h - 2}" text-anchor="middle">${esc(l)}</text>`;
  });
  return html`<svg class="rv-bars" viewBox="0 0 ${w} ${h}" role="img" aria-label="P&L bars">${raw(s)}</svg>`;
}

// VIX through the week, and how premium sellers and buyers did against it.
export function vixLine(v, sides) {
  if (!v) return null;
  const word = Math.abs(v.change) < 0.05 ? 'held at' : v.change > 0 ? 'rose to' : 'fell to';
  let out = `VIX ${word} ${v.close.toFixed(2)} from ${v.open.toFixed(2)} (${v.change >= 0 ? '+' : '−'}${Math.abs(v.change).toFixed(2)}), touching ${v.high.toFixed(2)}.`;
  const part = (k, l) => (sides[k] ? `${l} ${inr(p(sides[k]))} (${plural(sides[k].n, 'deployment')})` : null);
  const bits = [part('sold', 'Premium sellers booked'), part('bought', 'buyers')].filter(Boolean);
  if (bits.length) out += ` ${bits.join('; ')}.`;
  return out;
}

function vixSection(w) {
  if (!w.vix) return w.vix_error ? html`<p class="small mute">India VIX: ${w.vix_error}</p>` : '';
  const v = w.vix, rise = v.change > 0;
  return html`<section class="rv-vix"><h3 class="t-h3">India VIX <span class="mute small">the market’s priced volatility · rising hurts premium sellers</span></h3>
    <div class="row wrap" style="gap:28px;align-items:baseline">
      <span class="mono">${v.open.toFixed(2)} → <b class="${rise ? 'neg' : v.change < 0 ? 'pos' : ''}">${v.close.toFixed(2)}</b></span>
      <span class="small mute">high ${v.high.toFixed(2)} · low ${v.low.toFixed(2)}</span>
      <span class="small">${vixLine(v, w.by_side)}</span>
    </div></section>`;
}

// The week's repaired days, and the days the end-of-day check flagged and
// nothing has been applied to: each opens its case on the Repair page.
function repairsSection(w) {
  const R = w.repairs || [];
  if (!R.length) return '';
  const flagged = R.filter(r => r.state === 'flagged').length;
  return html`<section><h3 class="t-h3">Repairs <span class="mute small">${flagged ? `${plural(flagged, 'day')} flagged and not repaired: their numbers aren’t what the strategy would have done` : 'cycles replayed from Kite’s 1-minute candles'}</span></h3>
    <div class="col" style="gap:8px">${R.map(r => html`<a class="rv-flag" href="${href(`repair/${r.day}/${r.deployment_id}`)}">
      <b>${r.state === 'flagged' ? html`<span class="fix-flag">Flagged</span>` : html`<span class="fix-tag">Repaired</span>`} ${r.name} · ${r.cycle ? cycleName(r.cycle) : dDay(fromDay(r.day))}</b>
      <span class="small mute">${r.state === 'flagged' ? `${r.headline || r.status}. Review it ›` : 'Its trades from where it parted were replayed by its own strategy. Open the repair ›'}</span></a>`)}</div></section>`;
}
const depMark = (w, id) => {
  const R = (w.repairs || []).filter(r => r.deployment_id === id);
  if (R.some(r => r.state === 'flagged')) return html`<span class="fix-flag">Flagged</span>`;
  const C = [...new Map(R.map(r => [r.cycle ? r.cycle.from : r.day, r])).values()];
  return C.length ? fixTag(true, null, `Repaired this week: ${plural(C.length, 'cycle')} — ${C.map(r => (r.cycle ? cycleName(r.cycle) : dDay(fromDay(r.day)))).join(', ')}`) : '';
};

function tradeLine(t) {
  return html`<a class="rv-trade" href="${href(`d/${t.deployment_id}/trades`)}" data-tip="${`${t.name}\nOpened  ${dStamp(t.opened_at)}\nClosed  ${dStamp(t.closed_at)}\nP&L  ${inr(p(t))}${t.exit_reason ? `\nClosed by  ${ruleLabel(t.exit_reason)}` : ''}`}">
    <b>${t.name}</b>${fixTag(t.repaired)}<span class="mono tiny mute">${dDay(t.closed_at)}</span><span class="grow"></span><span class="mono ${tone(p(t))}">${inr(p(t))}</span></a>`;
}

export default {
  title: 'Week in review',
  render(main, params) {
    let data = null;
    const week = params && params.w;
    function paint() {
      if (data === null) { mount(main, html`<div class="page">${loading(6)}</div>`); return; }
      if (data instanceof Error) { mount(main, html`<div class="page">${empty('Couldn’t load the week', data.message)}</div>`); return; }
      const w = data, T = w.totals, wr = T.trades ? Math.round(T.wins / T.trades * 100) : null;
      const vd = Object.fromEntries(((w.vix && w.vix.days) || []).map(x => [x.day, x]));
      const vh = Object.fromEntries(((w.vix && w.vix.history) || []).map(x => [x.week, x]));
      const vtxt = x => (x ? `\nVIX  ${x.close.toFixed(2)} (${x.change >= 0 ? '+' : '−'}${Math.abs(x.change).toFixed(2)})` : '');
      const days = w.days.map(d => [dDay(d.day).slice(0, 3), p(d), `${dDay(d.day)}\nP&L  ${inr(p(d))}\nPositions closed  ${d.closes}${vtxt(vd[d.day])}`]);
      const best = w.days.filter(d => d.closes).sort((a, b) => p(b) - p(a));
      const hist = [...w.history, { week: w.week, ...T }].map(h => [`${fromDay(h.week).getUTCDate()} ${MONTHS[fromDay(h.week).getUTCMonth()]}`, p(h),
        `Week of ${dDay(h.week)}${h.week === w.week ? ' (this week)' : ''}\nP&L  ${inr(p(h))}\nTrades  ${h.trades}${h.trades ? `\nWon  ${Math.round(h.wins / h.trades * 100)}%` : ''}${vtxt(vh[h.week])}`]);
      mount(main, html`<div class="page-head">
          <div class="col"><h1 class="t-h1">Week in review</h1>
            <span class="mute">${weekName(w.week, w.week_end)}${w.complete ? '' : ' · so far'} · every deployment against its usual week <span class="stamp">${basisNow().stamp}</span></span></div>
          <span class="grow"></span>
          <div class="row" style="gap:8px">
            <button type="button" class="btn btn-sm" data-act="wk" data-w="${w.prev || ''}" ${w.prev ? '' : 'disabled'}>← Older</button>
            <button type="button" class="btn btn-sm" data-act="wk" data-w="${w.next || ''}" ${w.next ? '' : 'disabled'}>Newer →</button>
          </div>
        </div>
        <div class="page" style="padding-top:4px">
        <div class="stats rv-stats">
          ${stat('P&L booked', html`<span class="${tone(p(T))}">${inr(p(T))}</span>`, vsUsual(p(T), w.usual ? p(w.usual) : null))}
          ${stat('Trades', String(T.trades), w.usual ? `${plural(T.trades, 'trade')} closed` : '')}
          ${stat('Won', wr == null ? '—' : `${wr}%`, T.trades ? `${T.wins} of ${T.trades}` : 'nothing closed')}
          ${stat('Best day', best.length ? html`<span class="${tone(p(best[0]))}">${inr(p(best[0]))}</span>` : '—', best.length ? dDay(best[0].day) : '')}
          ${stat('Worst day', best.length ? html`<span class="${tone(p(best[best.length - 1]))}">${inr(p(best[best.length - 1]))}</span>` : '—', best.length ? dDay(best[best.length - 1].day) : '')}
        </div>
        ${vixSection(w)}
        <div class="rv-two">
          <section><h3 class="t-h3">Day by day</h3>${bars(days)}</section>
          <section><h3 class="t-h3">This week and the 8 before</h3>${bars(hist)}</section>
        </div>
        ${w.health.length || w.rules.length ? html`<div class="rv-two">
          <section><h3 class="t-h3">Health alerts <span class="mute small">drifting from how it usually trades</span></h3>
            ${w.health.length ? html`<div class="col" style="gap:8px">${w.health.map(f => html`<a class="rv-flag" href="${href(`d/${f.deployment_id}/performance`)}"><b>${f.name}: ${f.title.toLowerCase()}</b><span class="small mute">${f.body}</span></a>`)}</div>` : html`<p class="mute small">None. Every deployment is trading like it usually does.</p>`}</section>
          <section><h3 class="t-h3">Rules that fired more than usual</h3>
            ${w.rules.length ? html`<div class="col" style="gap:8px">${w.rules.map(r => html`<a class="rv-flag" href="${href(`d/${r.deployment_id}/geek/decisions`)}"><b>${r.name}: ${ruleLabel(r.trigger)}</b><span class="small mute">${r.n} times this week, ${r.usual ? `usually about ${r.usual}` : 'never in the 8 weeks before'}</span></a>`)}</div>` : html`<p class="mute small">None. Every rule fired about as often as usual.</p>`}</section>
        </div>` : ''}
        ${repairsSection(w)}
        <section><h3 class="t-h3">Deployment by deployment <span class="mute small">P&amp;L booked this week, every leg closed · trades counted when they finish</span></h3>
          ${w.deployments.length ? html`<div class="rv-tbl" role="table">
            <div class="tr th" role="row"><span>Deployment</span><span class="num">P&amp;L</span><span class="num">Usual<span class="hide-phone"> week</span></span><span class="num">Trades</span><span class="num hp">Won</span><span class="num hp">Best</span><span class="num hp">Worst</span></div>
            ${w.deployments.map(d => html`<a class="tr" role="row" href="${href(`d/${d.deployment_id}/performance`)}"
                data-tip="${`${d.name}\nThis week  ${inr(p(d))}\nUsual week  ${d.usual ? inr(p(d.usual)) : '—'}${d.usual ? ` (${d.weeks_known} week${d.weeks_known === 1 ? '' : 's'})` : ''}\nTrades  ${d.trades}${d.usual ? ` (usually ${d.usual.trades})` : ''}${d.open_positions ? `\nOpen now  ${plural(d.open_positions, 'position')}` : ''}`}">
              <span class="col" style="gap:1px"><b>${d.name} ${depMark(w, d.deployment_id)}</b><span class="tiny mute">${d.strategy || ''}${d.side ? ` · ${d.side === 'sold' ? 'sells' : 'buys'} premium` : ''}${d.status === 'stopped' ? ' · stopped' : ''}</span></span>
              <span class="num mono ${tone(p(d))}">${inr(p(d))}</span>
              <span class="num mono mute">${d.usual ? inr(p(d.usual)) : '—'}</span>
              <span class="num mono">${d.trades}</span>
              <span class="num mono hp">${d.trades ? `${Math.round(d.wins / d.trades * 100)}%` : '—'}</span>
              <span class="num mono hp ${d.best ? tone(p(d.best)) : ''}">${d.best ? inr(p(d.best)) : '—'}</span>
              <span class="num mono hp ${d.worst ? tone(p(d.worst)) : ''}">${d.worst ? inr(p(d.worst)) : '—'}</span></a>`)}
          </div>` : empty('', 'No deployment traded this week or the 8 before.')}</section>
        <div class="rv-two">
          <section><h3 class="t-h3">Best trades</h3>${w.best.length ? html`<div class="col">${w.best.map(tradeLine)}</div>` : html`<p class="mute small">No winning trades this week.</p>`}</section>
          <section><h3 class="t-h3">Worst trades</h3>${w.worst.length ? html`<div class="col">${w.worst.map(tradeLine)}</div>` : html`<p class="mute small">No losing trades this week.</p>`}</section>
        </div>
        <section><h3 class="t-h3">Journal <span class="mute small">notes from the week · <a href="${href('journal')}">Journal →</a></span></h3>
          ${w.notes.length ? html`<div class="col" style="gap:10px">${w.notes.map(n => html`<div class="rv-note"><span class="mono tiny mute">${dDay(n.day)} · ${n.standalone ? 'not attached' : n.deployment_name || 'whole day'}</span><p>${n.body}</p>${n.tags.length ? html`<span class="tiny">${n.tags.map(t => html`<span class="jr-tag">${t}</span> `)}</span>` : ''}</div>`)}</div>`
            : html`<p class="mute small">No notes this week.</p>`}</section>
        </div>`);
    }
    const off = actions(main, { wk: d => { if (d.w) go(`review?w=${d.w}`); } });
    let alive = true;
    api.review(week).then(d => { if (alive) { data = d; paint(); } }).catch(e => { if (alive) { data = e; paint(); } });
    paint();
    return () => { alive = false; off(); };
  },
};
