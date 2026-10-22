// Session sheet: one trading day (intraday) or one cycle (positional),
// full screen. The underlying above the combined P&L on one time axis,
// with entry / adjustment / exit markers, then every leg's final status
// and every order. Opened from Daily stats or Cycles; Newer / Older step
// through the rest.
// The same body also sits inline on Geek mode's cycle page (sessionBody).
// Cycles are numbered oldest first everywhere: the newest of 16 is 16.
//
// Data: GET /deployments/{id}/cycles/{key}/chart. The underlying comes
// from Kite's history; option legs from the app's 1-minute option store
// (recorded live, filled from Kite, kept after expiry), rolled up to the
// chart's interval; older legs fall back to the app's per-leg marks.
// The interval is whatever the server picked for the window; there's
// no picker.

import { api } from '../../core/api.js';
import { html, raw, mount } from '../../core/dom.js';
import { inr, inrS, pct, price, plural, tone, toneVar, hhmm, ist, dayKey, dFull, WEEKDAYS, MONTHS } from '../../core/format.js';
import { pick, basis } from '../../core/basis.js';
import { registerScrub } from '../../ui/tooltip.js';
import { empty, loading } from '../../ui/components.js';
import { spanMark } from '../../ui/repairs.js';

// Event types: label and colour. 'rollout' is the strangle's EOD roll-out.
const KINDS = { entry: ['Entry', 'var(--ink)'], adjustment: ['Adjustment', 'var(--b)'], rollout: ['EOD roll-out', 'var(--c5)'], exit: ['Exit', null] };
const kindLabel = k => (KINDS[k] || KINDS.adjustment)[0];
// The reason after the label, minus what the label already says.
const reasonOf = e => (e.reason || '').split(', ').filter(r => r && r !== 'eod_rollout').join(', ');
const INTERVAL = { minute: '1-minute', '5minute': '5-minute', '15minute': '15-minute', '60minute': 'hourly' };
const listCache = new Map();    // deployment id -> cycles list
const chartCache = new Map();   // `${id}:${key}` -> chart (closed cycles only)

const dayOf = t => ist(t).key;
const near = (T, t) => { let b = 0; for (let k = 1; k < T.length; k++) if (Math.abs(T[k] - t) < Math.abs(T[b] - t)) b = k; return b; };

// Chart payload -> everything the sheet draws.
function model(v, row, ch) {
  const intra = v.modeKey === 'intraday';
  const off = ch.series.eval_offset_ms || 0;
  const T = ch.series.t.map(t => t + off), N = T.length;
  const U = ch.underlying && ch.underlying.length === N ? ch.underlying.map(c => c.c) : [];
  const start = ch.cycle.start, end = ch.cycle.end || Date.now();
  const inWin = t => t >= start - off && t <= end + off;
  // Per-leg total, else (some leg has no prices) the account snapshots.
  const raw = ch.series.pnl;
  const snaps = ch.snapshot_pnl || [];
  const useSnap = snaps.length && raw.some((p, k) => p == null && inWin(T[k]));
  const snapAt = t => { let val = 0; for (const s of snaps) { if (s.t <= t) val = s.pnl; else break; } return val; };
  const P = T.map((t, k) => (!inWin(t) ? null : useSnap ? snapAt(t) : raw[k]));
  const openK = P.map((p, k) => [p, k]).filter(([p]) => p != null);
  const e0 = openK.length ? openK[0][1] : 0, ex = openK.length ? openK[openK.length - 1][1] : N - 1;
  const pk = openK.reduce((a, b) => (b[0] > a[0] ? b : a), [-Infinity, 0]);
  const wk = openK.reduce((a, b) => (b[0] < a[0] ? b : a), [Infinity, 0]);
  const red = openK.length ? openK.filter(([p]) => p < 0).length / openK.length : 0;
  const final = pick(row, 'realized_pnl') ?? 0;
  const finalGross = Number(row.realized_pnl) || 0;
  const events = (ch.events || []).map(e => ({ ...e, k: near(T, e.t) }));
  const entries = events.filter(e => e.kind === 'entry').length;
  const isAdj = e => e.kind === 'adjustment' || e.kind === 'rollout';
  const nAdj = intra ? Math.max(0, entries - 1) + events.filter(isAdj).length : events.filter(isAdj).length;
  const nRoll = events.filter(e => e.kind === 'rollout').length;
  const when = k => (intra ? hhmm(T[k]) : `${WEEKDAYS[ist(T[k]).wd]} ${ist(T[k]).d} ${MONTHS[ist(T[k]).mo]} ${hhmm(T[k])}`);
  const seps = intra ? [] : T.map((t, k) => (k && dayOf(t) !== dayOf(T[k - 1]) ? k : null)).filter(k => k != null);
  const X = k => (N > 1 ? k / (N - 1) : 0);
  let ticks;
  if (intra) ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(f * (N - 1))).map(k => ({ k, l: hhmm(T[k]) }));
  else {
    const all = [0, ...seps].map(k => ({ k, l: `${ist(T[k]).d} ${MONTHS[ist(T[k]).mo]}` }));
    // Keep labels at least ~14% of the width apart so they never touch.
    ticks = [];
    all.forEach(t => { if (!ticks.length || X(t.k) - X(ticks[ticks.length - 1].k) >= 0.14) ticks.push(t); });
  }
  const mv = U.length ? U[ex] - U[e0] : null;
  const kept = pk[0] > 0 && finalGross > 0 ? Math.round(finalGross / pk[0] * 100) : null;
  return { intra, T, N, U, P, e0, ex, pk, wk, red, final, finalGross, events, nAdj, nRoll, when, seps, X, ticks, mv, kept, start, end };
}

