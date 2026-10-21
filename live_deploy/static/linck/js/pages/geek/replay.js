// Replay (#/d/:id/geek/replay?from=N&to=M): a cycle — or a continuous
// chain of them — played back bar by bar, like a market replay. Nifty's
// candles march in from the right with each open leg's strike on the
// chart (or how far away it is), every event flagged on its bar; under it
// the P&L — the total and each leg on its own line — and India VIX; under
// that every position, appearing as it's opened, with what opened it,
// where it stands and how it closed. Play stops at each event by default.
// Keys: Space play/pause · ← → one bar · [ ] previous / next event.

import { api } from '../../core/api.js';
import { html, mount, actions, on } from '../../core/dom.js';
import { inr, dStamp, dShort, hhmm, price } from '../../core/format.js';
import { go } from '../../core/router.js';
import { empty, loading, fixTag, REPAIRED_TIP } from '../../ui/components.js';
import { head, table, svg, money, tone } from './ui.js';

const SPEEDS = [[2, '½×'], [5, '1×'], [15, '3×'], [40, '8×']];
const ZOOMS = [[60, '60 bars'], [120, '120'], [240, '240'], [0, 'All']];
export const KIND = {
  new: ['New cycle', 'var(--ink)', 'NEW'], entry: ['Entry', 'var(--ink)', 'IN'], adjustment: ['Adjustment', 'var(--b)', 'ADJ'],
  roll: ['Roll', 'var(--c5)', 'ROLL'], hedge: ['Hedge', 'var(--mute)', 'HEDGE'], added: ['Leg added', 'var(--b)', '+LEG'],
  leg_closed: ['Leg closed', 'var(--wr)', 'CLOSE'], exit: ['Exit', 'var(--ink)', 'EXIT'],
};
const kindOf = k => KIND[k] || KIND.entry;
const legColor = l => (l.kind === 'CE' ? 'var(--c5)' : l.kind === 'PE' ? 'var(--c4)' : 'var(--b)');
const legName = l => (l.kind && l.strike ? `${l.kind} ${l.strike}` : l.symbol);
const sgn = (v, dp = 0) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: dp, minimumFractionDigits: dp })}`);
const attr = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

// ── pure helpers (tested) ──────────────────────────────────────────────

export function eventAt(events, i) {
  let e = null;
  for (const x of events) { if (x.frame <= i) e = x; else break; }
  return e;
}
export const nextEvent = (events, i) => events.find(e => e.frame > i) || null;
export function prevEvent(events, i) {
  const before = events.filter(e => e.frame < i);
  return before.length ? before[before.length - 1] : null;
}
export function advance(i, n, events, pauseAtEvents) {
  const j = Math.min(n - 1, i + 1);
  if (pauseAtEvents) {
    const e = events.find(x => x.frame > i && x.frame <= j);
    if (e) return { i: e.frame, stop: true, event: e };
  }
  return { i: j, stop: j >= n - 1, event: null };
}
export function change(frames, a, b, k) {
  const x = frames[a] && frames[a][k], y = frames[b] && frames[b][k];
  return x == null || y == null ? null : y - x;
}

// The bars on screen at playhead i: the last `zoom` up to i (all when 0).
export const windowOf = (i, n, zoom) => (zoom ? [Math.max(0, i - zoom + 1), i] : [0, n - 1]);

// The bar a time falls in (the last one starting at or before it).
export function frameOf(frames, t) {
  const ms = new Date(t).getTime();
  let k = 0;
  for (let j = 0; j < frames.length; j++) { if (new Date(frames[j].t).getTime() <= ms) k = j; else break; }
  return k;
}

const endOf = (D, i) => (D.frames[i + 1] ? new Date(D.frames[i + 1].t).getTime() : Infinity);
const msOf = t => (t ? new Date(t).getTime() : null);

// Leg j's P&L at bar i: null before it opened, what it booked once closed,
// else its open P&L then.
export function legPnl(D, j, i) {
  const l = D.legs[j], f = D.frames[i];
  const open = f.legs.find(x => x[0] === j);
  if (open) return open[2];
  if (l.t1 && msOf(l.t1) < endOf(D, i)) return l.realized;
  return null;
}

// The positions table at bar i: every leg opened by then, oldest first.
export function positionsAt(D, i) {
  const until = endOf(D, i), f = D.frames[i];
  return D.legs.map((l, j) => ({ l, j })).filter(({ l }) => msOf(l.t0) < until).map(({ l, j }) => {
    const open = f.legs.find(x => x[0] === j);
    const closed = !open && !!l.t1 && msOf(l.t1) < until;
    return {
      j, l, closed, now: open ? open[1] : closed ? l.exit : null,
      pnl: open ? open[2] : closed ? l.realized : null, stale: open ? open[3] : false,
      fresh: frameOf(D.frames, l.t0) === i, justClosed: closed && frameOf(D.frames, l.t1) === i,
    };
  });
}

// ── the stage: candles, strikes, flags, P&L per leg, VIX ───────────────

const W = 1200, LX = 8, RX = 1104;
const PANES = { px: [8, 300], pnl: [326, 210], vix: [554, 74] };
const H = 650;

function yScale(vals, [top, h], extra = 0.06) {
  const v = vals.filter(x => x != null && Number.isFinite(x));
  let lo = v.length ? Math.min(...v) : 0, hi = v.length ? Math.max(...v) : 1;
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * extra;
  lo -= pad; hi += pad;
  return { y: x => top + (hi - x) / (hi - lo) * h, lo, hi };
}

function ticks(sc, n, fmt) {
  let s = '';
  for (let k = 1; k < n; k++) {
    const v = sc.lo + (sc.hi - sc.lo) * k / n, Y = sc.y(v);
    s += `<line x1="${LX}" x2="${RX}" y1="${Y}" y2="${Y}" stroke="var(--line)" stroke-width=".6"/><text x="${RX + 6}" y="${Y + 3}">${fmt(v)}</text>`;
  }
  return s;
}

// The current value as a tag at the right edge, like a trading chart.
const tag = (Y, text, bg) => `<rect x="${RX + 2}" y="${Y - 9}" width="${W - RX - 4}" height="18" fill="${bg}"/><text x="${RX + 6}" y="${Y + 4}" style="fill:var(--paper);font-weight:700">${text}</text>`;

export function stage(D, i, zoom, hover) {
  const F = D.frames, n = F.length;
  const [a, b] = windowOf(i, n, zoom);
  const bw = (RX - LX) / Math.max(zoom || n, 1);
  const x = k => LX + (k - a + 0.5) * bw;
  const vis = F.slice(a, Math.min(b, i) + 1);    // nothing after the playhead
  const cur = F[i];
  let s = '';
  for (const [top, h] of Object.values(PANES)) s += `<rect x="${LX}" y="${top}" width="${RX - LX}" height="${h}" fill="none" stroke="var(--line)"/>`;

  // ── price: candles, open legs' strikes, event flags
  const spx = yScale(vis.flatMap(f => [f.h ?? f.idx, f.l ?? f.idx, f.fut]), PANES.px);
  s += ticks(spx, 5, v => Math.round(v).toLocaleString('en-IN'));
  let lastDay = '', lastLabel = -99;
  vis.forEach((f, k) => {
    const d = dShort(f.t).replace(/ '\d\d$/, '');
    if (d === lastDay) return;
    const X = x(a + k) - bw / 2;
    if (lastDay) s += `<line x1="${X}" x2="${X}" y1="${PANES.px[0]}" y2="${PANES.vix[0] + PANES.vix[1]}" stroke="var(--line2)" stroke-dasharray="2 3"/>`;
    if (X - lastLabel > 54) { s += `<text x="${X + 3}" y="${H - 4}">${d}</text>`; lastLabel = X; }   // never on top of the last one
    lastDay = d;
  });
  if (vis.some(f => f.o != null)) {
    const cw = Math.max(1, Math.min(9, bw * 0.7));
    vis.forEach((f, k) => {
      if (f.o == null || f.idx == null) return;
      const X = x(a + k), up = f.idx >= f.o, c = up ? 'var(--g)' : 'var(--r)';
      const y1 = spx.y(Math.max(f.o, f.idx)), y2 = spx.y(Math.min(f.o, f.idx));
      s += `<line x1="${X}" x2="${X}" y1="${spx.y(f.h)}" y2="${spx.y(f.l)}" stroke="${c}"/>`;
      s += `<rect x="${X - cw / 2}" y="${y1}" width="${cw}" height="${Math.max(1, y2 - y1)}" fill="${up ? 'var(--paper)' : c}" stroke="${c}"/>`;
    });
  } else {
    const pts = vis.map((f, k) => (f.idx == null ? null : `${x(a + k).toFixed(1)} ${spx.y(f.idx).toFixed(1)}`)).filter(Boolean);
    if (pts.length > 1) s += `<path d="M${pts.join('L')}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>`;
  }
  // the future that prices the legs: a dashed line over the candles
  const fpts = vis.map((f, k) => (f.fut == null ? null : `${x(a + k).toFixed(1)} ${spx.y(f.fut).toFixed(1)}`)).filter(Boolean);
  if (fpts.length > 1) s += `<path d="M${fpts.join('L')}" fill="none" stroke="var(--b)" stroke-width="1.4" stroke-dasharray="5 3"/>`;
  D.legs.forEach((l, j) => {
    if (l.strike == null || !cur.legs.some(q => q[0] === j)) return;
    const c = legColor(l), from = Math.max(a, frameOf(F, l.t0));
    // the distance that matters is from the future (options are priced off it), else from the spot
    const ref = cur.fut ?? cur.idx, dist = ref != null ? l.strike - ref : null;
    const label = `${legName(l)} ${l.side === 'short' ? 'sold' : 'bought'}${dist != null ? ` · ${sgn(dist)} pts${cur.fut != null ? ' from fut' : ''}` : ''}`;
    if (l.strike >= spx.lo && l.strike <= spx.hi) {
      const Y = spx.y(l.strike);
      s += `<line x1="${x(from) - bw / 2}" x2="${RX}" y1="${Y}" y2="${Y}" stroke="${c}" stroke-width="1.6"${l.side === 'long' ? ' stroke-dasharray="5 3"' : ''}/>`;
      s += `<text x="${RX - 6}" y="${Y - 4}" text-anchor="end" style="fill:${c};font-weight:700;font-size:11px">${label}</text>`;
    } else {
      const above = l.strike > spx.hi, Y = above ? PANES.px[0] + 32 + j * 14 : PANES.px[0] + PANES.px[1] - 6 - j * 14;
      s += `<text x="${RX - 6}" y="${Y}" text-anchor="end" style="fill:${c};font-weight:700;font-size:11px">${above ? '▲' : '▼'} ${label}</text>`;
    }
  });
  for (const e of D.events) {
    if (e.frame < a || e.frame > i) continue;
    const X = x(e.frame), [lab, c, short0] = kindOf(e.kind), short = e.repaired ? `${short0} R` : short0, tw = short.length * 7 + 8;
    s += `<line x1="${X}" x2="${X}" y1="${PANES.px[0] + 17}" y2="${PANES.vix[0] + PANES.vix[1]}" stroke="${c}" stroke-dasharray="3 3" opacity=".8"/>`;
    s += `<rect x="${X - tw / 2}" y="${PANES.px[0] + 2}" width="${tw}" height="15" fill="${c}"/><text x="${X}" y="${PANES.px[0] + 13}" text-anchor="middle" style="fill:var(--paper);font-weight:700;font-size:10px">${short}</text>`;
    s += `<rect class="hit" x="${X - Math.max(5, tw / 2)}" y="${PANES.px[0]}" width="${Math.max(10, tw)}" height="18" fill="transparent" data-tip="${attr(`${lab} · ${dStamp(e.t)}\n${e.rule}${e.booked != null ? `\nBooked  ${inr(e.booked)}` : ''}${e.repaired ? `\n${REPAIRED_TIP}` : ''}`)}"/>`;
  }
  if (cur.idx != null) s += tag(spx.y(Math.max(spx.lo, Math.min(spx.hi, cur.idx))), price(cur.idx), 'var(--ink)');
  if (cur.fut != null) {
    const Yf = spx.y(Math.max(spx.lo, Math.min(spx.hi, cur.fut))), Yi = cur.idx != null ? spx.y(cur.idx) : -99;
    s += tag(Math.abs(Yf - Yi) < 19 ? Yi + (Yf < Yi ? -19 : 19) : Yf, price(cur.fut), 'var(--b)');
  }
  s += `<text x="${LX + 6}" y="${PANES.px[0] + 30}" style="font-weight:700;fill:var(--ink);font-size:12px">${D.index_name}${D.future ? `<tspan style="font-weight:500;fill:var(--b)">  · dashed: ${D.future.symbol}</tspan>` : ''}</text>`;

  // ── P&L: the total, each leg, what's booked
  const legVals = D.legs.map((l, j) => vis.map((f, k) => legPnl(D, j, a + k)));
  const totals = vis.map(f => f.pnl), booked = vis.map(f => f.booked);
  const sp = yScale([0, ...totals, ...booked, ...legVals.flat()], PANES.pnl, 0.1);
  s += ticks(sp, 4, v => inr(Math.round(v)));
  s += `<line x1="${LX}" x2="${RX}" y1="${sp.y(0)}" y2="${sp.y(0)}" stroke="var(--ink)" stroke-width=".8"/>`;
  const line = (vals, c, w, dash = '') => {
    let d = '', pen = false;
    vals.forEach((v, k) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(a + k).toFixed(1)} ${sp.y(v).toFixed(1)}`; pen = true; });
    return d ? `<path d="${d}" fill="none" stroke="${c}" stroke-width="${w}"${dash}/>` : '';
  };
  legVals.forEach((vals, j) => {
    const l = D.legs[j];
    s += line(vals, legColor(l), 1.3, l.t1 && msOf(l.t1) < endOf(D, i) ? ' stroke-dasharray="2 3"' : '');
    const last = vals[vals.length - 1];
    if (last != null && cur.legs.some(q => q[0] === j)) s += `<text x="${RX - 6}" y="${sp.y(last) - 3}" text-anchor="end" style="fill:${legColor(l)};font-size:10px;font-weight:700">${legName(l)} ${inr(last)}</text>`;
  });
  s += line(booked, 'var(--mute)', 1.2, ' stroke-dasharray="6 4"');
  s += line(totals, 'var(--ink)', 2.4);
  s += tag(sp.y(cur.pnl), inr(cur.pnl), cur.pnl >= 0 ? 'var(--g)' : 'var(--r)');
  s += `<text x="${LX + 6}" y="${PANES.pnl[0] + 14}" style="font-weight:700;fill:var(--ink);font-size:12px">P&amp;L <tspan style="font-weight:500;fill:var(--mute)">· bold: total · thin: each leg (dotted once closed) · dashed: booked</tspan></text>`;

  // ── VIX
  const sv = yScale(vis.map(f => f.vix), PANES.vix, 0.15);
  const vpts = vis.map((f, k) => (f.vix == null ? null : `${x(a + k).toFixed(1)} ${sv.y(f.vix).toFixed(1)}`)).filter(Boolean);
  if (vpts.length > 1) s += `<path d="M${vpts.join('L')}" fill="none" stroke="var(--wr)" stroke-width="1.5"/>`;
  if (cur.vix != null) s += tag(sv.y(cur.vix), cur.vix.toFixed(2), 'var(--wr)');
  s += `<text x="${LX + 6}" y="${PANES.vix[0] + 14}" style="font-weight:700;fill:var(--ink);font-size:12px">India VIX</text>`;

  const hv = hover ? {
    series: [
      { name: D.index_name, color: 'var(--ink)', pts: vis.map((f, k) => [x(a + k), f.idx == null ? spx.y(spx.lo) : spx.y(f.idx), f.o != null ? `${price(f.idx)}  O ${price(f.o)} H ${price(f.h)} L ${price(f.l)}` : price(f.idx), dStamp(f.t)]) },
      ...(D.future ? [{ name: 'Future', color: 'var(--b)', pts: vis.map((f, k) => (f.fut == null ? null : [x(a + k), spx.y(f.fut), `${price(f.fut)}${f.idx != null ? `  basis ${sgn(f.fut - f.idx)}` : ''}`])).filter(Boolean) }] : []),
      { name: 'P&L', color: 'var(--ink)', pts: vis.map((f, k) => [x(a + k), sp.y(f.pnl), inr(f.pnl)]) },
      ...D.legs.map((l, j) => ({ name: legName(l), color: legColor(l), pts: legVals[j].map((v, k) => (v == null ? null : [x(a + k), sp.y(v), inr(v)])).filter(Boolean) })).filter(s2 => s2.pts.length),
      { name: 'VIX', color: 'var(--wr)', pts: vis.map((f, k) => [x(a + k), f.vix == null ? sv.y(sv.lo) : sv.y(f.vix), f.vix == null ? '—' : f.vix.toFixed(2)]) },
    ],
  } : null;
  return svg(W, H, s, 'Replay: the index, each position’s P&L and India VIX, bar by bar', hv);
}

// ── around the stage ───────────────────────────────────────────────────

function strip(D, i) {
  const F = D.frames, f = F[i], e = eventAt(D.events, i), from = e ? e.frame : 0;
  const cell = (l, v, sub, cls = '') => html`<div class="rp-cell"><span>${l}</span><b class="${cls}">${v}</b>${sub ? html`<i>${sub}</i>` : ''}</div>`;
  return html`<div class="rp-strip">
    ${cell(dStamp(f.t), `bar ${i + 1} / ${F.length}`, '')}
    ${cell(D.index_name, f.idx == null ? '—' : price(f.idx), `${sgn(change(F, 0, i, 'idx'))} since start · ${sgn(change(F, from, i, 'idx'))} since event`)}
    ${D.future ? cell(`Future · ${D.future.symbol}`, f.fut == null ? '—' : price(f.fut), f.fut != null && f.idx != null ? `basis ${sgn(f.fut - f.idx)} pts · ${sgn(change(F, 0, i, 'fut'))} since start` : '') : ''}
    ${cell('India VIX', f.vix == null ? '—' : f.vix.toFixed(2), `${sgn(change(F, 0, i, 'vix'), 2)} since start · ${sgn(change(F, from, i, 'vix'), 2)} since event`)}
    ${cell('P&L', inr(f.pnl), `booked ${inr(f.booked)} · open ${inr(f.pnl - f.booked)}`, tone(f.pnl))}
    ${f.split ? html`<div class="rp-cell rp-from"><span>Came from</span>${[['Time decay', 0], [D.future ? 'Future move' : `${D.index_name} move`, 1], ['Volatility', 2]].map(([l, k]) => html`<div><i>${l}</i><b class="${tone(f.split[k])}">${inr(f.split[k])}</b></div>`)}</div>` : ''}
  </div>`;
}

function banner(D, i) {
  const e = eventAt(D.events, i);
  if (!e) return html`<div class="rp-banner mute small">Before the first event.</div>`;
  const [lab, c] = kindOf(e.kind), here = e.frame === i;
  return html`<div class="rp-banner ${here ? 'here' : ''}" style="--k:${c}">
    <span class="rp-chip" style="--k:${c}">${lab}${e.cycle != null && e.kind === 'new' ? ` ${e.cycle}` : ''}</span>
    <b class="small">${e.rule}</b> ${fixTag(e.repaired)}
    <span class="mono tiny">${dStamp(e.t)}${here ? '' : ` · ${i - e.frame} bar${i - e.frame === 1 ? '' : 's'} ago`}</span>
    ${e.booked != null ? html`<span class="small">booked <b class="${tone(e.booked)}">${inr(e.booked)}</b></span>` : ''}
    ${e.since ? html`<span class="tiny">since the last event: ${D.index_name} <b>${sgn(e.since.idx)}</b> · VIX <b>${sgn(e.since.vix, 2)}</b> · P&L <b class="${tone(e.since.pnl)}">${e.since.pnl == null ? '—' : inr(e.since.pnl)}</b></span>` : ''}
    ${e.closed_before ? html`<span class="tiny">first closed ${e.closed_before.cycle != null ? `cycle ${e.closed_before.cycle}` : 'the episode before'}${e.closed_before.booked != null ? html`, booking <b class="${tone(e.closed_before.booked)}">${inr(e.closed_before.booked)}</b>` : ''}</span>` : ''}
    ${e.why ? html`<span class="tiny mute rp-why">saw: ${e.why}</span>` : ''}
  </div>`;
}

function positions(D, i) {
  const P = positionsAt(D, i);
  if (!P.length) return html`<p class="small mute" style="margin:0">No position yet at this bar.</p>`;
  const open = P.filter(p => !p.closed), closed = P.filter(p => p.closed);
  const sum = xs => xs.reduce((t, p) => t + (p.pnl || 0), 0);
  const by = t => (t ? html`<span class="rp-chip sm" style="--k:${kindOf(t.kind)[1]}">${t.label || kindOf(t.kind)[0]}</span> <span class="tiny mute">${t.rule}</span>` : '—');
  const rows = P.map(p => [
    html`<b class="mono small" style="color:${legColor(p.l)}">${legName(p.l)}</b> ${fixTag(p.l.repaired)}`,
    html`<span class="small">${p.l.side === 'short' ? 'Sold' : 'Bought'} ${p.l.qty}</span>`,
    html`<span class="mono tiny">${dStamp(p.l.t0)}</span>`, by(p.l.opened_by),
    price(p.l.entry), p.l.s0 ? price(p.l.s0) : '—', ...(D.future ? [p.l.f0 ? price(p.l.f0) : '—'] : []),
    html`<span class="${p.stale ? 'mute' : ''}">${p.now == null ? '—' : price(p.now)}</span>`,
    p.closed ? html`<span class="small">Closed ${hhmm(p.l.t1)} · ${p.l.closed_by ? p.l.closed_by.label || p.l.closed_by.rule : ''}</span>` : html`<b class="small">Open</b>`,
    html`<b class="${tone(p.pnl)}">${p.pnl == null ? '—' : inr(p.pnl)}</b>`,
  ]);
  rows.push([html`<b class="small">Total</b>`, html`<span class="tiny mute">${open.length} open · ${closed.length} closed</span>`, '', '', '', '', ...(D.future ? [''] : []), '',
    html`<span class="tiny">open ${inr(sum(open))} · booked ${inr(sum(closed))}</span>`, html`<b class="${tone(sum(P))}">${inr(sum(P))}</b>`]);
  return table([['Leg', 'minmax(96px,.9fr)'], ['Side', '82px'], ['Opened', '110px'], ['By', 'minmax(150px,1.3fr)'], ['Entry', '72px', 'num'], [`${D.index_name} then`, '86px', 'num'], ...(D.future ? [['Future then', '86px', 'num']] : []), ['Now / exit', '80px', 'num'], ['Status', 'minmax(140px,1fr)'], ['P&L', '92px', 'num']],
    rows, { minw: 980, rowCls: k => (k === P.length ? 'rp-total' : P[k].fresh ? 'rp-fresh' : P[k].justClosed ? 'rp-gone' : P[k].closed ? 'rp-closed' : '') });
}

function eventList(D, i) {
  let day = '';
  const cur = eventAt(D.events, i);
  return html`<div class="rp-list">${D.events.map(e => {
    const d = dShort(e.t), first = d !== day; day = d;
    const [lab, c] = kindOf(e.kind);
    return html`${first ? html`<div class="cy-day">${d}</div>` : ''}<button type="button" class="rp-li ${e.frame > i ? 'later' : ''} ${cur === e ? 'on' : ''}" data-act="jump" data-f="${e.frame}">
      <span class="mono tiny">${hhmm(e.t)}</span><span class="rp-chip" style="--k:${c}">${lab}</span>
      <span class="small" style="min-width:0">${e.rule}${e.booked != null ? html` · <span class="${tone(e.booked)}">${inr(e.booked)}</span>` : ''} ${fixTag(e.repaired)}</span></button>`;
  })}</div>`;
}

// ── the view ───────────────────────────────────────────────────────────

export default {
  render(el, v, g) {
    const p = g.params || {};
    let D = null, i = 0, playing = false, timer = null, speed = 1, pauseAt = true, zoom = 120;

    function picker() {
      const E = D.episodes, [a, z] = D.selected;
      const opt = sel => E.map(e => html`<option value="${e.n}" ${e.n === sel ? 'selected' : ''}>${e.label} · ${dShort(e.t0)}${e.open ? ' · open' : ''}${e.repaired ? ' · repaired' : ''}</option>`);
      return html`<div class="rp-pick">
        <label class="small">From <select data-pick="from">${opt(a)}</select></label>
        <label class="small">to <select data-pick="to">${opt(z)}</select></label>
        <span class="tiny mute">${z > a ? `a chain of ${z - a + 1}, played as one` : 'one episode'} · ${D.interval === 'minute' ? '1-minute' : D.interval.replace('minute', '-minute')} bars · nights and weekends skipped</span>
      </div>`;
    }

    function episodes() {
      const E = [...D.episodes].reverse().slice(0, 40), [a, z] = D.selected;
      return table([['', 'minmax(110px,1fr)'], ['Opened', '110px'], ['Closed', '110px'], ['Moves', '64px', 'num'], ['Booked', '96px', 'num']],
        E.map(e => [html`<b class="small">${e.label}</b> ${fixTag(e.repaired)}${e.n >= a && e.n <= z ? html` <span class="tiny mute">· replaying</span>` : ''}`, html`<span class="mono tiny">${dStamp(e.t0)}</span>`,
          html`<span class="mono tiny">${e.open ? 'open' : dStamp(e.t1)}</span>`, String(e.adjustments), html`<span class="${tone(e.booked)}">${money(e.booked)}</span>`]),
        { minw: 0, rowAttrs: k => html`data-ep="${E[k].n}" role="button" tabindex="0" style="cursor:pointer"` });
    }

    function controls() {
      const n = D.frames.length;
      return html`<div class="rp-ctl">
        <button type="button" class="btn btn-sm" data-act="to" data-f="0" aria-label="To the start">⏮</button>
        <button type="button" class="btn btn-sm" data-act="prevEv" aria-label="Previous event">◀◀</button>
        <button type="button" class="btn btn-sm rp-play" data-act="play" aria-label="Play">▶ Play</button>
        <button type="button" class="btn btn-sm" data-act="nextEv" aria-label="Next event">▶▶</button>
        <button type="button" class="btn btn-sm" data-act="to" data-f="${n - 1}" aria-label="To the end">⏭</button>
        <span class="rp-seg" data-seg="speed">${SPEEDS.map(([, l], k) => html`<button type="button" class="${k === speed ? 'on' : ''}" data-act="speed" data-s="${k}">${l}</button>`)}</span>
        <span class="rp-seg" data-seg="zoom" title="bars on screen">${ZOOMS.map(([z, l]) => html`<button type="button" class="${z === zoom ? 'on' : ''}" data-act="zoom" data-z="${z}">${l}</button>`)}</span>
        <label class="small row" style="gap:6px"><input type="checkbox" data-pause ${pauseAt ? 'checked' : ''}> Stop at each event</label>
        <input class="rp-range" type="range" min="0" max="${n - 1}" value="${i}" data-range aria-label="Replay position">
        <span class="tiny mute">Space · ← → · [ ]</span>
      </div>`;
    }

    function paint() {
      if (!D) { mount(el, html`${head('Replay', 'how a cycle went, bar by bar')}${loading(6)}`); return; }
      if (D instanceof Error) { mount(el, html`${head('Replay')}${empty('The replay didn’t load', D.message)}`); return; }
      if (!D.episodes.length) { mount(el, html`${head('Replay')}${empty('Nothing to replay yet', 'A replay appears once this deployment has traded.')}`); return; }
      if (!D.frames || !D.frames.length) { mount(el, html`${head('Replay')}${picker()}${empty('No bars for this window', D.market_error || 'No prices were found for it.')}`); return; }
      i = Math.max(0, Math.min(D.frames.length - 1, i));
      mount(el, html`${head('Replay', `${D.index_name} bar by bar, every position as it opens and closes`, D.market_error ? 'index and VIX unavailable' : '')}
        ${picker()}
        ${controls()}
        <div data-rp="strip"></div>
        <div data-rp="banner"></div>
        <div class="rp-stage gk-wide" data-rp="stage"></div>
        <div class="gk-panel"><h3>Positions <span class="s">each appears as it’s opened · new: highlighted · closed: greyed, with how it closed</span></h3><div data-rp="pos"></div></div>
        <div class="gk-two">
          <div class="gk-panel"><h3>Every event <span class="s">${D.events.length} · click one to jump to it</span></h3><div data-rp="list"></div></div>
          <div class="gk-panel"><h3>Episodes <span class="s">click one to replay it · From and To above for a chain</span></h3>${episodes()}</div>
        </div>
        ${D.unpriced && D.unpriced.length ? html`<p class="gk-note">No stored prices for ${D.unpriced.join(', ')}: shown at entry until they close.</p>` : ''}`);
      update();
    }

    function update() {
      if (!D || !D.frames || !D.frames.length) return;
      const set = (k, h) => { const t = el.querySelector(`[data-rp="${k}"]`); if (t) mount(t, h); };
      set('strip', strip(D, i));
      set('banner', banner(D, i));
      set('stage', stage(D, i, zoom, !playing));
      // on a narrow screen the chart scrolls: keep the newest bar in view
      const st = el.querySelector('.rp-stage');
      if (st && st.scrollWidth > st.clientWidth) st.scrollLeft = st.scrollWidth;
      set('pos', positions(D, i));
      set('list', eventList(D, i));
      const r = el.querySelector('[data-range]');
      if (r && +r.value !== i) r.value = i;
      const b = el.querySelector('.rp-play');
      if (b) { b.textContent = playing ? '❚❚ Pause' : '▶ Play'; b.setAttribute('aria-label', playing ? 'Pause' : 'Play'); }
    }

    function stop() { playing = false; if (timer) clearInterval(timer); timer = null; update(); }
    function play() {
      if (!D || !D.frames) return;
      if (i >= D.frames.length - 1) i = 0;
      playing = true;
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        const s = advance(i, D.frames.length, D.events, pauseAt);
        i = s.i;
        if (s.stop) stop(); else update();
      }, 1000 / SPEEDS[speed][0]);
      update();
    }
    const to = j => { i = Math.max(0, Math.min(D.frames.length - 1, j)); update(); };
    const load = (a, z) => go(`d/${g.id}/geek/replay?from=${a}&to=${z}`);
    const seg = (name, key, val) => el.querySelectorAll(`[data-seg="${name}"] button`).forEach(b => b.classList.toggle('on', +b.dataset[key] === val));

    const off = actions(el, {
      play: () => (playing ? stop() : play()),
      to: d => { stop(); to(+d.f); },
      jump: d => { stop(); to(+d.f); },
      nextEv: () => { stop(); const e = nextEvent(D.events, i); to(e ? e.frame : D.frames.length - 1); },
      prevEv: () => { stop(); const e = prevEvent(D.events, i); to(e ? e.frame : 0); },
      speed: d => { speed = +d.s; seg('speed', 's', speed); if (playing) play(); },
      zoom: d => { zoom = +d.z; seg('zoom', 'z', zoom); update(); },
    });
    const offs = [
      on(el, 'input', '[data-range]', (e, t) => { stop(); to(+t.value); }),
      on(el, 'change', '[data-pause]', (e, t) => { pauseAt = t.checked; }),
      on(el, 'change', '[data-pick]', () => {
        const a = +el.querySelector('[data-pick="from"]').value, z = +el.querySelector('[data-pick="to"]').value;
        load(Math.min(a, z), Math.max(a, z));
      }),
      on(el, 'click', '[data-ep]', (e, t) => load(+t.dataset.ep, +t.dataset.ep)),
      on(el, 'keydown', '[data-ep]', (e, t) => { if (e.key === 'Enter') load(+t.dataset.ep, +t.dataset.ep); }),
      on(document, 'keydown', 'body', e => {
        if (!D || !D.frames || e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, textarea, select, [contenteditable]')) return;
        if (e.key === ' ') { e.preventDefault(); playing ? stop() : play(); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); stop(); to(i + 1); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); stop(); to(i - 1); }
        else if (e.key === ']') { e.preventDefault(); stop(); const x = nextEvent(D.events, i); if (x) to(x.frame); }
        else if (e.key === '[') { e.preventDefault(); stop(); const x = prevEvent(D.events, i); if (x) to(x.frame); }
      }),
    ];

    paint();
    const q = [p.from && `from=${encodeURIComponent(p.from)}`, p.to && `to=${encodeURIComponent(p.to)}`].filter(Boolean).join('&');
    let alive = true;
    api.geek(g.id, `replay${q ? `?${q}` : ''}`).then(d => { if (alive) { D = d; i = 0; paint(); } },
      e => { if (alive) { D = e instanceof Error ? e : new Error(String(e)); paint(); } });
    return () => { alive = false; if (timer) clearInterval(timer); off(); offs.forEach(f => f()); };
  },
};
