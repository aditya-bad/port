// Tools › Compare with underlying: an index (or any instrument) against
// the ETFs or stocks you pick, on one scale (% change since the start, or
// in the underlying's points), how far each strays from it candle by
// candle, and the figures (app/v2/track.py). Daily candles over a period,
// or one day in 5-minute candles. What you picked is remembered.

import { api } from '../core/api.js';
import { html, mount, $, actions, on, debounce } from '../core/dom.js';
import { pct, price, num, inrS, tone, dShort, hhmm, fromDay, dayKey } from '../core/format.js';
import { ui } from '../core/store.js';
import { chips, empty, loading } from '../ui/components.js';
import { line, yOf } from '../ui/charts.js';
import { registerScrub } from '../ui/tooltip.js';
import { say } from '../ui/toast.js';

export const SHORTCUTS = [['NIFTY', 'NIFTY 50'], ['BANKNIFTY', 'NIFTY BANK'], ['SENSEX', 'SENSEX'], ['BANKEX', 'BANKEX']];
const PERIODS = [['1M', '1 month', 30], ['3M', '3 months', 91], ['6M', '6 months', 182], ['1Y', '1 year', 365], ['3Y', '3 years', 1096], ['5Y', '5 years', 1826],
  ['custom', 'Dates'], ['intraday', 'One day, 5-min']];
const COLORS = ['var(--b)', 'var(--c4)', 'var(--c5)', 'var(--wt)', 'var(--g)', 'var(--r)', 'var(--b)', 'var(--c4)'];
const dashOf = i => (i >= 6 ? '6 4' : '');
const MAX = 8;
const st = ui('track', { base: { key: 'NIFTY', label: 'NIFTY 50' }, with: [], period: '1Y', start: '', end: '', day: '' });

const iso = d => dayKey(d);
// The dates asked for, from the period picked.
export function range(s, now = new Date()) {
  if (s.period === 'intraday') { const d = s.day || iso(now); return { start: d, end: d, interval: '5minute' }; }
  if (s.period === 'custom') return { start: s.start, end: s.end || iso(now), interval: 'day' };
  const days = (PERIODS.find(p => p[0] === s.period) || PERIODS[3])[2];
  return { start: iso(new Date(now.getTime() - days * 864e5)), end: iso(now), interval: 'day' };
}