function chart(m, sym, ch, embed = false) {
  const { U, P, N, X } = m;
  const vals = P.filter(p => p != null);
  const pLo = Math.min(0, ...vals), pHi = Math.max(0, ...vals);
  const pY = q => 6 + (1 - (q - pLo) / ((pHi - pLo) || 1)) * 108;
  const uLo = U.length ? Math.min(...U) : 0, uHi = U.length ? Math.max(...U) : 1;
  const uY = q => 24 + (1 - (q - uLo) / ((uHi - uLo) || 1)) * 150;   // top 24px kept clear for the high label
  const x = k => (X(k) * 1000).toFixed(1);
  const uPath = U.map((q, k) => `${k ? 'L' : 'M'}${x(k)} ${uY(q).toFixed(1)}`).join(' ');
  const pk = P.map((p, k) => [p, k]).filter(([p]) => p != null);
  const pPath = pk.map(([p, k], i) => `${i ? 'L' : 'M'}${x(k)} ${pY(p).toFixed(1)}`).join(' ');
  const z = pY(0);
  const pFill = pk.length ? `${pPath} L${x(pk[pk.length - 1][1])} ${z.toFixed(1)} L${x(pk[0][1])} ${z.toFixed(1)} Z` : '';
  const lineC = toneVar(m.finalGross);
  const cid = `ssp${Math.random().toString(36).slice(2, 8)}`;   // clip ids, unique per render
  const sk = embed ? 'ssi' : 'ss';   // scrub key: the sheet's, or the inline copy's (both can be on screen)
  const evC = e => (KINDS[e.kind] && KINDS[e.kind][1]) || lineC;
  const evL = e => `${kindLabel(e.kind)}${reasonOf(e) ? ` · ${reasonOf(e)}` : ''}`;
  const pc = k => `${X(k) * 100}%`;
  const tipAt = k => [m.when(k), U.length ? `${sym}  ${price(U[k])}` : null,
    U.length && k >= m.e0 ? `From entry  ${U[k] - U[m.e0] >= 0 ? '+' : '−'}${Math.abs(U[k] - U[m.e0]).toFixed(1)} pts` : null,
    `Combined P&L  ${P[k] != null ? inr(P[k]) : k < m.e0 ? 'not entered' : `closed at ${inr(m.finalGross)}`}`].filter(Boolean).join('\n');
  const idx = f => Math.max(0, Math.min(N - 1, Math.round(f * (N - 1))));
  // Both panels scrub together: one guide through the index and the
  // P&L, a dot on each line, the same moment in the card.
  registerScrub(sk, f => {
    const k = idx(f), plot = document.querySelector(`.ss-plot[data-plot="${sk}"]`);
    if (!plot) return null;
    const u = plot.querySelector('.ss-u'), pl = plot.querySelector('.ss-p');
    return {
      t: tipAt(k), span: plot,
      pts: [u && U.length ? { el: u, y: uY(U[k]) / 180, c: 'var(--ink)' } : null,
            pl && P[k] != null ? { el: pl, y: pY(P[k]) / 120, c: P[k] >= 0 ? 'var(--g)' : 'var(--r)' } : null].filter(Boolean),
    };
  });

  return html`
    <div class="ss-marks">${m.events.map(e => html`<i style="left:${pc(e.k)};background:${evC(e)}" data-tip="${evL(e)}\n${m.when(e.k)}${U.length ? `\n${sym}  ${price(U[e.k])}` : ''}${P[e.k] != null ? `\nCombined P&L  ${inr(P[e.k])}` : ''}"></i>`)}</div>
    <div class="ss-plot" data-plot="${sk}">
      <div class="ss-open" style="left:${pc(m.e0)};width:${(X(m.ex) - X(m.e0)) * 100}%"></div>
      ${m.seps.map(k => html`<div class="ss-sep" style="left:${pc(k)}"></div>`)}
      ${m.events.map(e => html`<div class="ss-evl" style="left:${pc(e.k)};border-color:${evC(e)}"></div>`)}
      ${U.length ? html`
        <div class="ss-lab" style="top:4px">${price(uHi)}</div>
        <svg data-scrub="${sk}" viewBox="0 0 1000 180" preserveAspectRatio="none" class="ss-u" aria-label="${sym} price">${raw(`<path d="${uPath}" fill="none" stroke="var(--ink)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`)}</svg>`
        : html`<div class="ss-nou">${ch.underlying_error || 'No underlying history for this window.'}</div>`}
      <div class="ss-mid"><span>${sym}${U.length ? ` · low ${price(uLo)}` : ''}</span><span>Combined P&L · high ${inrS(pHi)}</span></div>
      <svg data-scrub="${sk}" viewBox="0 0 1000 120" preserveAspectRatio="none" class="ss-p" aria-label="Combined P&L">${raw(`
        <defs><clipPath id="${cid}-up"><rect x="0" y="0" width="1000" height="${z.toFixed(1)}"/></clipPath>
          <clipPath id="${cid}-dn"><rect x="0" y="${z.toFixed(1)}" width="1000" height="${(120 - z).toFixed(1)}"/></clipPath></defs>
        <line x1="0" x2="1000" y1="${z.toFixed(1)}" y2="${z.toFixed(1)}" stroke="var(--line2)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
        <g clip-path="url(#${cid}-up)"><path d="${pFill}" fill="var(--g)" opacity=".14"/>
          <path d="${pPath}" fill="none" stroke="var(--g)" stroke-width="2" vector-effect="non-scaling-stroke"/></g>
        <g clip-path="url(#${cid}-dn)"><path d="${pFill}" fill="var(--r)" opacity=".14"/>
          <path d="${pPath}" fill="none" stroke="var(--r)" stroke-width="2" vector-effect="non-scaling-stroke"/></g>`)}</svg>
    </div>
    <div class="ss-x">${m.ticks.map(t => html`<span style="left:${pc(t.k)}">${t.l}</span>`)}</div>
    <div class="ss-legend">
      <span><i style="width:18px;height:10px;background:var(--soft)"></i>Position open</span>
      <span><i class="d" style="background:var(--ink)"></i>Entry</span>
      <span><i class="d" style="background:var(--b)"></i>${m.intra ? 'Adjustment / re-entry' : 'Adjustment'}</span>
      ${m.events.some(e => e.kind === 'rollout') ? html`<span><i class="d" style="background:var(--c5)"></i>EOD roll-out</span>` : ''}
      <span><i class="d" style="background:${lineC}"></i>Exit</span>
      ${m.seps.length ? html`<span><i style="width:0;height:12px;border-left:1px dashed var(--line2)"></i>New trading day</span>` : ''}
      <span class="grow"></span><span>${INTERVAL[ch.window.interval] || ch.window.interval} data · index ${({ kite: 'from Kite history', stored: 'stored 1-minute history', stored_partial: 'stored history, some gaps' })[ch.underlying_source] || 'unavailable'}</span>
    </div>`;
}

