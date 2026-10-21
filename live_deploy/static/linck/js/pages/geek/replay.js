// Replay (#/d/:id/geek/replay?from=N&to=M): how a cycle — or a continuous
// chain of them — actually went, played back step by step. The index
// (with the open legs' strikes against it), India VIX, the P&L and where
// it came from (time decay, the index's move, volatility) share one time
// axis with nights and weekends skipped. Every event is labelled for what
// it was — a new cycle, an adjustment, a roll, a hedge, a leg closed, the
// exit — with the rule, what it saw, the fills, and how the index, VIX and
// P&L had moved since the event before. Play, pause, step, jump between
// events; by default the replay stops at each one.
// Keys: Space play/pause · ← → one step · [ ] previous / next event.

import { api } from '../../core/api.js';
import { html, mount, actions, on } from '../../core/dom.js';
import { inr, dStamp, dShort, hhmm, price } from '../../core/format.js';
import { go } from '../../core/router.js';
import { empty, loading } from '../../ui/components.js';
import { head, table, svg, path, money, tone, yLabels, wide, hit } from './ui.js';

const WF = 1200, L = 64, R = WF - 14;
const SPEEDS = [[2, '½×'], [5, '1×'], [15, '3×'], [40, '8×']];
export const KIND = {
  new: ['New cycle', 'var(--ink)'], entry: ['Entry', 'var(--ink)'], adjustment: ['Adjustment', 'var(--b)'],
  roll: ['Roll', 'var(--c5)'], hedge: ['Hedge', 'var(--mute)'], added: ['Leg added', 'var(--b)'],
  leg_closed: ['Leg closed', 'var(--wr)'], exit: ['Exit', 'var(--ink)'],
};
const sgn = (v, dp = 0) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: dp, minimumFractionDigits: dp })}`);

// ── pure helpers (tested) ──────────────────────────────────────────────

// The last event at or before frame i (null before the first).
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

// One play step from i: the next frame, or the next event's frame when
// pausing at events and one lies in between (playing stops there).
export function advance(i, n, events, pauseAtEvents) {
  const j = Math.min(n - 1, i + 1);
  if (pauseAtEvents) {
    const e = events.find(x => x.frame > i && x.frame <= j);
    if (e) return { i: e.frame, stop: true, event: e };
  }
  return { i: j, stop: j >= n - 1, event: null };
}

// Change in a frame field from frame a to frame b (null if either is unknown).
export function change(frames, a, b, k) {
  const x = frames[a] && frames[a][k], y = frames[b] && frames[b][k];
  return x == null || y == null ? null : y - x;
}

// x of frame i on a gapless axis (nights and weekends skipped).
export const xOf = (i, n) => L + (n > 1 ? i / (n - 1) : 0) * (R - L);

// Day boundaries: [frame index, label] where the IST date changes.
export function dayTicks(frames) {
  const out = [];
  let last = '';
  frames.forEach((f, i) => { const d = dShort(f.t).replace(/ '\d\d$/, ''); if (d !== last) { out.push([i, d]); last = d; } });
  return out;
}

// ── charts ─────────────────────────────────────────────────────────────

let uid = 0;
function scale(vals, H, top = 10, bottom = 28, padFrac = 0.08, zero = false) {
  const v = vals.filter(x => x != null);
  let lo = Math.min(...v, ...(zero ? [0] : [])), hi = Math.max(...v, ...(zero ? [0] : []));
  if (!v.length) { lo = 0; hi = 1; }
  const pad = (hi - lo || Math.abs(hi) || 1) * padFrac;
  lo -= pad; hi += pad;
  return { y: x => top + (hi - x) / ((hi - lo) || 1) * (H - top - bottom), lo, hi };
}

function frame(D, H, id, body, label, hover) {
  // the playhead and the reveal: a clip that grows with the replay
  const n = D.frames.length;
  let s = `<defs><clipPath id="${id}"><rect data-rp-clip x="0" y="0" width="${xOf(0, n)}" height="${H}"/></clipPath></defs>`;
  dayTicks(D.frames).forEach(([i, d], k) => {
    const X = xOf(i, n);
    if (k) s += `<line x1="${X}" x2="${X}" y1="4" y2="${H - 22}" stroke="var(--line)" stroke-dasharray="2 3"/>`;
    s += `<text x="${X + 3}" y="${H - 8}">${d}</text>`;
  });
  D.events.forEach(e => {
    const X = xOf(e.frame, n), c = (KIND[e.kind] || KIND.entry)[1];
    s += `<line x1="${X}" x2="${X}" y1="4" y2="${H - 22}" stroke="${c}" stroke-width="1.2" opacity=".55"/>`;
    s += hit(X - 5, 4, 10, H - 26, `${(KIND[e.kind] || KIND.entry)[0]} · ${dStamp(e.t)}\n${e.rule}${e.booked != null ? `\nBooked  ${inr(e.booked)}` : ''}`);
  });
  s += body.replace(/__CLIP__/g, `clip-path="url(#${id})"`);
  s += `<line data-rp-head x1="${xOf(0, n)}" x2="${xOf(0, n)}" y1="2" y2="${H - 20}" stroke="var(--ink)" stroke-width="1.5"/>`;
  return wide(svg(WF, H, s, label, hover));
}

// A series drawn twice: faint in full, bright up to the playhead.
const both = (d, c, w = 1.8, dash = '') => `<path d="${d}" fill="none" stroke="var(--line2)" stroke-width="${w}"${dash}/><path d="${d}" fill="none" stroke="${c}" stroke-width="${w}"${dash} __CLIP__/>`;

function indexChart(D) {
  const F = D.frames, n = F.length, H = 230;
  if (!F.some(f => f.idx != null)) return empty('', D.market_error || 'No index history for this window.');
  const strikes = D.legs.filter(l => l.strike != null && l.kind);
  const sc = scale([...F.map(f => f.idx), ...strikes.map(l => l.strike)], H);
  const pts = F.map((f, i) => (f.idx == null ? null : [xOf(i, n), sc.y(f.idx)])).filter(Boolean);
  let s = yLabels([sc.lo + (sc.hi - sc.lo) * 0.08, (sc.lo + sc.hi) / 2, sc.hi - (sc.hi - sc.lo) * 0.08], sc.y, L - 6, v => Math.round(v).toLocaleString('en-IN'));
  // each leg's strike while it was open
  const fi = t => { const i = F.findIndex(f => f.t >= t); return i < 0 ? n - 1 : Math.max(0, i - 1); };
  for (const l of strikes) {
    const a = xOf(fi(l.t0), n), b = xOf(l.t1 ? fi(l.t1) : n - 1, n), Y = sc.y(l.strike);
    const c = l.kind === 'CE' ? 'var(--c5)' : 'var(--c4)';
    s += `<line x1="${a}" x2="${Math.max(a + 2, b)}" y1="${Y}" y2="${Y}" stroke="${c}" stroke-width="2"${l.side === 'long' ? ' stroke-dasharray="5 3"' : ''} opacity=".8"/>`;
    s += hit(a, Y - 4, Math.max(2, b - a), 8, `${l.symbol}\n${l.side === 'short' ? 'Sold' : 'Bought'}  ${l.qty} @ ${price(l.entry)}\nStrike  ${l.strike}\nOpened  ${dStamp(l.t0)}\n${l.t1 ? `Closed  ${dStamp(l.t1)}` : 'Still open'}`);
  }
  s += both(path(pts), 'var(--ink)', 1.8);
  return frame(D, H, `rpI${++uid}`, s, `${D.index_name} with the open legs’ strikes`, {
    series: [{ name: D.index_name, color: 'var(--ink)', pts: F.map((f, i) => [xOf(i, n), f.idx == null ? 0 : sc.y(f.idx), f.idx == null ? '—' : price(f.idx), dStamp(f.t)]) }],
  });
}

function vixChart(D) {
  const F = D.frames, n = F.length, H = 120;
  if (!F.some(f => f.vix != null)) return empty('', D.market_error || 'No India VIX history for this window.');
  const sc = scale(F.map(f => f.vix), H);
  const pts = F.map((f, i) => (f.vix == null ? null : [xOf(i, n), sc.y(f.vix)])).filter(Boolean);
  let s = yLabels([sc.lo + (sc.hi - sc.lo) * 0.1, sc.hi - (sc.hi - sc.lo) * 0.1], sc.y, L - 6, v => v.toFixed(1));
  s += both(path(pts), 'var(--wr)', 1.6);
  return frame(D, H, `rpV${++uid}`, s, 'India VIX', {
    series: [{ name: 'India VIX', color: 'var(--wr)', pts: F.map((f, i) => [xOf(i, n), f.vix == null ? 0 : sc.y(f.vix), f.vix == null ? '—' : f.vix.toFixed(2), dStamp(f.t)]) }],
  });
}

function pnlChart(D) {
  const F = D.frames, n = F.length, H = 220;
  const sc = scale([...F.map(f => f.pnl), ...F.map(f => f.booked)], H, 10, 28, 0.08, true);
  const pts = F.map((f, i) => [xOf(i, n), sc.y(f.pnl)]);
  const z = sc.y(0), d = path(pts);
  const area = `${d}L${pts[n - 1][0].toFixed(1)} ${z.toFixed(1)}L${pts[0][0].toFixed(1)} ${z.toFixed(1)}Z`;
  const id = `rpP${++uid}`;
  let s = `<clipPath id="${id}u"><rect x="0" y="0" width="${WF}" height="${z}"/></clipPath><clipPath id="${id}d"><rect x="0" y="${z}" width="${WF}" height="${H}"/></clipPath>`;
  s += yLabels([0, Math.min(...F.map(f => f.pnl)), Math.max(...F.map(f => f.pnl))], sc.y, L - 6);
  s += `<line x1="${L}" x2="${R}" y1="${z}" y2="${z}" stroke="var(--ink)" stroke-width=".8"/>`;
  s += `<g __CLIP__><path d="${area}" fill="var(--gs)" clip-path="url(#${id}u)"/><path d="${area}" fill="var(--rs)" clip-path="url(#${id}d)"/></g>`;
  s += both(path(F.map((f, i) => [xOf(i, n), sc.y(f.booked)])), 'var(--mute)', 1.2, ' stroke-dasharray="5 4"');
  s += both(d, 'var(--ink)', 1.8);
  return frame(D, H, id, s, 'P&L through the replay', {
    series: [{ name: 'P&L', color: 'var(--ink)', pts: F.map((f, i) => [xOf(i, n), sc.y(f.pnl), inr(f.pnl), dStamp(f.t)]) },
      { name: 'Booked', color: 'var(--mute)', pts: F.map((f, i) => [xOf(i, n), sc.y(f.booked), inr(f.booked)]) }],
  });
}

const SPLIT = [['Time decay', 'var(--g)'], [null, 'var(--ink)'], ['Volatility', 'var(--wr)']];
function splitChart(D) {
  const F = D.frames, n = F.length, H = 170;
  if (!F.some(f => f.split)) return empty('', 'Needs each option leg’s strike, expiry and the index at entry (and a price that implies a volatility).');
  const names = SPLIT.map(([l, c], k) => [k === 1 ? `${D.index_name} move` : l, c]);
  const sc = scale(F.flatMap(f => f.split || []), H, 10, 28, 0.08, true);
  let s = yLabels([0, sc.lo, sc.hi].map(v => v), sc.y, L - 6);
  s += `<line x1="${L}" x2="${R}" y1="${sc.y(0)}" y2="${sc.y(0)}" stroke="var(--line2)"/>`;
  names.forEach(([, c], k) => {
    const pts = F.map((f, i) => (f.split ? [xOf(i, n), sc.y(f.split[k])] : null)).filter(Boolean);
    s += both(path(pts), c, 1.6);
  });
  return frame(D, H, `rpS${++uid}`, s, 'Where the P&L came from', {
    series: names.map(([nm, c], k) => ({ name: nm, color: c, pts: F.map((f, i) => [xOf(i, n), f.split ? sc.y(f.split[k]) : 0, f.split ? inr(f.split[k]) : '—', dStamp(f.t)]) })),
  });
}

// ── the side: now, the event, the legs ─────────────────────────────────

function nowPanel(D, i) {
  const F = D.frames, f = F[i], e = eventAt(D.events, i), from = e ? e.frame : 0;
  const row = (l, v, d0, d1, fmt, cls = '') => html`<div class="rp-kv"><span>${l}</span><b class="${cls}">${v}</b>
    <span class="mono tiny mute" title="since the start · since the last event">${fmt(d0)} · ${fmt(d1)}</span></div>`;
  const pts = v => sgn(v, 0), vx = v => sgn(v, 2), rs = v => (v == null ? '—' : inr(v));
  return html`<div class="rp-now">
    <div class="rp-when"><b>${dStamp(f.t)}</b><span class="mono tiny mute">step ${i + 1} of ${F.length}</span></div>
    ${row(D.index_name, f.idx == null ? '—' : price(f.idx), change(F, 0, i, 'idx'), change(F, from, i, 'idx'), pts)}
    ${row('India VIX', f.vix == null ? '—' : f.vix.toFixed(2), change(F, 0, i, 'vix'), change(F, from, i, 'vix'), vx)}
    ${row('P&L', inr(f.pnl), change(F, 0, i, 'pnl'), change(F, from, i, 'pnl'), rs, tone(f.pnl))}
    <div class="rp-kv"><span>Booked · open</span><b>${inr(f.booked)} · ${inr(f.pnl - f.booked)}</b><span></span></div>
    ${f.split ? html`<div class="rp-split">${SPLIT.map(([l], k) => html`<span><i style="background:${SPLIT[k][1]}"></i>${k === 1 ? `${D.index_name} move` : l}<b class="${tone(f.split[k])}">${inr(f.split[k])}</b></span>`)}</div>` : ''}
    <p class="tiny mute" style="margin:0">Changes: since the start · since the last event</p>
  </div>`;
}

function legsTable(D, i) {
  const f = D.frames[i];
  if (!f.legs.length) return html`<p class="small mute" style="margin:0">Flat: no open legs at this step.</p>`;
  return table([['Leg', 'minmax(110px,1fr)'], ['Entry', '64px', 'num'], ['Now', '64px', 'num'], ['P&L', '80px', 'num']],
    f.legs.map(([j, px, pnl, stale]) => {
      const l = D.legs[j];
      return [html`<b class="mono small ${l.kind === 'CE' ? 'leg-ce' : l.kind === 'PE' ? 'leg-pe' : ''}">${l.kind && l.strike ? `${l.kind} ${l.strike}` : l.symbol}</b> <span class="tiny mute">${l.side === 'short' ? 'sold' : 'bought'} ${l.qty}</span>`,
        price(l.entry),
        html`<span class="${stale ? 'mute' : ''}" title="${stale ? 'no price yet: at its entry' : ''}">${price(px)}</span>`,
        html`<span class="${tone(pnl)}">${inr(pnl)}</span>`];
    }), { minw: 0 });
}

function eventCard(D, i) {
  const e = eventAt(D.events, i);
  if (!e) return html`<div class="rp-ev-card mute small">Before the first event.</div>`;
  const [lab, c] = KIND[e.kind] || KIND.entry;
  const here = e.frame === i;
  return html`<div class="rp-ev-card ${here ? 'here' : ''}" style="--k:${c}">
    <div class="row base" style="gap:8px;flex-wrap:wrap"><span class="rp-chip" style="--k:${c}">${lab}${e.cycle != null && e.kind === 'new' ? ` ${e.cycle}` : ''}</span>
      <b class="small">${e.rule}</b><span class="grow"></span><span class="mono tiny mute">${dStamp(e.t)}</span></div>
    ${e.why ? html`<p class="tiny mute" style="margin:0">Saw: ${e.why}</p>` : ''}
    <div class="col" style="gap:2px">${e.fills.map(f => html`<span class="mono tiny">${f.action === 'sell' ? 'Sold' : 'Bought'} ${f.qty} ${f.symbol} @ ${price(f.price)}${f.opening ? '' : f.pnl != null ? html` · <span class="${tone(f.pnl)}">${inr(f.pnl)}</span>` : ''}</span>`)}</div>
    ${e.booked != null ? html`<p class="small" style="margin:0">Booked <b class="${tone(e.booked)}">${inr(e.booked)}</b></p>` : ''}
    ${e.closed_before ? html`<p class="tiny" style="margin:0">First closed ${e.closed_before.cycle != null ? `cycle ${e.closed_before.cycle}` : 'the episode before'} (${e.closed_before.fills.length} leg${e.closed_before.fills.length === 1 ? '' : 's'})${e.closed_before.booked != null ? html`, booking <b class="${tone(e.closed_before.booked)}">${inr(e.closed_before.booked)}</b>` : ''}.</p>` : ''}
    ${e.since ? html`<p class="tiny" style="margin:0">Since the ${dStamp(e.since.t)} event: ${D.index_name} <b>${sgn(e.since.idx)}</b> · VIX <b>${sgn(e.since.vix, 2)}</b> · P&L <b class="${tone(e.since.pnl)}">${e.since.pnl == null ? '—' : inr(e.since.pnl)}</b></p>` : ''}
    ${here ? '' : html`<p class="tiny mute" style="margin:0">The latest event; the replay is ${i - e.frame} step${i - e.frame === 1 ? '' : 's'} past it.</p>`}
  </div>`;
}

function eventList(D, i) {
  let day = '';
  return html`<div class="rp-list">${D.events.map((e, k) => {
    const d = dShort(e.t), first = d !== day; day = d;
    const [lab, c] = KIND[e.kind] || KIND.entry;
    const cur = eventAt(D.events, i);
    return html`${first ? html`<div class="cy-day">${d}</div>` : ''}<button type="button" class="rp-li ${e.frame > i ? 'later' : ''} ${cur === e ? 'on' : ''}" data-act="jump" data-f="${e.frame}" data-k="${k}">
      <span class="mono tiny">${hhmm(e.t)}</span><span class="rp-chip" style="--k:${c}">${lab}</span>
      <span class="small" style="min-width:0">${e.rule}${e.booked != null ? html` · <span class="${tone(e.booked)}">${inr(e.booked)}</span>` : ''}</span></button>`;
  })}</div>`;
}

// ── the view ───────────────────────────────────────────────────────────

export default {
  render(el, v, g) {
    const p = g.params || {};
    let D = null, i = 0, playing = false, timer = null, speed = 1, pauseAt = true;

    function picker() {
      const E = D.episodes, [a, z] = D.selected;
      const opt = sel => E.map(e => html`<option value="${e.n}" ${e.n === sel ? 'selected' : ''}>${e.label} · ${dShort(e.t0)}${e.open ? ' · open' : ''}</option>`);
      return html`<div class="rp-pick">
        <label class="small">From <select data-pick="from">${opt(a)}</select></label>
        <label class="small">to <select data-pick="to">${opt(z)}</select></label>
        <span class="tiny mute">${z > a ? `a chain of ${z - a + 1}, played as one` : 'one episode'} · ${D.interval === 'minute' ? '1-minute' : D.interval.replace('minute', '-minute')} steps · nights and weekends skipped</span>
      </div>`;
    }

    function episodes() {
      const E = [...D.episodes].reverse().slice(0, 40), [a, z] = D.selected;
      return table([['', 'minmax(110px,1fr)'], ['Opened', '110px'], ['Closed', '110px'], ['Moves', '64px', 'num'], ['Booked', '96px', 'num']],
        E.map(e => [html`<b class="small">${e.label}</b>${e.n >= a && e.n <= z ? html` <span class="tiny mute">· replaying</span>` : ''}`, html`<span class="mono tiny">${dStamp(e.t0)}</span>`,
          html`<span class="mono tiny">${e.open ? 'open' : dStamp(e.t1)}</span>`, String(e.adjustments), html`<span class="${tone(e.booked)}">${money(e.booked)}</span>`]),
        { minw: 0, rowAttrs: k => html`data-ep="${E[k].n}" role="button" tabindex="0" style="cursor:pointer"` });
    }

    function controls() {
      const n = D.frames.length;
      return html`<div class="rp-ctl">
        <button type="button" class="btn btn-sm" data-act="to" data-f="0" aria-label="To the start">⏮</button>
        <button type="button" class="btn btn-sm" data-act="prevEv" aria-label="Previous event">◀◀</button>
        <button type="button" class="btn btn-sm rp-play" data-act="play" aria-label="${playing ? 'Pause' : 'Play'}">${playing ? '❚❚ Pause' : '▶ Play'}</button>
        <button type="button" class="btn btn-sm" data-act="nextEv" aria-label="Next event">▶▶</button>
        <button type="button" class="btn btn-sm" data-act="to" data-f="${n - 1}" aria-label="To the end">⏭</button>
        <span class="rp-speed">${SPEEDS.map(([, l], k) => html`<button type="button" class="${k === speed ? 'on' : ''}" data-act="speed" data-s="${k}">${l}</button>`)}</span>
        <label class="small row" style="gap:6px"><input type="checkbox" data-pause ${pauseAt ? 'checked' : ''}> Stop at each event</label>
        <input class="rp-range" type="range" min="0" max="${n - 1}" value="${i}" data-range aria-label="Replay position">
        <span class="tiny mute">Space · ← → · [ ]</span>
      </div>`;
    }

    function paint() {
      if (!D) { mount(el, html`${head('Replay', 'how a cycle went, step by step')}${loading(6)}`); return; }
      if (D instanceof Error) { mount(el, html`${head('Replay')}${empty('The replay didn’t load', D.message)}`); return; }
      if (!D.episodes.length) { mount(el, html`${head('Replay')}${empty('Nothing to replay yet', 'A replay appears once this deployment has traded.')}`); return; }
      if (!D.frames || !D.frames.length) { mount(el, html`${head('Replay')}${picker()}${empty('No steps for this window', D.market_error || 'No prices were found for it.')}`); return; }
      i = Math.max(0, Math.min(D.frames.length - 1, i));
      mount(el, html`${head('Replay', `${D.index_name}, VIX, the legs and the P&L, step by step — every adjustment and new cycle labelled`, D.market_error ? 'index and VIX unavailable' : '')}
        ${picker()}
        ${controls()}
        <div class="rp-grid">
          <div class="col" style="gap:18px;min-width:0">
            <div class="gk-panel"><h3>${D.index_name} <span class="s">with each open leg’s strike, in its leg colour (dashed: bought) · vertical lines: events</span></h3>${indexChart(D)}</div>
            <div class="gk-panel"><h3>India VIX</h3>${vixChart(D)}</div>
            <div class="gk-panel"><h3>P&L <span class="s">booked plus the open legs at each step · dashed: booked</span></h3>${pnlChart(D)}</div>
            <div class="gk-panel"><h3>Where the P&L came from <span class="s">each leg revalued one change at a time at the volatility its entry price implied${D.split_legs < D.legs.length ? ` · ${D.split_legs} of ${D.legs.length} legs` : ''}</span></h3>${splitChart(D)}</div>
          </div>
          <aside class="col rp-side" style="gap:16px;min-width:0">
            <div class="gk-panel"><h3>Now</h3><div data-rp="now"></div></div>
            <div class="gk-panel"><h3>The event</h3><div data-rp="ev"></div></div>
            <div class="gk-panel"><h3>Open legs</h3><div data-rp="legs"></div></div>
          </aside>
        </div>
        <div class="gk-two">
          <div class="gk-panel"><h3>Every event <span class="s">${D.events.length} · click one to jump to it</span></h3><div data-rp="list"></div></div>
          <div class="gk-panel"><h3>Episodes <span class="s">click one to replay it · pick From and To above for a chain</span></h3>${episodes()}</div>
        </div>
        ${D.unpriced && D.unpriced.length ? html`<p class="gk-note">No stored prices for ${D.unpriced.join(', ')}: shown at entry until they close.</p>` : ''}`);
      update();
    }

    // per step: only the playhead, the reveal and the side panels
    function update() {
      if (!D || !D.frames || !D.frames.length) return;
      const n = D.frames.length, X = xOf(i, n);
      el.querySelectorAll('[data-rp-head]').forEach(l => {
        l.setAttribute('x1', X); l.setAttribute('x2', X);
        // a chart that scrolls sideways (a phone) keeps the playhead in view
        const box = l.closest('.gk-wide');
        if (box && box.scrollWidth > box.clientWidth) {
          const at = X / WF * box.scrollWidth;
          if (at < box.scrollLeft + 24 || at > box.scrollLeft + box.clientWidth - 24) box.scrollLeft = Math.max(0, at - box.clientWidth / 2);
        }
      });
      el.querySelectorAll('[data-rp-clip]').forEach(r => r.setAttribute('width', X));
      const set = (k, h) => { const t = el.querySelector(`[data-rp="${k}"]`); if (t) mount(t, h); };
      set('now', nowPanel(D, i));
      set('ev', eventCard(D, i));
      set('legs', legsTable(D, i));
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

    const off = actions(el, {
      play: () => (playing ? stop() : play()),
      to: d => { stop(); to(+d.f); },
      jump: d => { stop(); to(+d.f); },
      nextEv: () => { stop(); const e = nextEvent(D.events, i); to(e ? e.frame : D.frames.length - 1); },
      prevEv: () => { stop(); const e = prevEvent(D.events, i); to(e ? e.frame : 0); },
      speed: d => { speed = +d.s; el.querySelectorAll('.rp-speed button').forEach((b, k) => b.classList.toggle('on', k === speed)); if (playing) play(); },
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
      // a click on a chart moves the playhead there
      on(el, 'click', '.rp-grid svg.gk-chart', (e, t) => {
        if (!D || !D.frames) return;
        const r = t.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * WF;
        stop(); to(Math.round((x - L) / (R - L) * (D.frames.length - 1)));
      }),
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