const fmtT = (t, intraday) => (intraday ? `${dShort(new Date(t))} ${hhmm(new Date(t))}` : dShort(new Date(t)));
const pp = (v, dp = 2) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(dp)} pts`);

function legend(s, r) {
  return html`<div class="trk-legend tiny">
    <span><i class="key" style="background:var(--ink)"></i><b>${s.base.label}</b> (underlying)</span>
    ${r.others.map((o, i) => html`<span><i class="key ${dashOf(i) ? 'dash' : ''}" style="background:${COLORS[i]};border-color:${COLORS[i]}"></i>${labelOf(o.token)}</span>`)}</div>`;
}
let labels = new Map();
const labelOf = tok => labels.get(tok) || String(tok);

function chartLevels(s, r, unit, intraday) {
  const H = 260, base0 = r.base.c[0];
  const conv = v => (unit === 'pts' ? base0 * (1 + v / 100) : v);
  const bs = r.base.pct.map(conv), os = r.others.map(o => o.pct.map(conv));
  const all = [...bs, ...os.flat()], lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.06 || 1;
  const L = lo - pad, Hh = hi + pad, key = 'trk-levels';
  registerScrub(key, f => {
    const i = Math.round(f * (r.t.length - 1));
    const v = x => (unit === 'pts' ? num(x, 2) : pct(x, 2));
    return { t: `${fmtT(r.t[i], intraday)}\n${s.base.label}  ${v(bs[i])}${unit === 'pts' ? '' : `  (${price(r.base.c[i])})`}`
        + r.others.map((o, k) => `\n${labelOf(o.token)}  ${v(os[k][i])}${unit === 'pts' ? '' : `  (₹${price(o.c[i])})`}`).join(''),
      ys: [bs[i], ...os.map(x => x[i])].map(y => yOf(y, H, L, Hh) / H), cs: ['var(--ink)', ...r.others.map((_, k) => COLORS[k])] };
  });
  const yl = v => (unit === 'pts' ? num(v, 0) : pct(v, 1));
  return html`<div class="trk-plot" data-chart="levels"><span class="trk-y top tiny mono mute">${yl(Hh)}</span><span class="trk-y tiny mono mute" style="top:${H - 18}px">${yl(L)}</span>${line({ series: [
      ...os.map((v, k) => ({ values: v, color: COLORS[k], width: 1.5, dash: dashOf(k), label: labelOf(r.others[k].token) })),
      { values: bs, color: 'var(--ink)', width: 2.2, label: s.base.label }], h: H, key, lo: L, hi: Hh, refs: [unit === 'pts' ? base0 : 0] })}
    <div class="trk-axis tiny mute mono"><span>${fmtT(r.t[0], intraday)}</span><span>${unit === 'pts' ? `${s.base.label} points; each one scaled to start at ${num(base0, 2)}` : '% change since the start'}</span><span>${fmtT(r.t[r.t.length - 1], intraday)}</span></div></div>`;
}

function chartGap(r, intraday) {
  const H = 200, gs = r.others.map(o => o.gap);
  const all = gs.flat(), lo = Math.min(0, ...all), hi = Math.max(0, ...all), pad = (hi - lo) * 0.08 || 0.1;
  const L = lo - pad, Hh = hi + pad, key = 'trk-gap';
  registerScrub(key, f => {
    const i = Math.round(f * (r.t.length - 1));
    return { t: `${fmtT(r.t[i], intraday)}\nAhead (+) or behind (−) the underlying` + r.others.map((o, k) => `\n${labelOf(o.token)}  ${pp(gs[k][i])}`).join(''),
      ys: gs.map(g => yOf(g[i], H, L, Hh) / H), cs: r.others.map((_, k) => COLORS[k]) };
  });
  return html`<div class="trk-plot" data-chart="gap"><span class="trk-y top tiny mono mute">${pp(Hh)}</span><span class="trk-y tiny mono mute" style="top:${H - 18}px">${pp(L)}</span>${line({ series: gs.map((v, k) => ({ values: v, color: COLORS[k], width: 1.5, dash: dashOf(k), label: labelOf(r.others[k].token) })),
      h: H, key, lo: L, hi: Hh, zero: true })}
    <div class="trk-axis tiny mute mono"><span>${fmtT(r.t[0], intraday)}</span><span>0 = moving exactly with the underlying</span><span>${fmtT(r.t[r.t.length - 1], intraday)}</span></div></div>`;
}

function figures(s, r, intraday) {
  return html`<div class="tbl trk-tbl" role="table" data-track="figures">
    <div class="tr th trk-row" role="row"><span>Compared</span><span class="num">Its move</span><span class="num">Underlying</span><span class="num">Gap at the end</span>
      <span class="num">Tracking error</span><span class="num">Widest gap</span><span class="num">Average gap</span><span class="num">Correlation</span><span class="num">Beta</span><span class="num">Traded a ${intraday ? 'candle' : 'day'}</span></div>
    ${r.others.map((o, k) => { const x = o.stats; return html`<div class="tr trk-row" role="row" data-token="${o.token}">
      <span class="cell-name"><b><i class="key ${dashOf(k) ? 'dash' : ''}" style="background:${COLORS[k]};border-color:${COLORS[k]}"></i>${labelOf(o.token)}</b><span>${x.candles} ${intraday ? '5-minute candles' : 'days'}</span></span>
      <span class="num ${tone(x.ret * 100)}" data-l="Its move">${pct(x.ret, 2)}</span>
      <span class="num ${tone(x.base_ret * 100)}" data-l="Underlying">${pct(x.base_ret, 2)}</span>
      <span class="num ${tone(x.diff * 100)}" data-l="Gap at the end">${pp(x.diff)}</span>
      <span class="num" data-l="Tracking error" data-tip="${`${labelOf(o.token)}\nTracking error  ${x.te == null ? '—' : `${x.te.toFixed(3)} pts a ${intraday ? 'candle' : 'day'}`}${x.te_year != null ? `\nA year  ${x.te_year.toFixed(2)}%` : ''}`}">${x.te_year != null ? `${x.te_year.toFixed(2)}%` : x.te != null ? `${x.te.toFixed(3)} pts` : '—'}<small>${x.te_year != null ? 'a year' : 'a candle'}</small></span>
      <span class="num ${tone(x.max_gap * 100)}" data-l="Widest gap">${pp(x.max_gap)}<small>${fmtT(x.max_gap_at, intraday)}</small></span>
      <span class="num" data-l="Average gap">${pp(x.mean_gap)}</span>
      <span class="num" data-l="Correlation">${x.corr == null ? '—' : x.corr.toFixed(3)}</span>
      <span class="num" data-l="Beta">${x.beta == null ? '—' : x.beta.toFixed(3)}</span>
      <span class="num" data-l="Traded">${x.traded == null ? '—' : inrS(x.traded, { sign: false })}</span></div>`; })}
  </div>`;
}

function picker(s, view) {
  const res = (view.results || []).filter(x => ['NSE', 'BSE'].includes(x.exchange));
  const chosen = new Set(s.with.map(w => w.token));
  const p = s.period;
  return html`<div class="trk-pick">
    <div class="col" style="gap:8px"><b class="small">Underlying</b>
      <div class="row wrap" style="gap:6px" data-pick="base">${chips(SHORTCUTS, s.base.key || '', 'tbase')}
        ${s.base.token ? html`<span class="chip on" data-base-other>${s.base.label}</span>` : ''}
        <button type="button" class="chip ${view.mode === 'base' ? 'on' : ''}" data-act="tsearch" data-m="base">Other…</button></div></div>
    <div class="col" style="gap:8px"><b class="small">Compare with <span class="mute">(up to ${MAX})</span></b>
      <div class="row wrap" style="gap:6px" data-pick="with">${s.with.map((w, i) => html`<span class="chip on trk-w" data-w="${w.token}"><i class="key ${dashOf(i) ? 'dash' : ''}" style="background:${COLORS[i]};border-color:${COLORS[i]}"></i>${w.label}
          <button type="button" class="x" data-act="tdrop" data-t="${w.token}" aria-label="Remove ${w.label}">×</button></span>`)}
        ${s.with.length < MAX ? html`<button type="button" class="chip ${view.mode === 'with' ? 'on' : ''}" data-act="tsearch" data-m="with">+ Add</button>` : ''}</div></div>
    ${view.mode ? html`<div class="trk-search col" style="gap:6px">
      <input class="input" type="search" data-tq placeholder="${view.mode === 'base' ? 'Search an index or stock: NIFTY MIDCAP, RELIANCE…' : 'Search an ETF or stock: NIFTYBEES, SETFNIF50…'}" value="${view.q}" aria-label="Search instruments">
      ${view.err ? html`<span class="err small">${view.err}</span>` : ''}
      ${view.busy ? html`<span class="small mute"><i class="spin"></i> Searching…</span>` : view.q.length >= 2 ? (res.length ? html`<div class="trk-res">${res.slice(0, 20).map(x => html`<button type="button" class="trk-r ${chosen.has(x.instrument_token) ? 'on' : ''}" data-act="tchoose" data-t="${x.instrument_token}" data-s="${x.tradingsymbol}" data-x="${x.exchange}">
          <b class="mono">${x.tradingsymbol}</b><span class="tiny mute">${x.name || ''} · ${x.exchange}${x.segment === 'INDICES' ? ' · index' : ''}</span></button>`)}</div>`
          : html`<span class="small mute">Nothing on NSE or BSE matches.</span>`) : html`<span class="tiny mute">Type at least 2 letters. Needs Kite connected.</span>`}
    </div>` : ''}
    <div class="col" style="gap:8px"><b class="small">Period</b>
      <div class="row wrap" style="gap:6px" data-pick="period">${chips(PERIODS.map(x => [x[0], x[1]]), p, 'tperiod')}</div>
      ${p === 'custom' ? html`<div class="row wrap" style="gap:8px"><label class="row small" style="gap:6px">From <input class="input" type="date" data-td="start" value="${s.start}"></label>
        <label class="row small" style="gap:6px">To <input class="input" type="date" data-td="end" value="${s.end}"></label></div>` : ''}
      ${p === 'intraday' ? html`<label class="row small" style="gap:6px">Day <input class="input" type="date" data-td="day" value="${s.day || iso(new Date())}"></label>` : ''}</div>
  </div>`;
}

export function mountTrack(root) {
  const view = { mode: null, q: '', results: null, busy: false, err: '', r: null, loading: false, error: '', unit: 'pct', asked: '' };
  const s = () => st.get();
  for (const w of s().with) labels.set(w.token, w.label);

  function paint() {
    const S = s(), r = view.r, intraday = r && r.interval === '5minute';
    mount(root, html`<div class="stack trk" style="gap:22px">
      ${picker(S, view)}
      <div data-track="out">${!S.with.length ? empty('', 'Pick at least one ETF or stock to compare with the underlying.')
        : view.error ? html`<div data-track="error">${empty('Couldn’t compare', view.error)}</div>`
        : view.loading || !r ? loading(6)
        : r.error ? html`<div data-track="error">${empty('Nothing to compare', r.error)}</div>`
        : html`<div class="stack" style="gap:22px">
          <div class="row wrap" style="gap:10px;align-items:center"><h3 class="t-h3" style="margin:0">On one scale</h3><span class="grow"></span>
            <div class="row" style="gap:6px" data-pick="unit">${chips([['pct', '% change'], ['pts', `${S.base.label} points`]], view.unit, 'tunit')}</div></div>
          ${legend(S, r)}${chartLevels(S, r, view.unit, intraday)}
          <div class="col" style="gap:4px"><h3 class="t-h3" style="margin:0">How far each strays</h3><span class="small mute">Its % move minus the underlying’s, ${intraday ? 'candle by candle' : 'day by day'}, in points. Above 0 it’s ahead, below it’s behind.</span></div>
          ${chartGap(r, intraday)}
          <h3 class="t-h3" style="margin:0">The numbers</h3>
          ${figures(S, r, intraday)}
          <p class="tiny mute pretty" style="margin:0">From ${dShort(fromDay(r.start))} to ${dShort(fromDay(r.end))}, ${r.t.length} ${intraday ? '5-minute candles' : 'days'} every one of them has${r.dropped ? `; ${r.dropped} of the underlying’s left out, as not all had them` : ''}.
            Gap at the end: the difference in the two moves (the tracking difference). Tracking error: how much the gap moves from one ${intraday ? 'candle' : 'day'} to the next (the standard deviation of the difference in returns${intraday ? '' : '; yearly: × √252'}).
            These are traded prices, not NAV: Kite has no NAV for ETFs. Kite’s indices are price indices, without dividends, which ETFs keep, so one that tracks well drifts slowly above its index (about 1–1.5% a year for Nifty).</p>
        </div>`}</div></div>`);
  }

  let seq = 0;
  async function run() {
    const S = s();
    if (!S.with.length) { view.r = null; view.error = ''; paint(); return; }
    const rg = range(S);
    if (!rg.start || !rg.end) { view.r = null; view.error = 'Pick both dates.'; paint(); return; }
    const base = S.base.token ? String(S.base.token) : S.base.key;
    const ask = `${base}|${S.with.map(w => w.token).join(',')}|${rg.start}|${rg.end}|${rg.interval}`;
    if (ask === view.asked && view.r) { paint(); return; }
    const my = ++seq;
    view.loading = true; view.error = ''; paint();
    try {
      const r = await api.track({ base, with: S.with.map(w => w.token).join(','), ...rg });
      if (my !== seq) return;
      view.r = r; view.asked = ask;
    } catch (e) { if (my !== seq) return; view.error = e.message; view.r = null; }
    view.loading = false; paint();
  }

  const search = debounce(async () => {
    if (view.q.length < 2) { view.results = null; view.busy = false; paint(); return; }
    try { view.results = (await api.searchInstruments(view.q)).results; view.err = ''; } catch (e) { view.results = []; view.err = e.message; }
    view.busy = false; paint();
    const el = $('[data-tq]', root); if (el) { el.focus(); el.setSelectionRange(view.q.length, view.q.length); }
  }, 350);

  const offActs = actions(root, {
    tbase: d => { const sc = SHORTCUTS.find(x => x[0] === d.v); st.set({ base: { key: sc[0], label: sc[1] } }); view.mode = null; run(); },
    tsearch: d => { view.mode = view.mode === d.m ? null : d.m; view.q = ''; view.results = null; paint(); const el = $('[data-tq]', root); if (el) el.focus(); },
    tchoose: d => {
      const tok = +d.t, label = d.s;
      labels.set(tok, label);
      if (view.mode === 'base') { st.set({ base: { key: '', token: tok, label } }); view.mode = null; run(); return; }
      const w = s().with;
      if (w.some(x => x.token === tok)) { st.set({ with: w.filter(x => x.token !== tok) }); run(); return; }
      if (w.length >= MAX) { say(`At most ${MAX} at a time.`); return; }
      st.set({ with: [...w, { token: tok, label, ex: d.x }] });
      run();
    },
    tdrop: d => { st.set({ with: s().with.filter(x => x.token !== +d.t) }); run(); },
    tperiod: d => { st.set({ period: d.v }); run(); },
    tunit: d => { view.unit = d.v; paint(); },
  });
  const offQ = on(root, 'input', '[data-tq]', (e, el) => { view.q = el.value.trim(); view.busy = view.q.length >= 2; search(); });
  const offD = on(root, 'change', '[data-td]', (e, el) => { st.set({ [el.dataset.td]: el.value }); run(); });
  run();
  return () => { offActs(); offQ(); offD(); seq++; };
}