// Cycle number, oldest first: the strategy's own when it keeps one.
export const cycleNo = (L, i) => (L[i] && L[i].cycle_id != null ? L[i].cycle_id : L.length - i);

function body(v, row, ch, i, L, embed = false) {
  const m = model(v, row, ch);
  const sym = ch.underlying_symbol || v.index;
  const nDays = new Set(m.T.slice(m.e0, m.ex + 1).map(dayOf)).size || 1;
  const stats = [
    // coloured by sign: a worst point still in profit is green, a peak still in loss red
    ['Peak', m.pk[0] > -Infinity ? inr(m.pk[0]) : '—', m.pk[0] > -Infinity ? `at ${m.when(m.pk[1])}` : '', m.pk[0] > -Infinity ? tone(m.pk[0]) : ''],
    ['Worst', m.wk[0] < Infinity ? inr(m.wk[0]) : '—', m.wk[0] < Infinity ? `at ${m.when(m.wk[1])}` : '', m.wk[0] < Infinity ? tone(m.wk[0]) : ''],
    [`${sym} move`, m.mv == null ? '—' : `${m.mv >= 0 ? '+' : '−'}${Math.abs(m.mv).toFixed(0)} pts`, m.mv == null ? 'no underlying history' : `${pct(m.mv / m.U[m.e0] * 100)} entry to exit`, ''],
    ['Kept of peak', m.kept == null ? '—' : `${m.kept}%`, m.finalGross <= 0 ? (m.pk[0] > 0 ? `peak was ${inr(m.pk[0])}, closed below ₹0` : 'never in profit') : `gave back ${inr(Math.max(0, m.pk[0] - m.finalGross), { sign: false })}`, ''],
    ['Time in red', `${Math.round(m.red * 100)}%`, 'of the time it was open', ''],
    [m.intra ? 'Re-entries' : 'Adjustments', String(m.nAdj), m.nAdj ? `${m.nRoll ? `incl. ${plural(m.nRoll, 'EOD roll-out')} · ` : ''}markers on the chart` : 'ran untouched', ''],
  ];
  const note = `${m.wk[0] < 0 && Math.abs(m.wk[0]) > Math.abs(m.finalGross) * 0.5 && m.U.length ? `The worst point came at ${m.when(m.wk[1])}, with ${sym} ${Math.abs(m.U[m.wk[1]] - m.U[m.e0]).toFixed(0)} points from entry. ` : ''}${
    m.kept != null && m.kept < 60 ? `It closed with ${m.kept}% of its peak.` : m.finalGross < 0 && m.pk[0] > -Infinity ? `It closed ${inr(m.pk[0] - m.finalGross, { sign: false })} below its best point.` : row.status === 'open' ? 'Still open; the chart runs to now.' : 'It closed near its best point.'}`;

  const legs = ch.positions.map((p, j) => {
    const series = ch.series.pos[j] || [];
    const last = [...series].reverse().find(x => x != null);
    const pnl = p.status === 'closed' ? p.realized_pnl : last ?? 0;
    const rolled = p.status === 'closed' && p.closed_at < m.end - 120000;
    const at = p.closed_at ? m.when(near(m.T, p.closed_at)) : '';
    return { ...p, pnl, dim: rolled, stt: p.status === 'open' ? 'Open' : `${rolled ? 'Rolled' : 'Closed'} · ${at}` };
  });
  const legTot = legs.reduce((a, l) => a + l.pnl, 0);
  const title = m.intra ? dFull(m.start) : `Cycle #${cycleNo(L, i)}`;
  const kicker = m.intra ? `TRADING DAY · ${v.name.toUpperCase()}` : `CYCLE · ${plural(nDays, 'DAY').toUpperCase()} · ${v.name.toUpperCase()}`;
  const sub = m.intra
    ? `${plural(row.positions, 'leg')} · entered ${hhmm(m.start)} · ${row.status === 'open' ? 'still open' : `out ${hhmm(m.end)}`}`
    : `${m.when(m.e0)} → ${row.status === 'open' ? 'still open' : m.when(m.ex)}`;

  return html`
    ${embed ? '' : html`<div class="ss-head">
      <div class="col grow" style="gap:6px;min-width:240px"><span class="ss-kick">${kicker}</span><h1 class="t-h1">${title} ${spanMark(v, dayKey(m.start), dayKey(row.status === 'open' ? new Date() : m.end))}</h1><span class="mute">${sub}</span></div>
      <div class="col" style="align-items:flex-end;gap:2px"><b class="mono ss-res ${tone(m.final)}">${inr(m.final)}</b>
        <span class="small mute">${pct(m.final / v.cap * 100)} of ${inrS(v.cap, { sign: false })} capital${row.status === 'open' ? ' · realized so far' : ''}</span></div>
    </div>`}
    <div class="ss-stats">${stats.map(([l, val, s, c]) => html`<div><span class="l">${l}</span><b class="mono ${c}">${val}</b><span class="s">${s}</span></div>`)}</div>
    <div>
      <div class="row" style="padding-bottom:12px"><h2 class="t-h3">${sym} and combined P&L</h2></div>
      ${chart(m, sym, ch, embed)}
      <p class="pretty" style="margin:12px 0 0;max-width:760px">${note}</p>
      ${(ch.notes || []).map(n => html`<p class="tiny mute" style="margin:6px 0 0;max-width:760px">${n}</p>`)}
      ${basis() !== 'gross' ? html`<p class="tiny mute" style="margin:6px 0 0">The chart is gross. The result above follows your P&L basis.</p>` : ''}
    </div>
    <div class="grid-two" style="gap:36px">
      <div class="scroll-x">
        <h2 class="t-h3" style="padding-bottom:10px">Positions, final status</h2>
        <div class="ss-leg th"><span>Leg</span><span class="right">Qty</span><span class="right hide-phone">Entry</span><span class="right hide-phone">Exit</span><span class="right">P&L</span><span>Status</span></div>
        ${legs.map(l => html`<div class="ss-leg ${l.dim ? 'dim' : ''}">
          <span class="col" style="gap:0"><b class="mono tiny">${l.symbol}</b><span class="tiny mute">${l.side === 'short' ? 'Sell' : 'Buy'}${l.option_type ? ` ${l.option_type}` : ''}${l.strike ? ` · ${l.strike}` : ''}</span></span>
          <span class="num">${Number(l.qty)}</span><span class="num hide-phone">${price(l.entry_price)}</span><span class="num hide-phone">${l.exit_price == null ? '—' : price(l.exit_price)}</span>
          <span class="num ${tone(l.pnl)}">${inr(l.pnl)}</span><span class="small mute">${l.stt}</span></div>`)}
        <div class="ss-leg tot"><span>Total</span><span></span><span class="hide-phone"></span><span class="hide-phone"></span><span class="num ${tone(legTot)}">${inr(legTot)}</span><span class="tiny mute">gross</span></div>
      </div>
      <div>
        <h2 class="t-h3" style="padding-bottom:10px">Orders and events</h2>
        <div class="ss-orders">${m.events.length ? m.events.map(e => html`<div>
          <i style="background:${(KINDS[e.kind] && KINDS[e.kind][1]) || toneVar(m.finalGross)}"></i>
          <span class="col" style="gap:2px;min-width:0"><b>${kindLabel(e.kind)}${reasonOf(e) ? html` <span class="mute" style="font-weight:500">· ${reasonOf(e)}</span>` : ''}</b>${e.kind === 'rollout' ? html`<span class="small mute">Tested side rolled out instead of converging into a straddle overnight</span>` : ''}
            ${e.fills.map(f => html`<span class="small mute">${f.action.toUpperCase()} ${Number(f.qty)} ${f.symbol} @ ${price(f.price)}</span>`)}</span>
          <span class="col" style="gap:2px;align-items:flex-end"><span class="mono tiny">${m.when(e.k)}</span>${m.U.length ? html`<span class="mono tiny mute">${sym} ${price(m.U[e.k])}</span>` : ''}</span></div>`) : empty('', 'No fills recorded.')}</div>
      </div>
    </div>`;
}

