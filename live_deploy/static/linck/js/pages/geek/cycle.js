// One cycle, end to end (#/d/:id/geek/cycle?c=N): everything about it in
// one place. How it was entered, its P&L path with every roll on it, the
// index and combined P&L with its legs and orders (the session sheet's body), the
// side balance against the rules, the call and put premium, each leg's
// strike over time, every action and every check in order, its rolls
// scored, every leg, and where the money went. From Cycles or Adjustments.

import { api } from '../../core/api.js';
import { html, mount, actions } from '../../core/dom.js';
import { inr, dStamp, dShort, hhmm, price } from '../../core/format.js';
import { go } from '../../core/router.js';
import * as book from '../../data/book.js';
import { empty, loading } from '../../ui/components.js';
import { loadSession, sessionBody } from '../deployment/session.js';
import { journeys } from './cycles.js';
import { head, tiles, table, poll, svg, path, gapped, steps, money, tone, ratio, bars, LABEL, yLabels, wide } from './ui.js';

// Viewbox widths: half-width panels and full-width ones (text keeps its size).
const W = 640, WF = 1200;
const ms = t => new Date(t).getTime();
const endTag = r => html`<span class="oc ${r.ended_by === 'checkpoint_target' ? '' : r.ended_by === 'open' ? 'no_action' : 'blocked'}">${r.ended_by === 'open' ? 'Running' : LABEL[r.ended_by] || r.ended_by.replace(/_/g, ' ')}</span>`;
const legTxt = l => `${l.opt || ''} ${l.strike ?? l.symbol}`;

// A time axis over the cycle: x(t) in px, plus day ticks.
function timeAxis(t0, t1, left = 46, right = W - 12) {
  const span = (t1 - t0) || 1;
  const x = t => left + (ms(t) - t0) / span * (right - left);
  let ticks = '';
  const days = [];
  for (let d = new Date(t0); d.getTime() <= t1; d = new Date(d.getTime() + 864e5)) days.push(d.getTime());
  const every = Math.max(1, Math.ceil(days.length / 8));
  days.forEach((d, i) => { if (i % every === 0) ticks += `<text x="${x(d)}" y="__Y__" text-anchor="middle">${dShort(d).replace(/ '\d\d$/, '')}</text>`; });
  return { x, ticks: y => ticks.replace(/__Y__/g, y) };
}

// Vertical markers for every roll and the entry/exit, on any time chart.
function marks(cy, ax, top, bottom) {
  let s = '';
  for (const r of cy.rolls) {
    const X = ax.x(r.t);
    s += `<line x1="${X}" x2="${X}" y1="${top}" y2="${bottom}" stroke="var(--b)" stroke-dasharray="3 3"/>`;
    s += `<text class="lab" x="${X + 3}" y="${bottom - 3}" style="fill:var(--b);font-size:10px">${r.side || ''} ${r.direction || ''}</text>`;
  }
  return s;
}

// The Cycles page's journey chart for this one cycle.
function journey(cy) {
  const t0 = ms(cy.row.start);
  const path = cy.path.map(p => [(ms(p.t) - t0) / 3600000, p.pnl]);
  return journeys([{ ...cy.row, path, path_kind: cy.path_kind, path_gaps: cy.path_gaps }], cy.checkpoint_target, true);
}

