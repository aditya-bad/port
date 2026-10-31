// Replay (#/d/:id/geek/replay?from=N&to=M): a cycle — or a chain of them —
// played back the way Opstra and Sensibull replay a position. The payoff
// chart shows the P&L the open positions would make at every level of the
// index: at expiry (shaded profit and loss) and today (from each leg's
// price at that moment), with where the index and its future stand. Under
// it every position: new ones join as they're opened, closed ones grey out
// with what they booked. Step 1m, 5m, 15m, 30m, 1h or a day at a time (as
// fine as the stored prices go), or play it. The controls, the payoff and
// the positions share one screen: the chart's height follows the window,
// and on a phone a bar at the bottom keeps play and step in reach.
// Keys: Space play/pause · ← → one step · [ ] previous / next trade.
// Explain plays the replay told in words (app/geek/explain.py): the playhead
// goes to each moment, the device's voice says what happened and why, and
// the same words show as a caption.

import { api } from '../../core/api.js';
import { sentences, readMs, glide, pickVoice } from '../../data/explain.js';
import { html, mount, actions, on } from '../../core/dom.js';
import { inr, dStamp, dShort, hhmm, price, ist } from '../../core/format.js';
import { go } from '../../core/router.js';
import { empty, loading, fixTag } from '../../ui/components.js';
import { bs, black76, intrinsic, yearsTo } from '../../data/options.js';
import { head, table, svg, money, tone } from './ui.js';