// The cycles list (newest first) and the index of the one at time `at`:
// same IST day (intraday), or the cycle containing that opening time
// (positional), else the nearest start.
async function listAt(v, at) {
  const L = listCache.get(v.id) || await api.cycles(v.id);
  listCache.set(v.id, L);
  setTimeout(() => listCache.delete(v.id), 60000);
  if (!L.length) return { L, i: -1 };
  const t = Number(at);
  let k = v.modeKey === 'intraday'
    ? L.findIndex(c => dayOf(c.start) === dayOf(t))
    : L.findIndex(c => new Date(c.start).getTime() <= t + 60000 && (c.end ? new Date(c.end).getTime() : Date.now()) >= t);
  if (k < 0) k = L.reduce((b, c, j) => (Math.abs(new Date(c.start) - t) < Math.abs(new Date(L[b].start) - t) ? j : b), 0);
  return { L, i: k };
}
async function chartOf(v, row) {
  const ck = `${v.id}:${row.key}`;
  const ch = chartCache.get(ck) || await api.cycleChart(v.id, row.key);
  if (row.status === 'closed') chartCache.set(ck, ch);
  return ch;
}

// For a page that shows one cycle: its chart, legs and orders, without
// the sheet's header. Resolves to { row, ch } (render with sessionBody).
export async function loadSession(v, at) {
  const { L, i } = await listAt(v, at);
  if (i < 0) throw new Error('No cycles recorded for this deployment yet.');
  return { L, i, row: L[i], ch: await chartOf(v, L[i]) };
}
export const sessionBody = (v, s) => html`<div class="ss-inline">${body(v, s.row, s.ch, s.i, s.L, true)}</div>`;