function pnlChart(cy) {
  const P = cy.path;
  if (P.length < 2) return empty('', 'Appears after this cycle’s first two 5-minute snapshots.');
  const H = 260, t0 = ms(cy.row.start), t1 = cy.row.end ? ms(cy.row.end) : ms(P[P.length - 1].t);
  const ax = timeAxis(t0, t1, 64, WF - 12);
  const vals = P.map(p => p.pnl), tgt = cy.checkpoint_target;
  const top = Math.max(tgt, ...vals), bot = Math.min(0, ...vals), pad = (top - bot || 1) * 0.08;
  const hi = top + pad, lo = bot - pad;
  const y = v => 12 + (hi - v) / ((hi - lo) || 1) * (H - 44);
  const booked = cy.path_kind === 'booked';
  const pts = booked ? steps(P.map(p => [ax.x(p.t), y(p.pnl)])) : P.map(p => [ax.x(p.t), y(p.pnl)]);
  const d = path(pts), z = y(0), line = booked ? { solid: d, gaps: '' } : gapped(pts, cy.path_gaps);
  const area = `${d}L${pts[pts.length - 1][0].toFixed(1)} ${z.toFixed(1)}L${pts[0][0].toFixed(1)} ${z.toFixed(1)}Z`;
  let s = `<defs><clipPath id="cyU"><rect x="0" y="0" width="${WF}" height="${z}"/></clipPath><clipPath id="cyD"><rect x="0" y="${z}" width="${WF}" height="${H}"/></clipPath></defs>`;
  [bot, top].forEach(v => { s += `<line x1="64" x2="${WF - 12}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/>`; });
  s += yLabels([0, bot, top], y, 58);
  s += `<path d="${area}" fill="var(--gs)" clip-path="url(#cyU)"/><path d="${area}" fill="var(--rs)" clip-path="url(#cyD)"/>`;
  s += `<line x1="64" x2="${WF - 12}" y1="${z}" y2="${z}" stroke="var(--ink)"/>`;
  s += `<line x1="64" x2="${WF - 12}" y1="${y(tgt)}" y2="${y(tgt)}" stroke="var(--g)" stroke-dasharray="5 3"/><text class="lab" x="${WF - 12}" y="${y(tgt) - 5}" text-anchor="end" style="fill:var(--g)">checkpoint ${inr(tgt, { sign: false })}</text>`;
  s += marks(cy, ax, 10, H - 32);
  s += `<path d="${line.solid}" fill="none" stroke="var(--ink)" stroke-width="1.8"${booked ? ' stroke-dasharray="6 4"' : ''}/>`;
  if (line.gaps) s += `<path class="gap" d="${line.gaps}" fill="none" stroke="var(--ink)" stroke-width="1.8" stroke-dasharray="1 4" stroke-linecap="round"/>`;
  if (booked) return finish(s);
  const iLo = vals.indexOf(Math.min(...vals)), iHi = vals.indexOf(Math.max(...vals));
  // near the right edge a label goes to the left of its point
  const lab = (X, Y, txt, c) => (X > WF - 160
    ? `<text class="lab" x="${X - 8}" y="${Y}" text-anchor="end" style="fill:${c}">${txt}</text>`
    : `<text class="lab" x="${X + 6}" y="${Y}" style="fill:${c}">${txt}</text>`);
  if (vals[iLo] < 0) s += `<circle cx="${pts[iLo][0]}" cy="${pts[iLo][1]}" r="4" fill="var(--r)"/>${lab(pts[iLo][0], pts[iLo][1] + 14, `worst ${inr(vals[iLo])}`, 'var(--r)')}`;
  if (vals[iHi] > 0) s += `<circle cx="${pts[iHi][0]}" cy="${pts[iHi][1]}" r="4" fill="var(--g)"/>${lab(pts[iHi][0], pts[iHi][1] - 8, `best ${inr(vals[iHi])}`, 'var(--g)')}`;
  return finish(s);
  function finish(s) {
    const L = pts[pts.length - 1];
    s += `<circle cx="${L[0]}" cy="${L[1]}" r="4" fill="var(--ink)"/>`;
    s += ax.ticks(H - 8);
    return wide(svg(WF, H, s, 'Cycle P&L over its life, with every roll'));
  }
}