export const KIND = {
  new: ['New cycle', 'var(--ink)'], entry: ['Entry', 'var(--ink)'], adjustment: ['Adjustment', 'var(--b)'],
  roll: ['Roll', 'var(--c5)'], hedge: ['Hedge', 'var(--mute)'], added: ['Leg added', 'var(--b)'],
  leg_closed: ['Leg closed', 'var(--wr)'], exit: ['Exit', 'var(--ink)'],
};
const kindOf = k => KIND[k] || KIND.entry;
const legColor = l => (l.kind === 'CE' ? 'var(--c5)' : l.kind === 'PE' ? 'var(--c4)' : 'var(--b)');
const legName = l => (l.kind && l.strike ? `${l.kind} ${l.strike}` : l.symbol);
const sgn = (v, dp = 0) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: dp, minimumFractionDigits: dp })}`);
const ms = t => new Date(t).getTime();
const isOpt = l => l.kind === 'CE' || l.kind === 'PE';

// ── steps (tested) ─────────────────────────────────────────────────────

const BAR = { minute: 1, '5minute': 5, '15minute': 15, '60minute': 60 };
export const STEPS = [['1m', 1], ['5m', 5], ['15m', 15], ['30m', 30], ['1h', 60], ['1D', 0]];   // 0: a trading day
// The step sizes a replay can take: whole multiples of its bars, and a day.
export const stepsFor = interval => {
  const b = BAR[interval] || 1;
  return STEPS.filter(([, m]) => m === 0 || (m >= b && m % b === 0));
};
export const barMinutes = interval => BAR[interval] || 1;

// The step `m` minutes (0: a trading day) forward (dir 1) or back (−1)
// from step i. Nights and weekends aren't steps: 30 minutes on from 15:20
// is the next morning's open. A day keeps the time of day where it can.
export function stepTo(frames, i, m, dir = 1) {
  const n = frames.length;
  if (!n) return 0;
  if (m === 0) return dayStep(frames, i, dir);
  const target = ms(frames[i].t) + dir * m * 60000;
  if (dir > 0) {
    for (let j = i + 1; j < n; j++) if (ms(frames[j].t) >= target) return j;
    return n - 1;
  }
  for (let j = i - 1; j >= 0; j--) if (ms(frames[j].t) <= target) return j;
  return 0;
}
function dayStep(frames, i, dir) {
  const p = ist(frames[i].t), mod = p.h * 60 + p.mi;
  let j = i;
  while (j >= 0 && j < frames.length && ist(frames[j].t).key === p.key) j += dir;
  if (j < 0) return 0;
  if (j >= frames.length) return frames.length - 1;
  const key = ist(frames[j].t).key;
  const day = frames.map((f, k) => [k, ist(f.t)]).filter(([, q]) => q.key === key);
  const hit = day.find(([, q]) => q.h * 60 + q.mi >= mod);
  return (hit || day[day.length - 1])[0];
}

// One play tick: the next step, stopping on a trade on the way when asked.
export function advance(frames, events, i, m, pauseAtTrades) {
  const j = stepTo(frames, i, m, 1);
  if (pauseAtTrades) {
    const e = events.find(x => x.frame > i && x.frame <= j);
    if (e) return { i: e.frame, stop: true, event: e };
  }
  return { i: j, stop: j >= frames.length - 1, event: null };
}

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
export function change(frames, a, b, k) {
  const x = frames[a] && frames[a][k], y = frames[b] && frames[b][k];
  return x == null || y == null ? null : y - x;
}
// The step a time falls in (the last one starting at or before it).
export function frameOf(frames, t) {
  const m = ms(t);
  let k = 0;
  for (let j = 0; j < frames.length; j++) { if (ms(frames[j].t) <= m) k = j; else break; }
  return k;
}
// When step i's prices are known: its bar's close, at the bar's end.
export const asOf = (D, i) => new Date(ms(D.frames[i].t) + barMinutes(D.interval) * 60000);

// ── the positions at a step (tested) ───────────────────────────────────

const endOf = (D, i) => (D.frames[i + 1] ? ms(D.frames[i + 1].t) : Infinity);

// Every leg opened by step i: open ones with their price, P&L and
// volatility there; closed ones with what they booked.
export function positionsAt(D, i) {
  const until = endOf(D, i), f = D.frames[i];
  return D.legs.map((l, j) => ({ l, j })).filter(({ l }) => ms(l.t0) < until).map(({ l, j }) => {
    const open = f.legs.find(x => x[0] === j);
    const closed = !open && !!l.t1 && ms(l.t1) < until;
    return {
      j, l, closed, now: open ? open[1] : closed ? l.exit : null,
      pnl: open ? open[2] : closed ? l.realized : null, stale: open ? open[3] : false, iv: open ? open[4] ?? null : null,
      fresh: frameOf(D.frames, l.t0) === i, justClosed: closed && frameOf(D.frames, l.t1) === i,
    };
  });
}

// ── the payoff (tested) ────────────────────────────────────────────────

// The levels of the index the payoff is drawn over: around where it is,
// wide enough for every open strike and every breakeven at expiry.
export function rangeOf(D, i) {
  const f = D.frames[i];
  const ks = f.legs.map(x => D.legs[x[0]].strike).filter(Boolean);
  const spot = f.idx ?? (f.fut != null ? f.fut : ks.length ? ks.reduce((a, b) => a + b, 0) / ks.length : null);
  if (spot == null) return null;
  let half = Math.max(spot * 0.035, ...ks.map(k => Math.abs(k - spot) * 1.4));
  if (f.legs.length) {   // look wide for the breakevens, then take them in with room to spare
    const wide = grid(spot - spot * 0.25, spot + spot * 0.25), be = breakevens(wide, payoff(D, i, wide).expiry);
    half = Math.max(half, ...be.map(b => Math.abs(b - spot) * 1.25));
  }
  half = Math.min(half, spot * 0.25);
  return [spot - half, spot + half, spot];
}
export const grid = (lo, hi, n = 241) => Array.from({ length: n }, (_, k) => lo + (hi - lo) * k / (n - 1));

// The P&L of everything at step i — booked plus the open legs — if the
// index were at each of xs: at expiry (the nearest expiry among the open
// options; legs expiring later valued then at their volatility now), and
// today (each option at the volatility its price implies now, off the
// future where the backend priced it off the future, the basis held).
// {expiry, now (null when it can't be drawn), target, approx, notes}.
export function payoff(D, i, xs) {
  const f = D.frames[i], r = D.rate ?? 0.065, at = asOf(D, i);
  const basis = f.fut != null && f.idx != null ? f.fut - f.idx : 0;
  const O = f.legs.map(([j, mark, , stale, iv]) => ({ l: D.legs[j], mark, stale, iv }));
  const target = O.filter(o => isOpt(o.l) && o.l.expiry).map(o => o.l.expiry).sort()[0] || null;
  const atTarget = target ? new Date(`${target}T15:30:00+05:30`) : null;
  const approx = [], notes = [];
  let canNow = true;
  const fns = O.map(o => {
    const l = o.l, sz = (l.side === 'short' ? -1 : 1) * l.qty;
    if (!isOpt(l)) {   // a future (or anything else priced like the index)
      return { exp: x => (x - l.entry) * sz, now: x => (x + basis - l.entry) * sz };
    }
    const k = l.strike;
    if (!k) { canNow = false; notes.push(`${l.symbol}: no strike known, left out`); return { exp: () => 0, now: () => 0 }; }
    const vol = o.iv != null ? o.iv / 100 : l.iv != null ? l.iv / 100 : null;
    if (o.iv == null && vol != null) approx.push(l.symbol);
    const exp = !l.expiry || !target || l.expiry === target || vol == null
      ? x => (intrinsic(l.kind, x, k) - l.entry) * sz
      : x => (bs(l.kind, x, k, yearsTo(l.expiry, atTarget), vol, r) - l.entry) * sz;
    if (!l.expiry || vol == null) {
      canNow = false;
      notes.push(`${legName(l)}: ${!l.expiry ? 'its expiry isn’t known' : 'no price to read its volatility from'}, so there’s no line for today`);
      return { exp, now: () => 0 };
    }
    const T = yearsTo(l.expiry, at);
    const now = l.fwd ? x => (black76(l.kind, x + basis, k, T, vol, r) - l.entry) * sz : x => (bs(l.kind, x, k, T, vol, r) - l.entry) * sz;
    return { exp, now };
  });
  const sum = key => xs.map(x => f.booked + fns.reduce((a, g) => a + g[key](x), 0));
  return { expiry: sum('exp'), now: canNow && O.length ? sum('now') : null, target, approx, notes, basis };
}

// Where a curve crosses zero (linear between the points).
export function breakevens(xs, ys) {
  const out = [];
  for (let k = 1; k < xs.length; k++) {
    const a = ys[k - 1], b = ys[k];
    if ((a < 0 && b >= 0) || (a >= 0 && b < 0)) out.push(xs[k - 1] + (xs[k] - xs[k - 1]) * (0 - a) / (b - a));
  }
  return out;
}

// The most it makes and loses at expiry across the range: "unlimited"
// when the curve still runs that way at the edge of the range.
export function extremes(ys) {
  const n = ys.length, hi = Math.max(...ys), lo = Math.min(...ys);
  const runs = (k, d) => (k === 0 ? ys[0] - ys[1] : ys[n - 1] - ys[n - 2]) * d > 1e-6;
  const at = v => [ys.indexOf(v), ys.lastIndexOf(v)];
  const [h0, h1] = at(hi), [l0, l1] = at(lo);
  return {
    max: hi, maxUnlimited: (h0 === 0 && runs(0, 1)) || (h1 === n - 1 && runs(n - 1, 1)),
    min: lo, minUnlimited: (l0 === 0 && runs(0, -1)) || (l1 === n - 1 && runs(n - 1, -1)),
  };
}

// ── the payoff chart ───────────────────────────────────────────────────

let PW = 1000, PH = 430;
const PL = 72, PR = 24, PT = 46, PB = 58;
// The payoff's height for a window vw × vh: about a third of the window, so
// the controls above it and the positions under it fit on the same screen.
export const payoffHeight = (vw, vh) => (vw < 700 ? 280 : Math.round(Math.max(210, Math.min(360, vh * 0.45 - 110))));
let clipN = 0;
const niceStep = span => { const p = 10 ** Math.floor(Math.log10(span / 5)); return [1, 2, 2.5, 5, 10].map(m => m * p).find(s => span / s <= 6) || p * 10; };

// The open strikes the index level can't belong to: far further away than
// any leg of these strategies goes (the history is for another index, or
// wrong). The payoff isn't drawn against them.
export function mismatched(D, i) {
  const f = D.frames[i];
  if (f.idx == null) return [];
  return f.legs.map(x => D.legs[x[0]]).filter(l => l.strike && Math.abs(l.strike / f.idx - 1) > 0.35);
}

export function payoffChart(D, i, P, xs, width = 1000, height = 430) {
  PW = Math.round(Math.max(420, width));   // drawn at the width it's shown at: text stays its size
  PH = Math.round(height);
  const f = D.frames[i];
  const [lo, hi, spot] = rangeOf(D, i);
  const all = [...P.expiry, ...(P.now || []), 0];
  let vmin = Math.min(...all), vmax = Math.max(...all);
  const pad = Math.max((vmax - vmin) * 0.08, 50);
  vmin -= pad; vmax += pad;
  const X = x => PL + (x - lo) / (hi - lo) * (PW - PL - PR);
  const Y = v => PT + (vmax - v) / (vmax - vmin) * (PH - PT - PB);
  const xAt = Xp => lo + (Xp - PL) / (PW - PL - PR) * (hi - lo);
  const line = ys => xs.map((x, k) => `${k ? 'L' : 'M'}${X(x).toFixed(1)} ${Y(ys[k]).toFixed(1)}`).join('');
  const y0 = Y(0), id = `rpc${++clipN}`;
  let s = '';
  // grid and axes
  const ys = niceStep(vmax - vmin);
  for (let v = Math.ceil(vmin / ys) * ys; v <= vmax; v += ys) {
    s += `<line x1="${PL}" x2="${PW - PR}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" stroke-width=".6"/><text x="${PL - 8}" y="${Y(v) + 3.5}" text-anchor="end">${esc(inr(v))}</text>`;
  }
  const xsStep = niceStep(hi - lo);
  for (let v = Math.ceil(lo / xsStep) * xsStep; v <= hi; v += xsStep) {
    s += `<line x1="${X(v)}" x2="${X(v)}" y1="${PT}" y2="${PH - PB}" stroke="var(--line)" stroke-width=".4"/><text x="${X(v)}" y="${PH - PB + 16}" text-anchor="middle">${v.toLocaleString('en-IN')}</text>`;
  }
  // profit and loss at expiry: shaded either side of zero
  const area = `${line(P.expiry)}L${X(xs[xs.length - 1])} ${y0}L${X(xs[0])} ${y0}Z`;
  s += `<defs><clipPath id="${id}u"><rect x="0" y="0" width="${PW}" height="${y0}"/></clipPath><clipPath id="${id}d"><rect x="0" y="${y0}" width="${PW}" height="${PH}"/></clipPath></defs>`;
  s += `<path d="${area}" fill="var(--gs)" clip-path="url(#${id}u)"/><path d="${area}" fill="var(--rs)" clip-path="url(#${id}d)"/>`;
  s += `<line x1="${PL}" x2="${PW - PR}" y1="${y0}" y2="${y0}" stroke="var(--line2)" stroke-width="1.2"/>`;
  s += `<path d="${line(P.expiry)}" fill="none" stroke="var(--ink)" stroke-width="1.8" data-curve="expiry"/>`;
  if (P.now) s += `<path d="${line(P.now)}" fill="none" stroke="var(--b)" stroke-width="2.2" data-curve="now"/>`;
  // breakevens at expiry
  for (const b of breakevens(xs, P.expiry)) {
    s += `<circle cx="${X(b)}" cy="${y0}" r="3.5" fill="var(--paper)" stroke="var(--ink)" stroke-width="1.4"/><text x="${X(b)}" y="${y0 - 8}" text-anchor="middle" class="rp-be">${Math.round(b).toLocaleString('en-IN')}</text>`;
  }
  // the open strikes, under the axis
  const seen = {};
  for (const [j] of f.legs) {
    const l = D.legs[j];
    if (!l.strike || l.strike < lo || l.strike > hi) continue;
    const n = (seen[l.strike] = (seen[l.strike] || 0) + 1) - 1;
    const Xk = X(l.strike), Yk = PH - PB + 30 + n * 13;
    s += `<path d="M${Xk} ${PH - PB}V${PH - PB + 5}" stroke="${legColor(l)}" stroke-width="2"/><text x="${Xk}" y="${Yk}" text-anchor="middle" style="fill:${legColor(l)};font-weight:700">${l.side === 'short' ? 'S' : 'B'} ${l.kind || ''} ${l.strike}</text>`;
  }
  // where the index and its future are
  const tagAt = (x, y, text, fill, ink) => {
    const w = text.length * 7.3 + 14, x0 = Math.max(PL, Math.min(PW - PR - w, x - w / 2));
    return `<rect x="${x0}" y="${y - 12}" width="${w}" height="17" fill="${fill}"/><text x="${x0 + w / 2}" y="${y + 1}" text-anchor="middle" style="fill:${ink};font:700 11px var(--f-mono)">${esc(text)}</text>`;
  };
  if (f.fut != null && f.fut >= lo && f.fut <= hi) {
    s += `<line x1="${X(f.fut)}" x2="${X(f.fut)}" y1="${PT - 4}" y2="${PH - PB}" stroke="var(--mute)" stroke-dasharray="4 3"/>`;
    s += tagAt(X(f.fut), 34, `FUT ${price(f.fut)} · ${sgn(f.fut - (f.idx ?? f.fut))}`, 'var(--soft)', 'var(--ink)');
  }
  if (f.idx != null) {
    s += `<line x1="${X(spot)}" x2="${X(spot)}" y1="${PT - 22}" y2="${PH - PB}" stroke="var(--ink)" stroke-width="1.2"/>`;
    s += tagAt(X(spot), 14, `${D.index_name} ${price(spot)}`, 'var(--ink)', 'var(--paper)');
  }
  // the P&L now, on the curve at the index
  const curve = P.now || P.expiry, k0 = xs.reduce((b, x, k) => (Math.abs(x - spot) < Math.abs(xs[b] - spot) ? k : b), 0);
  const here = P.now ? f.pnl : curve[k0];
  s += `<circle cx="${X(spot)}" cy="${Y(curve[k0])}" r="5" fill="${P.now ? 'var(--b)' : 'var(--ink)'}" stroke="var(--paper)" stroke-width="2"/>`;
  s += `<text x="${X(spot) + 9}" y="${Y(curve[k0]) - 8}" class="${tone(here)}" style="font-weight:700;font-size:12px">${esc(inr(here))}</text>`;
  const pts = ys_ => xs.map((x, k) => [X(x), Y(ys_[k]), inr(ys_[k])]);
  const hover = {
    series: [{ name: P.target ? `At expiry ${dShort(P.target)}` : 'At expiry', color: 'var(--ink)', pts: pts(P.expiry) },
      ...(P.now ? [{ name: `Today ${hhmm(asOf(D, i))}`, color: 'var(--b)', pts: pts(P.now) }] : [])],
    title: Xp => { const x = xAt(Xp); return `${D.index_name} at ${Math.round(x).toLocaleString('en-IN')} (${sgn((x / spot - 1) * 100, 2)}%)`; },
  };
  return svg(PW, PH, s, `Payoff: P&L by where ${D.index_name} is`, hover);
}

const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');

// ── the timeline: P&L across the whole replay, its trades, the playhead ─

// Drawn at the width it's shown at (like the payoff), so the line fills the
// track and a click or drag maps straight onto the step under the pointer.
let TW = 1000;
const TH = 48;
function timeline(D, i, width = 1000) {
  TW = Math.round(Math.max(200, width));
  const F = D.frames, n = F.length, v = F.map(f => f.pnl);
  let lo = Math.min(0, ...v), hi = Math.max(0, ...v);
  if (hi === lo) { hi += 1; lo -= 1; }
  const X = k => 4 + k / Math.max(1, n - 1) * (TW - 8), Y = x => 12 + (hi - x) / (hi - lo) * (TH - 16);
  let s = '', day = '';
  F.forEach((f, k) => { const d = ist(f.t).key; if (d !== day) { if (day) s += `<line x1="${X(k)}" x2="${X(k)}" y1="10" y2="${TH}" stroke="var(--line)"/>`; day = d; } });
  s += `<line x1="0" x2="${TW}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--line2)" stroke-dasharray="3 3"/>`;
  s += `<path d="${v.map((x, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)} ${Y(x).toFixed(1)}`).join('')}" fill="none" stroke="var(--mute)" stroke-width="1.4"/>`;
  s += `<path d="${v.slice(0, i + 1).map((x, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)} ${Y(x).toFixed(1)}`).join('')}" fill="none" stroke="var(--ink)" stroke-width="2"/>`;
  for (const e of D.events) s += `<rect x="${X(e.frame) - 2}" y="0" width="4" height="9" fill="${kindOf(e.kind)[1]}"/>`;
  s += `<line x1="${X(i)}" x2="${X(i)}" y1="0" y2="${TH}" stroke="var(--ink)" stroke-width="1.5"/><circle cx="${X(i)}" cy="${Y(v[i])}" r="4" fill="var(--ink)"/>`;
  const hover = {
    series: [{ name: 'P&L', color: 'var(--ink)', pts: F.map((f, k) => [X(k), Y(f.pnl), inr(f.pnl), dStamp(f.t)]) }],
    title: (Xp, p) => (p ? p[3] : ''),
  };
  return svg(TW, TH, s, 'Replay timeline: P&L, trades and where you are', hover);
}

// ── around the chart ───────────────────────────────────────────────────

// The readout under the controls: where everything stands at this step.
function strip(D, i) {
  const F = D.frames, f = F[i];
  const cell = (l, v, sub, cls = '', k = '') => html`<div class="rp-cell ${k}"><span>${l}</span><b class="${cls}">${v}</b>${sub ? html`<i>${sub}</i>` : ''}</div>`;
  return html`<div class="rp-strip">
    ${cell(dShort(f.t), hhmm(asOf(D, i)), `${i + 1} / ${F.length}`)}
    ${cell(`${D.index_name} · since start`, f.idx == null ? '—' : price(f.idx), sgn(change(F, 0, i, 'idx')))}
    ${D.future ? cell(`Future · ${D.future.symbol}`, f.fut == null ? '—' : price(f.fut), f.fut != null && f.idx != null ? `basis ${sgn(f.fut - f.idx)}` : '') : ''}
    ${cell('India VIX · since start', f.vix == null ? '—' : f.vix.toFixed(2), sgn(change(F, 0, i, 'vix'), 2))}
    ${cell('P&L', inr(f.pnl), `booked ${inr(f.booked)} · open ${inr(f.pnl - f.booked)}`, `${tone(f.pnl)}`, 'wide')}
  </div>`;
}

// What the payoff says about what's held: in the chart's header.
function payStats(D, i, P, xs) {
  const f = D.frames[i];
  if (!f.legs.length) return '';
  const ex = extremes(P.expiry), be = breakevens(xs, P.expiry);
  return html`<span class="rp-stats">
    <span>Max profit <b class="${ex.maxUnlimited ? 'pos' : tone(ex.max)}">${ex.maxUnlimited ? 'Unlimited' : inr(ex.max)}</b></span>
    <span>Max loss <b class="${ex.minUnlimited ? 'neg' : tone(ex.min)}">${ex.minUnlimited ? 'Unlimited' : inr(ex.min)}</b></span>
    <span>Breakevens <b>${be.length ? be.map(b => `${Math.round(b).toLocaleString('en-IN')}${f.idx != null ? ` (${sgn((b / f.idx - 1) * 100, 1)}%)` : ''}`).join(' · ') : 'none'}</b></span>
    <span class="mute">at expiry, in the range shown</span></span>`;
}

function banner(D, i, dep) {
  const e = eventAt(D.events, i);
  if (!e) return html`<div class="rp-banner mute small">Before the first trade.</div>`;
  const [lab, c] = kindOf(e.kind), here = e.frame === i;
  const ago = Math.round((ms(D.frames[i].t) - ms(D.frames[e.frame].t)) / 60000);
  return html`<div class="rp-banner ${here ? 'here' : ''}" style="--k:${c}">
    <span class="rp-chip" style="--k:${c}">${lab}${e.cycle != null && e.kind === 'new' ? ` ${e.cycle}` : ''}</span>
    <b class="small" ${e.why ? html`data-tip="${`saw: ${e.why}`}"` : ''}>${e.rule}</b> ${fixTag(e.repaired, dep)}
    <span class="mono tiny">${dStamp(e.t)}${here ? ' · now' : ` · ${ago < 120 ? `${ago} min` : ago < 2880 ? `${Math.round(ago / 60)} h` : `${Math.round(ago / 1440)} days`} ago`}</span>
    ${e.booked != null ? html`<span class="small">booked <b class="${tone(e.booked)}">${inr(e.booked)}</b></span>` : ''}
    ${e.closed_before ? html`<span class="tiny">first closed ${e.closed_before.cycle != null ? `cycle ${e.closed_before.cycle}` : 'the episode before'}${e.closed_before.booked != null ? html`, booking <b class="${tone(e.closed_before.booked)}">${inr(e.closed_before.booked)}</b>` : ''}</span>` : ''}
    <span class="rp-fills">${e.fills.map(x => html`<span class="tiny"><b>${x.action === 'sell' ? 'Sold' : 'Bought'}</b> ${x.qty} <span class="mono">${x.symbol}</span> @ ${price(x.price)}</span>`)}</span>
    ${e.why ? html`<span class="tiny mute rp-why" data-tip="${`saw: ${e.why}`}">saw: ${e.why}</span>` : ''}
  </div>`;
}

// A leg's P&L as a % of what it was entered for (its premium × quantity).
export const pnlPct = (l, pnl) => (pnl == null || !l.entry || !l.qty ? null : pnl / (l.entry * l.qty) * 100);

function positions(D, i, dep) {
  const P = positionsAt(D, i);
  if (!P.length) return html`<p class="small mute" style="margin:0">No position yet at this step.</p>`;
  const open = P.filter(p => !p.closed), closed = P.filter(p => p.closed), L = [...open, ...closed];
  const sum = xs => xs.reduce((t, p) => t + (p.pnl || 0), 0);
  const how = t => (t ? t.label || kindOf(t.kind)[0] : '');
  const today = dShort(D.frames[i].t);
  const at = t => html`<span class="tiny mute nowrap">${dShort(t) !== today ? `${dShort(t).replace(/ '\d\d$/, '')} ` : ''}${hhmm(t)}</span>`;
  const rows = L.map(p => {
    const l = p.l, pct = pnlPct(l, p.pnl);
    const entryTip = `Entered ${dStamp(l.t0)}${l.s0 ? `\n${D.index_name}  ${price(l.s0)}` : ''}${l.f0 ? `\nFuture  ${price(l.f0)}` : ''}${l.iv ? `\nVolatility  ${l.iv}%` : ''}${l.opened_by ? `\nBy  ${how(l.opened_by)} · ${l.opened_by.rule}` : ''}`;
    const exitTip = p.closed ? `Closed ${dStamp(l.t1)}${l.closed_by ? `\nBy  ${how(l.closed_by)} · ${l.closed_by.rule}` : ''}` : '';
    return [
      html`<span class="col" style="gap:0"><b class="mono small" style="color:${legColor(l)}">${legName(l)}</b><span class="tiny mute">${l.expiry ? dShort(l.expiry) : l.symbol}</span></span> ${fixTag(l.repaired, dep)}`,
      html`<span class="small">${l.side === 'short' ? 'Sold' : 'Bought'} ${l.qty}</span>`,
      html`<span class="col" style="gap:0" data-tip="${entryTip}" data-cell="entry"><span class="mono">${price(l.entry)}</span>${at(l.t0)}</span>`,
      p.closed ? html`<span class="col" style="gap:0" data-tip="${exitTip}" data-cell="exit"><span class="mono">${price(l.exit)}</span>${at(l.t1)}</span>`
        : html`<span class="tiny mute" data-cell="exit">open</span>`,
      p.closed ? html`<span class="mute">—</span>`
        : html`<span class="mono ${p.stale ? 'mute' : ''}" data-cell="now" ${p.stale ? html`data-tip="No price stored at this step: shown at entry"` : ''}>${p.now == null ? '—' : price(p.now)}</span>`,
      html`<span class="mono small">${p.iv != null ? `${p.iv.toFixed(1)}%` : '—'}</span>`,
      html`<b class="mono ${tone(p.pnl)}">${p.pnl == null ? '—' : inr(p.pnl)}</b>`,
      html`<span class="mono small ${tone(p.pnl)}" data-cell="pct">${pct == null ? '—' : `${sgn(pct, 1)}%`}</span>`,
      p.closed ? html`<span class="small" data-tip="${exitTip}">Closed${l.closed_by ? ` · ${how(l.closed_by)}` : ''}</span>`
        : p.fresh ? html`<span class="rp-chip sm" style="--k:var(--b)">New${l.opened_by ? ` · ${how(l.opened_by)}` : ''}</span>` : html`<span class="small">Open</span>`,
    ];
  });
  rows.push([html`<b class="small">Total</b>`, html`<span class="tiny mute nowrap">${open.length} open · ${closed.length} closed</span>`, '', '', '', '',
    html`<b class="mono ${tone(sum(P))}">${inr(sum(P))}</b>`, '', html`<span class="tiny">open <b class="${tone(sum(open))}">${inr(sum(open))}</b> · booked <b class="${tone(sum(closed))}">${inr(sum(closed))}</b></span>`]);
  return table([['Instrument', 'minmax(118px,1.1fr)'], ['Qty', '74px'], ['Entry', '92px', 'num'], ['Exit', '92px', 'num'], ['Now', '78px', 'num'],
    ['IV', '58px', 'num'], ['P&L', '96px', 'num'], ['%', '64px', 'num'], ['Status', 'minmax(150px,1.2fr)']],
  rows, { minw: 860, cls: 'rp-pos', rowCls: k => (k === L.length ? 'rp-total' : L[k].fresh && !L[k].closed ? 'rp-fresh' : L[k].justClosed ? 'rp-gone rp-closed' : L[k].closed ? 'rp-closed' : '') });
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

const SPEEDS = [[1, '½×'], [2, '1×'], [4, '2×'], [8, '4×']];   // steps a second
const N = 241;   // points across the payoff

export default {
  render(el, v, g) {
    const p = g.params || {};
    let D = null, i = 0, playing = false, timer = null, speed = 1, pauseAt = true, step = null;
    // Explain: which beat, paused or not, voice or captions only
    const X = { on: false, k: 0, paused: false, voice: typeof speechSynthesis !== 'undefined', timer: null, run: 0 };

    function picker() {
      const E = D.episodes, [a, z] = D.selected;
      const opt = sel => E.map(e => html`<option value="${e.n}" ${e.n === sel ? 'selected' : ''}>${e.label} · ${dShort(e.t0)}${e.open ? ' · open' : ''}${e.repaired ? ' · repaired' : ''}</option>`);
      return html`<div class="rp-pick">
        <label class="small">From <select data-pick="from">${opt(a)}</select></label>
        <label class="small">to <select data-pick="to">${opt(z)}</select></label>
        <span class="tiny mute">${z > a ? `a chain of ${z - a + 1}, played as one` : 'one episode'} · prices every ${barMinutes(D.interval) === 1 ? 'minute' : `${barMinutes(D.interval)} minutes`} · nights and weekends skipped</span>
      </div>`;
    }

    function episodes() {
      const E = [...D.episodes].reverse().slice(0, 40), [a, z] = D.selected;
      return table([['', 'minmax(110px,1fr)'], ['Opened', '110px'], ['Closed', '110px'], ['Moves', '64px', 'num'], ['Booked', '96px', 'num']],
        E.map(e => [html`<b class="small">${e.label}</b> ${fixTag(e.repaired, g.id)}${e.n >= a && e.n <= z ? html` <span class="tiny mute">· replaying</span>` : ''}`, html`<span class="mono tiny">${dStamp(e.t0)}</span>`,
          html`<span class="mono tiny">${e.open ? 'open' : dStamp(e.t1)}</span>`, String(e.adjustments), html`<span class="${tone(e.booked)}">${money(e.booked)}</span>`]),
        { minw: 0, rowAttrs: k => html`data-ep="${E[k].n}" role="button" tabindex="0" style="cursor:pointer"` });
    }

    function controls() {
      const n = D.frames.length, S = stepsFor(D.interval);
      return html`<div class="rp-dock"><div class="rp-ctl">
        <span class="rp-btns">
          ${D.explain && D.explain.beats.length ? html`<button type="button" class="btn btn-sm btn-primary rp-explain" data-act="explain" title="The replay told in words: what it did and why">${X.on ? 'Explaining…' : '▶ Explain'}</button>` : ''}
          <button type="button" class="btn btn-sm" data-act="to" data-f="0" aria-label="To the start">⏮</button>
          <button type="button" class="btn btn-sm" data-act="prevEv" aria-label="Previous trade" title="Previous trade  [">⇤</button>
          <button type="button" class="btn btn-sm" data-act="back" aria-label="Step back" title="Step back  ←">‹</button>
          <button type="button" class="btn btn-sm rp-play" data-act="play" aria-label="Play">▶ Play</button>
          <button type="button" class="btn btn-sm" data-act="fwd" aria-label="Step forward" title="Step forward  →">›</button>
          <button type="button" class="btn btn-sm" data-act="nextEv" aria-label="Next trade" title="Next trade  ]">⇥</button>
          <button type="button" class="btn btn-sm" data-act="to" data-f="${n - 1}" aria-label="To the end">⏭</button>
        </span>
        <span class="rp-lab tiny mute">Step</span>
        <span class="rp-seg" data-seg="step" role="group" aria-label="Step size">${S.map(([l, m]) => html`<button type="button" class="${m === step ? 'on' : ''}" data-act="step" data-m="${m}">${l}</button>`)}</span>
        <span class="rp-lab tiny mute">Speed</span>
        <span class="rp-seg" data-seg="speed" role="group" aria-label="Speed">${SPEEDS.map(([, l], k) => html`<button type="button" class="${k === speed ? 'on' : ''}" data-act="speed" data-s="${k}">${l}</button>`)}</span>
        <label class="small row" style="gap:6px"><input type="checkbox" data-pause ${pauseAt ? 'checked' : ''}> Pause at trades</label>
        <span class="tiny mute hide-phone">Space · ← → · [ ]</span>
      </div>
      <div class="rp-track" data-track>
        <div data-rp="track"></div>
        <input class="sr-only" type="range" min="0" max="${n - 1}" value="${i}" data-range aria-label="Replay position">
      </div>
      <div data-rp="strip"></div></div>`;
    }

    // On a phone the dock scrolls away with the page: play and step stay here.
    function mini() {
      return html`<div class="rp-mini" role="group" aria-label="Replay controls">
        <button type="button" class="btn btn-sm" data-act="back" aria-label="Step back">‹</button>
        <button type="button" class="btn btn-sm rp-play" data-act="play" aria-label="Play">▶ Play</button>
        <button type="button" class="btn btn-sm" data-act="fwd" aria-label="Step forward">›</button>
        <span data-rp="mini"></span></div>`;
    }

    function paint() {
      if (!D) { mount(el, html`${head('Replay', 'the payoff, step by step')}${loading(6)}`); return; }
      if (D instanceof Error) { mount(el, html`${head('Replay')}${empty('The replay didn’t load', D.message)}`); return; }
      if (!D.episodes.length) { mount(el, html`${head('Replay')}${empty('Nothing to replay yet', 'A replay appears once this deployment has traded.')}`); return; }
      if (!D.frames || !D.frames.length) { mount(el, html`${head('Replay')}${picker()}${empty('No prices for this window', D.market_error || 'No prices were found for it.')}`); return; }
      i = Math.max(0, Math.min(D.frames.length - 1, i));
      if (step == null || !stepsFor(D.interval).some(([, m]) => m === step)) step = stepsFor(D.interval)[0][1];
      mount(el, html`<div class="rp-view">
        <div class="rp-top">${head('Replay', '', D.market_error ? 'index and VIX unavailable' : '')}
        ${picker()}</div>
        ${controls()}
        <div data-rp="explain"></div>
        <div data-rp="banner"></div>
        <div class="gk-panel rp-pay"><h3>Payoff <span class="s" data-rp="paysub"></span><span data-rp="paystats"></span></h3>
          <div class="rp-canvas" data-rp="stage"></div>
          <div class="rp-legend tiny" data-rp="legend"></div></div>
        <div class="gk-panel"><h3>Positions <span class="s">new ones join as they’re opened · closed ones grey out, entry and exit kept</span></h3><div data-rp="pos"></div></div>
        <div class="gk-two">
          <div class="gk-panel"><h3>Trades <span class="s">${D.events.length} · click one to go there</span></h3><div data-rp="list"></div></div>
          <div class="gk-panel"><h3>Episodes <span class="s">click one to replay it · From and To above for a chain</span></h3>${episodes()}</div>
        </div>
        ${D.unpriced && D.unpriced.length ? html`<p class="gk-note">No stored prices for ${D.unpriced.join(', ')}: shown at entry until they close.</p>` : ''}
        ${mini()}</div>`);
      update();
    }

    function update() {
      if (!D || !D.frames || !D.frames.length) return;
      const set = (k, h) => { const t = el.querySelector(`[data-rp="${k}"]`); if (t) mount(t, h); };
      const f = D.frames[i], R = rangeOf(D, i);
      const xs = R ? grid(R[0], R[1], N) : [];
      const bad = mismatched(D, i);
      const P = R && f.legs.length && !bad.length ? payoff(D, i, xs) : null;
      const tr = el.querySelector('[data-rp="track"]');
      set('track', timeline(D, i, tr && tr.clientWidth ? tr.clientWidth : 1000));
      set('strip', strip(D, i));
      set('paystats', P ? payStats(D, i, P, xs) : '');
      set('mini', html`<span class="mono small">${dShort(f.t).replace(/ '\d\d$/, '')} ${hhmm(asOf(D, i))}</span> <b class="mono small ${tone(f.pnl)}">${inr(f.pnl)}</b>`);
      set('banner', banner(D, i, g.id));
      set('explain', explainPanel());
      if (P) {
        const st = el.querySelector('[data-rp="stage"]');
        set('stage', payoffChart(D, i, P, xs, st ? st.clientWidth : 1000, payoffHeight(window.innerWidth, window.innerHeight)));
        set('paysub', html`at expiry${P.target ? ` (${dShort(P.target)})` : ''}${P.now ? ` and today at ${hhmm(asOf(D, i))}` : ''} · ${D.index_name} ${R[2] ? price(R[2]) : ''}`);
        set('legend', html`<span><i class="rp-k ink"></i>At expiry${P.target ? ` · ${dShort(P.target)}` : ''}</span>
          ${P.now ? html`<span><i class="rp-k b"></i>Today · from each leg’s price now</span>` : ''}
          <span><i class="rp-k spot"></i>${D.index_name}</span>${f.fut != null ? html`<span><i class="rp-k fut"></i>Future</span>` : ''}
          <span><i class="rp-k gs"></i>profit / <i class="rp-k rs"></i>loss at expiry</span>
          ${P.approx.length ? html`<span class="rp-warn">${P.approx.join(', ')}: no price at this step, valued at the volatility at entry</span>` : ''}
          ${P.notes.map(n => html`<span class="rp-warn">${n}</span>`)}`);
      } else {
        set('stage', html`<p class="small mute rp-flat">${bad.length
          ? `${D.index_name} at ${price(f.idx)} can’t be the index of ${bad.map(legName).join(', ')}: its history here doesn’t match these contracts, so no payoff is drawn.`
          : f.legs.length ? `The ${D.index_name}’s level isn’t known at this step, so there’s no payoff to draw.` : 'Nothing open at this step. Booked so far: '}${f.legs.length ? '' : html`<b class="${tone(f.booked)}">${inr(f.booked)}</b>`}</p>`);
        set('paysub', '');
        set('legend', '');
      }
      set('pos', positions(D, i, g.id));
      set('list', eventList(D, i));
      const r = el.querySelector('[data-range]');
      if (r && +r.value !== i) r.value = i;
      el.querySelectorAll('.rp-play').forEach(b => { b.textContent = playing ? '❚❚ Pause' : '▶ Play'; b.setAttribute('aria-label', playing ? 'Pause' : 'Play'); });
    }

    // ── Explain ──
    function explainPanel() {
      if (!X.on) return '';
      const B = D.explain.beats, b = B[X.k], last = X.k >= B.length - 1;
      return html`<div class="rp-explain-box" role="region" aria-label="Explanation" data-explain>
        <div class="row" style="gap:10px;align-items:baseline"><b class="tiny up mute">${b.kind === 'intro' ? 'The setup' : b.kind === 'summary' ? 'How it went' : b.kind === 'decision' ? 'Didn’t trade' : 'Trade'}</b>
          <span class="tiny mute mono" data-explain-n>${X.k + 1} of ${B.length}</span><span class="grow"></span>
          <button type="button" class="btn btn-xs" data-act="xPrev" ${X.k ? '' : 'disabled'} aria-label="Previous">‹</button>
          <button type="button" class="btn btn-xs" data-act="xPause">${X.paused ? '▶ Resume' : '❚❚ Pause'}</button>
          <button type="button" class="btn btn-xs" data-act="xNext" ${last ? 'disabled' : ''} aria-label="Next">›</button>
          <button type="button" class="btn btn-xs" data-act="xVoice" aria-pressed="${X.voice}" ${typeof speechSynthesis === 'undefined' ? 'disabled title="This browser has no voice: captions only"' : ''}>${X.voice ? 'Voice on' : 'Voice off'}</button>
          <button type="button" class="btn btn-xs btn-quiet" data-act="xStop" aria-label="Stop explaining">✕</button></div>
        <p class="rp-explain-text" aria-live="polite" data-explain-text>${b.text}</p></div>`;
    }
    function hush() {
      if (X.timer) { clearTimeout(X.timer); clearInterval(X.timer); X.timer = null; }
      if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
    }
    function explainStop() { hush(); X.on = false; X.run += 1; paint(); }
    function say(text, done) {
      const run = X.run;
      if (!X.voice || typeof speechSynthesis === 'undefined') { X.timer = setTimeout(() => { if (run === X.run) done(); }, readMs(text)); return; }
      const parts = sentences(text), voice = pickVoice(speechSynthesis.getVoices());
      let n = 0;
      const next = () => {
        if (run !== X.run) return;
        if (n >= parts.length) { done(); return; }
        const u = new SpeechSynthesisUtterance(parts[n].replace(/₹([\d,]+)/g, '$1 rupees'));   // “₹4,388” said “4,388 rupees”
        n += 1;
        if (voice) u.voice = voice;
        u.lang = voice ? voice.lang : 'en-IN';
        u.onend = next;
        u.onerror = () => { if (run === X.run) X.timer = setTimeout(next, 300); };
        speechSynthesis.speak(u);
      };
      next();
    }
    // Glide to the beat's moment, say it, then on to the next.
    function beat(k) {
      hush();
      X.run += 1;
      const run = X.run, B = D.explain.beats;
      X.k = Math.max(0, Math.min(B.length - 1, k));
      const path = glide(i, B[X.k].frame);
      let j = 0;
      update();
      const speak = () => {
        if (run !== X.run) return;
        say(B[X.k].text, () => {
          if (run !== X.run || X.paused) return;
          if (X.k >= B.length - 1) { X.timer = setTimeout(() => { if (run === X.run) { X.on = false; paint(); } }, 1500); return; }
          X.timer = setTimeout(() => beat(X.k + 1), 500);
        });
      };
      X.timer = setInterval(() => {
        if (run !== X.run) return;
        if (j >= path.length) { clearInterval(X.timer); X.timer = null; speak(); return; }
        i = path[j]; j += 1; update();
      }, 35);
    }
    function explainStart() { stop(); X.on = true; X.paused = false; paint(); beat(0); }

    function stop() { playing = false; if (timer) clearInterval(timer); timer = null; update(); }
    function play() {
      if (!D || !D.frames) return;
      if (i >= D.frames.length - 1) i = 0;
      playing = true;
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        const s = advance(D.frames, D.events, i, step, pauseAt);
        i = s.i;
        if (s.stop) stop(); else update();
      }, 1000 / SPEEDS[speed][0]);
      update();
    }
    const to = j => { i = Math.max(0, Math.min(D.frames.length - 1, j)); update(); };
    const load = (a, z) => go(`d/${g.id}/geek/replay?from=${a}&to=${z}`);
    const seg = (name, key, val) => el.querySelectorAll(`[data-seg="${name}"] button`).forEach(x => x.classList.toggle('on', +x.dataset[key] === val));

    // the timeline: press and drag to go anywhere
    let dragging = false;
    const seek = e => {
      const s = el.querySelector('[data-rp="track"] svg');
      if (!s || !D || !D.frames) return;
      const rc = s.getBoundingClientRect(), X = (e.clientX - rc.left) / rc.width * TW;   // in the timeline's own units
      const fx = Math.max(0, Math.min(1, (X - 4) / (TW - 8)));
      to(Math.round(fx * (D.frames.length - 1)));
    };

    const off = actions(el, {
      explain: () => (X.on ? explainStop() : explainStart()),
      xStop: () => explainStop(),
      xNext: () => { X.paused = false; beat(X.k + 1); },
      xPrev: () => { X.paused = false; beat(X.k - 1); },
      xPause: () => { if (X.paused) { X.paused = false; beat(X.k); } else { X.paused = true; hush(); X.run += 1; update(); } },
      xVoice: () => { X.voice = !X.voice; if (!X.paused) beat(X.k); else update(); },
      play: () => { if (X.on) explainStop(); return playing ? stop() : play(); },
      to: d => { stop(); to(+d.f); },
      jump: d => { stop(); to(+d.f); },
      fwd: () => { stop(); to(stepTo(D.frames, i, step, 1)); },
      back: () => { stop(); to(stepTo(D.frames, i, step, -1)); },
      nextEv: () => { stop(); const e = nextEvent(D.events, i); to(e ? e.frame : D.frames.length - 1); },
      prevEv: () => { stop(); const e = prevEvent(D.events, i); to(e ? e.frame : 0); },
      step: d => { step = +d.m; seg('step', 'm', step); },
      speed: d => { speed = +d.s; seg('speed', 's', speed); if (playing) play(); },
    });
    const offs = [
      on(el, 'input', '[data-range]', (e, t) => { stop(); to(+t.value); }),
      on(el, 'change', '[data-pause]', (e, t) => { pauseAt = t.checked; }),
      on(el, 'pointerdown', '[data-track]', e => { if (e.button !== 0) return; dragging = true; stop(); seek(e); }),
      on(document, 'pointermove', 'body', e => { if (dragging) seek(e); }),
      on(document, 'pointerup', 'body', () => { dragging = false; }),
      on(el, 'change', '[data-pick]', () => {
        const a = +el.querySelector('[data-pick="from"]').value, z = +el.querySelector('[data-pick="to"]').value;
        load(Math.min(a, z), Math.max(a, z));
      }),
      on(el, 'click', '[data-ep]', (e, t) => load(+t.dataset.ep, +t.dataset.ep)),
      on(el, 'keydown', '[data-ep]', (e, t) => { if (e.key === 'Enter') load(+t.dataset.ep, +t.dataset.ep); }),
      on(document, 'keydown', 'body', e => {
        if (!D || !D.frames || e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input:not([data-range]), textarea, select, [contenteditable]')) return;
        if (e.key === ' ') { e.preventDefault(); playing ? stop() : play(); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); stop(); to(stepTo(D.frames, i, step, 1)); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); stop(); to(stepTo(D.frames, i, step, -1)); }
        else if (e.key === ']') { e.preventDefault(); stop(); const x = nextEvent(D.events, i); if (x) to(x.frame); }
        else if (e.key === '[') { e.preventDefault(); stop(); const x = prevEvent(D.events, i); if (x) to(x.frame); }
      }),
    ];

    let rz = null;
    const onResize = () => { clearTimeout(rz); rz = setTimeout(update, 150); };
    window.addEventListener('resize', onResize);

    paint();
    const q = [p.from && `from=${encodeURIComponent(p.from)}`, p.to && `to=${encodeURIComponent(p.to)}`].filter(Boolean).join('&');
    let alive = true;
    api.geek(g.id, `replay${q ? `?${q}` : ''}`).then(d => { if (alive) { D = d; i = 0; paint(); } },
      e => { if (alive) { D = e instanceof Error ? e : new Error(String(e)); paint(); } });
    return () => { alive = false; dragging = false; hush(); X.run += 1; if (timer) clearInterval(timer); clearTimeout(rz); window.removeEventListener('resize', onResize); off(); offs.forEach(f => f()); };
  },
};