export async function openSession(v, at) {
  const el = document.createElement('div');
  el.className = 'sess';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', v.modeKey === 'intraday' ? 'Trading day' : 'Cycle');
  document.body.appendChild(el);
  document.body.style.overflow = 'hidden';
  const intra = v.modeKey === 'intraday';
  let L = null, i = 0, ch = null, err = '';

  const close = () => {
    el.remove(); document.body.style.overflow = '';
    document.removeEventListener('keydown', onKey); window.removeEventListener('hashchange', close);
  };
  const onKey = e => {
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') go(-1);
    if (e.key === 'ArrowRight') go(1);
  };
  document.addEventListener('keydown', onKey);
  window.addEventListener('hashchange', close);

  function paint() {
    const row = L && L[i];
    mount(el, html`
      <div class="ss-bar">
        <button type="button" class="b" data-ss="close">‹ ${intra ? 'Daily stats' : 'Cycles'}</button>
        <span class="grow"></span>
        ${L ? html`<span class="mono tiny mute nowrap">${L.length - i} of ${L.length}</span>
          <button type="button" class="btn btn-sm" data-ss="prev" ${i > 0 ? '' : 'disabled style="opacity:.3"'}>${intra ? html`‹ Newer<span class="hide-phone">&nbsp;day</span>` : '‹ Newer'}</button>
          <button type="button" class="btn btn-sm" data-ss="next" ${i < L.length - 1 ? '' : 'disabled style="opacity:.3"'}><span class="hide-phone">${intra ? 'Older day' : 'Older'}</span><span class="only-phone-in">Older</span> ›</button>` : ''}
        <button type="button" class="ss-x-btn" data-ss="close" aria-label="Close">×</button>
      </div>
      <div class="ss-body">
        ${err ? empty('Couldn’t load this chart', err) : !row || !ch ? loading(8) : body(v, row, ch, i, L)}
      </div>`);
  }
  async function load() {
    ch = null; err = ''; paint();
    const row = L[i];
    try { ch = await chartOf(v, row); } catch (e) { err = e.message; }
    if (L[i] === row) paint();
  }
  function go(d) {
    if (!L) return;
    const j = i + d;
    if (j < 0 || j >= L.length) return;
    i = j; el.scrollTop = 0; load();
  }
  el.addEventListener('click', e => {
    const b = e.target.closest('[data-ss]');
    if (!b || b.disabled) return;
    if (b.dataset.ss === 'close') close();
    if (b.dataset.ss === 'prev') go(-1);
    if (b.dataset.ss === 'next') go(1);
  });

  paint();
  try {
    ({ L, i } = await listAt(v, at));
  } catch (e) { err = e.message; paint(); return; }
  if (!L.length) { err = 'No cycles recorded for this deployment yet.'; paint(); return; }
  load();
}