function balanceChart(cy) {
  const S = cy.side;
  if (S.length < 2) return empty('', 'Appears after this cycle’s first two 5-minute snapshots.');
  const H = 200, t0 = ms(cy.row.start), t1 = cy.row.end ? ms(cy.row.end) : ms(S[S.length - 1].t);
  const ax = timeAxis(t0, t1), R = cy.rules;
  const lo = Math.min(0.35, R.trigger - 0.05, ...S.map(p => p.ratio)), hi = 1.02;
  const y = v => 10 + (hi - v) / (hi - lo) * (H - 40);
  let s = `<rect x="46" y="${y(R.band[1])}" width="${W - 58}" height="${y(R.band[0]) - y(R.band[1])}" fill="var(--gs)"/>`;
  [0.4, 0.6, 0.8, 1].filter(v => v >= lo).forEach(v => { s += `<line x1="46" x2="${W - 12}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="40" y="${y(v) + 3}" text-anchor="end">${v * 100}%</text>`; });
  s += `<line x1="46" x2="${W - 12}" y1="${y(R.floor)}" y2="${y(R.floor)}" stroke="var(--wr)" stroke-dasharray="4 3"/><text class="lab" x="${W - 12}" y="${y(R.floor) - 4}" text-anchor="end" style="fill:var(--wr)">EOD floor ${R.floor * 100}%</text>`;
  s += `<line x1="46" x2="${W - 12}" y1="${y(R.trigger)}" y2="${y(R.trigger)}" stroke="var(--r)" stroke-width="1.5"/><text class="lab" x="${W - 12}" y="${y(R.trigger) + 13}" text-anchor="end" style="fill:var(--r)">roll now ${R.trigger * 100}%</text>`;
  s += marks(cy, ax, 6, H - 30);
  s += `<path d="${path(S.map(p => [ax.x(p.t), y(p.ratio)]))}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>`;
  s += ax.ticks(H - 8);
  return svg(W, H, s, 'Smaller side as a share of the bigger, with the roll rules');
}

function premiumChart(cy) {
  const S = cy.side;
  if (S.length < 2) return empty('', 'Appears after this cycle’s first two 5-minute snapshots.');
  const H = 190, t0 = ms(cy.row.start), t1 = cy.row.end ? ms(cy.row.end) : ms(S[S.length - 1].t);
  const ax = timeAxis(t0, t1);
  const all = S.flatMap(p => [p.ce, p.pe]), lo = Math.min(...all) * 0.9, hi = Math.max(...all) * 1.06;
  const y = v => 10 + (hi - v) / ((hi - lo) || 1) * (H - 40);
  let s = '';
  for (let k = 0; k <= 3; k++) { const v = lo + (hi - lo) * k / 3; s += `<line x1="46" x2="${W - 12}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="40" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`; }
  s += marks(cy, ax, 6, H - 30);
  s += `<path d="${path(S.map(p => [ax.x(p.t), y(p.ce)]))}" fill="none" stroke="var(--c5)" stroke-width="1.8"/>`;
  s += `<path d="${path(S.map(p => [ax.x(p.t), y(p.pe)]))}" fill="none" stroke="var(--c4)" stroke-width="1.8"/>`;
  const L = S[S.length - 1];
  s += `<text class="lab" x="${ax.x(L.t) - 4}" y="${y(L.ce) - 6}" text-anchor="end" style="fill:var(--c5)">CE ${L.ce.toFixed(1)}</text>`;
  s += `<text class="lab" x="${ax.x(L.t) - 4}" y="${y(L.pe) + 14}" text-anchor="end" style="fill:var(--c4)">PE ${L.pe.toFixed(1)}</text>`;
  s += ax.ticks(H - 8);
  return svg(W, H, s, 'Call side and put side premium through the cycle');
}

// Each leg as a bar at its strike, from opened to closed: rolls show as
// one bar ending and the next starting at a new strike.
function ladder(cy) {
  const L = cy.legs.filter(l => l.strike != null);
  if (!L.length) return empty('', 'No strikes recorded for these legs.');
  const t0 = ms(cy.row.start), t1 = cy.row.end ? ms(cy.row.end) : Date.now();
  const ax = timeAxis(t0, t1, 70, WF - 120);
  const ks = [...new Set(L.map(l => l.strike))].sort((a, b) => b - a);
  const H = 30 + ks.length * 24, y = k => 14 + ks.indexOf(k) * 24;
  let s = '';
  ks.forEach(k => { s += `<line x1="70" x2="${WF - 12}" y1="${y(k) + 6}" y2="${y(k) + 6}" stroke="var(--line)"/><text x="64" y="${y(k) + 9}" text-anchor="end">${k}</text>`; });
  L.forEach(l => {
    const a = ax.x(l.opened_at), b = ax.x(l.closed_at || t1), c = l.opt === 'CE' ? 'var(--c5)' : 'var(--c4)';
    s += l.hedge
      ? `<rect x="${a}" y="${y(l.strike) + 3}" width="${Math.max(2, b - a)}" height="6" fill="none" stroke="${c}" stroke-dasharray="3 2"/>`
      : `<rect x="${a}" y="${y(l.strike)}" width="${Math.max(2, b - a)}" height="12" fill="${c}"/>`;
    s += `<text class="lab" x="${b + 4}" y="${y(l.strike) + 10}" style="font-size:10px">${l.opt}${l.hedge ? ' wing' : ''} ${l.status === 'open' ? '· open' : l.pnl != null ? `· ${inr(l.pnl)}` : ''}</text>`;
  });
  s += ax.ticks(H - 4);
  return wide(svg(WF, H, s, 'Each leg’s strike over time'));
}

function timeline(cy) {
  if (!cy.timeline.length) return empty('', 'Nothing yet.');
  let day = '';
  return html`<div class="cy-tl">${cy.timeline.map(e => {
    const d = dShort(e.t), first = d !== day;
    day = d;
    return html`${first ? html`<div class="cy-day">${d}</div>` : ''}
      <div class="cy-ev ${e.kind} ${e.outcome}"><span class="mono tiny">${hhmm(e.t)}</span><span class="oc ${e.outcome === 'acted' ? '' : e.outcome}">${e.outcome === 'acted' ? e.label : e.outcome === 'blocked' ? 'Held back' : 'No action'}</span>
        <span class="col" style="gap:1px;min-width:0"><b class="small">${e.summary}</b>${e.why ? html`<span class="tiny mute">${e.why}</span>` : e.kind === 'check' ? html`<span class="tiny mute">${e.label}</span>` : ''}</span></div>`;
  })}</div>`;
}

function entryBox(e) {
  if (!e) return empty('', 'This cycle’s entry isn’t in the fills.');
  return html`<div class="col" style="gap:8px">
    <p class="small" style="margin:0">${dStamp(e.t)} · ${e.trigger === 'initial_entry' ? 'first entry' : 're-entry after the last cycle'} · expiry <b>${e.contract_expiry || '—'}</b>${e.days_to_expiry != null ? ` · ${e.days_to_expiry} days out` : ''}${e.rotation ? ` · ${e.rotation.replace(/_/g, ' ').toLowerCase()}` : ''}</p>
    ${table([['Leg', '70px'], ['Strike', '80px', 'num'], ['Sold at', '90px', 'num'], ['Target', '90px', 'num'], ['Book', '90px', 'num'], ['Slippage', '80px', 'num']],
      e.legs.map(l => [html`<b>${l.opt}</b>`, l.strike ?? '—', price(l.price), l.target != null ? price(l.target) : '—', l.book_price != null ? price(l.book_price) : '—', l.slip_pts != null ? `${l.slip_pts} pts` : '—']), { minw: 0 })}
    ${e.why ? html`<p class="tiny mute" style="margin:0">Why: ${e.why}</p>` : ''}
  </div>`;
}

export default {
  render(el, v, g) {
    let data = null, sess = null, sessFor = null;   // the price chart, legs and orders, loaded once per cycle
    const c = g.params && g.params.c;
    function sessPanel() {
      return html`<div class="gk-panel" id="cySess"><h3>Price chart, legs and orders <span class="s">the index and the combined P&L on one time line · every leg’s final status · every order</span></h3>
        ${sess === null ? loading(4) : sess instanceof Error ? empty('Couldn’t load the price chart', sess.message) : sessionBody(book.find(v.id) || v, sess)}</div>`;
    }
    async function loadSess(cy) {
      const at = ms(cy.row.start);
      if (sessFor === at) return;
      sessFor = at; sess = null;
      try { sess = await loadSession(book.find(v.id) || v, at); } catch (e) { sess = e; }
      if (sessFor === at) paint();
    }
    function paint() {
      if (data === null) { mount(el, html`${head('Cycle')}${loading(8)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Cycle')}${empty('Couldn’t load this cycle', data.message)}`); return; }
      const cy = data;
      if (!cy.found) {
        mount(el, html`${head('Cycle')}${empty(cy.cycle_id == null ? 'No cycles yet' : `There’s no cycle ${cy.cycle_id}`, html`<a href="${g.href('cycles')}">Back to all cycles</a>`)}`);
        return;
      }
      const r = cy.row, m = cy.money;
      const result = r.status === 'open' ? m.open : r.pnl;
      const nav = (id, l) => (id == null ? html`<span class="btn btn-sm" aria-disabled="true">${l}</span>` : html`<a class="btn btn-sm" href="${g.href(`cycle?c=${id}`)}">${l}</a>`);
      mount(el, html`
        <div class="row wrap" style="gap:8px;align-items:center">
          <a class="small" href="${g.href('cycles')}">← All cycles</a><span class="grow"></span>
          <span class="mono tiny mute nowrap">${cy.ids.indexOf(cy.cycle_id) + 1} of ${cy.ids.length}</span>
          ${nav(cy.prev, '‹ Previous')}${nav(cy.next, 'Next ›')}
          <button type="button" class="btn btn-sm" data-act="toSess">Price chart, legs and orders ↓</button>
        </div>
        ${head(html`Cycle ${cy.cycle_id} ${endTag(r)}`, `${dStamp(r.start)} → ${r.end ? dStamp(r.end) : 'now'} · ${r.days} days · expiry ${r.contract_expiry || '—'}`, 'fills · checks · 5-minute marks · candles')}
        ${tiles([
          [r.status === 'open' ? 'P&L so far' : 'Result', html`<span class="${tone(result)}">${money(result)}</span>`, r.status === 'open' ? 'booked + open legs, gross' : `net ${inr(m.net)} after costs`],
          ['Checkpoint', `${result != null && cy.checkpoint_target ? Math.round(Math.max(0, result) / cy.checkpoint_target * 100) : 0}%`, `of ${inr(cy.checkpoint_target, { sign: false })}${result != null && result < 0 ? ' · in loss' : ''}`],
          ['Worst dip / best peak', html`<span class="${tone(r.mae)}">${money(r.mae)}</span> / <span class="${tone(r.mfe)}">${money(r.mfe)}</span>`, r.mae == null ? 'no prices stored for this cycle' : r.mae === 0 ? 'never below zero' : r.path_kind === 'candles' ? 'from 15-minute candle closes' : 'from the 5-minute marks', 'sm'],
          ['Rolls', r.rolls, `continuous ${r.rolls_by_kind.continuous} · EOD ${r.rolls_by_kind.eod} · roll-out ${r.rolls_by_kind.rollout}`],
          ['Premium sold', inr(m.premium_sold, { sign: false }), m.premium_sold && r.status !== 'open' ? `${(m.net / m.premium_sold * 100).toFixed(1)}% kept, net` : `${cy.legs.filter(l => !l.hedge).length} short legs`],
          ['Costs', inr(-(m.charges + m.slippage)), `charges ${inr(m.charges, { sign: false })} · slippage ${inr(m.slippage, { sign: false })}`],
        ], 6)}
        <div class="gk-panel"><h3>P&L through the cycle <span class="s">${cy.path_kind === 'booked' ? 'no prices were stored for this cycle: steps at what each leg booked' : `closed legs at what they booked, open legs at each ${cy.path_kind === 'candles' ? '15-minute candle close' : '5-minute mark'}`} · dotted: market open, no prices · dashed blue: rolls</span></h3>${pnlChart(cy)}</div>
        <div class="gk-panel"><h3>Journey <span class="s">this cycle only · P&L against days since it opened, as on Cycles · dashed green: checkpoint</span></h3>${journey(cy)}</div>
        ${sessPanel()}
        <div class="gk-two even">
          <div class="gk-panel"><h3>Side balance <span class="s">smaller ÷ bigger · shaded: where rolls aim</span></h3>${balanceChart(cy)}</div>
          <div class="gk-panel"><h3>Call and put premium <span class="s">each side’s open short legs</span></h3>${premiumChart(cy)}</div>
        </div>
        <div class="gk-panel"><h3>Strikes over time <span class="s">each leg from open to close · a roll ends one bar and starts another</span></h3>${ladder(cy)}</div>
        <div class="gk-two">
          <div class="gk-panel"><h3>Everything that happened <span class="s">${cy.timeline.length} actions and checks, oldest first</span></h3>${timeline(cy)}</div>
          <div class="col" style="gap:28px">
            <div class="gk-panel"><h3>How it was entered</h3>${entryBox(cy.entry)}</div>
            <div class="gk-panel"><h3>Where the money went <span class="s">closed legs, gross, then costs</span></h3>
              ${bars([['Original legs', m.by_role.original ?? null], ['Adjustment legs', m.by_role.adjustment ?? null], ['Hedges', m.by_role.hedge ?? null],
                ['Charges', -m.charges], ['Slippage', -m.slippage], [r.status === 'open' ? 'Booked so far, net' : 'Net', m.net]].filter(([, x]) => x !== null))}
              ${r.status === 'open' ? html`<p class="gk-note">Still open: ${money(m.open)} including the open legs at their latest price.</p>` : ''}</div>
          </div>
        </div>
        <div class="gk-panel"><h3>Rolls in this cycle <span class="s">each against keeping the leg it closed</span></h3>
          ${cy.rolls.length ? table([['When', '110px'], ['Rule', '110px'], ['Side', '70px'], ['Balance', '104px', 'num'], ['Closed → opened', 'minmax(200px,1.6fr)'], ['If held', '92px', 'num'], ['Roll paid', '100px', 'num']],
            cy.rolls.map(x => [html`<span class="mono tiny">${dStamp(x.t)}</span>`, html`<span class="oc">${LABEL[x.rule]}</span>`, `${x.side || ''} ${x.direction || ''}`,
              `${ratio(x.ratio_before)} → ${ratio(x.ratio_after)}`,
              html`<span class="mono tiny">${x.closed.map(f => `closed ${f.opt} ${f.strike ?? ''} @${Number(f.price).toFixed(1)}`).join(' · ')}${x.opened.length ? ' → ' : ''}${x.opened.map(f => `sold ${f.opt} ${f.strike ?? ''} @${Number(f.price).toFixed(1)}`).join(' · ')}</span>`,
              html`<span class="${tone(x.if_held)}">${money(x.if_held)}</span>`, html`<b class="${tone(x.roll_paid)}">${money(x.roll_paid)}</b>`]), { minw: 860 })
            : empty('', 'No rolls in this cycle.')}</div>
        <div class="gk-panel"><h3>Every leg <span class="s">${cy.legs.length} legs, in the order they were opened</span></h3>
          ${table([['Leg', 'minmax(120px,1fr)'], ['Role', '100px'], ['Opened', '110px'], ['Closed', '110px'], ['Entry', '76px', 'num'], ['Exit', '76px', 'num'], ['P&L', '96px', 'num'], ['Costs', '80px', 'num'], ['Closed by', '130px']],
            cy.legs.map(l => [html`<b class="mono small">${legTxt(l)}</b>${l.hedge ? html` <span class="tiny mute">wing</span>` : ''}`, html`<span class="tiny">${(l.role || '').replace(/_/g, ' ')}</span>`,
              html`<span class="mono tiny">${dStamp(l.opened_at)}</span>`, html`<span class="mono tiny">${l.closed_at ? dStamp(l.closed_at) : 'open'}</span>`,
              price(l.entry), l.exit != null ? price(l.exit) : '—', html`<span class="${tone(l.pnl)}">${money(l.pnl)}</span>`,
              l.charges != null ? inr(l.charges + l.slippage, { sign: false }) : '—', l.closed_by ? (LABEL[l.closed_by] || l.closed_by.replace(/_/g, ' ')) : '—']), { minw: 980 })}</div>`);
      loadSess(cy);
    }
    const off = actions(el, {
      toSess: () => { const t = el.querySelector('#cySess'); if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); },
    });
    const stop = poll(() => api.geek(g.id, `cycle${c ? `?c=${encodeURIComponent(c)}` : ''}`), 60000, d => { data = d; paint(); });
    return () => { stop(); off(); };
  },
};
