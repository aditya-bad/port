// One flagged day of one deployment (app/repair/dayrepair.py): what the
// day check found, and its repair — planned by the repair worker from the
// minute the app and its strategy parted, with the proof to judge it by:
// the index minute by minute with every trade the app made and the
// strategy would have, each leg's price, each step and what triggered it.
// A minute that reads two ways waits for you to choose; the choices are a
// draft until you push it to the deployment (and it can be undone).

import { api } from '../core/api.js';
import { html, raw, mount, actions, $, on } from '../core/dom.js';
import { inr, price, dDay, fromDay, hhmm, plural, tone } from '../core/format.js';
import { href } from '../core/router.js';
import { sect, empty, loading } from '../ui/components.js';
import { line } from '../ui/charts.js';
import { openDialog } from '../ui/dialog.js';
import { say, fail } from '../ui/toast.js';

const PILL = {
  rule: ['Rule broken', 't-bad'], mismatch: ['Traded differently', 't-bad'], ambiguous: ['To review', 't-warn'],
  error: ['Check failed', 't-warn'], cant_check: ['Not replayed', 't-mute'], ok: ['As expected', 't-ok'],
};
const ORDER_WORDS = { up_first: 'Up first', down_first: 'Down first' };
const LEG_COLORS = ['var(--b)', 'var(--wr)', 'var(--g)', 'var(--r)', 'var(--bz)', 'var(--mute)', 'var(--ink)'];
const N = 375;   // minutes, 09:15–15:29
const minIdx = hhmm => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m - (9 * 60 + 15); };
const xPct = i => ((i + 0.5) / N) * 100;

const kv = o => {
  if (o == null) return '';
  if (typeof o !== 'object') return String(o);
  return Object.entries(o).filter(([, v]) => v != null && typeof v !== 'object')
    .map(([k, v]) => `${k.replace(/_/g, ' ')} ${typeof v === 'number' ? (Math.abs(v) >= 100 ? v.toFixed(1) : +v.toFixed(3)) : v}`).join(' · ');
};
const why = f => kv((f.meta || {}).trigger) || kv(f.trigger) || '';
const fillLine = f => html`<span class="mono">${f.minute || f.at}</span> ${f.action === 'buy' ? 'Bought' : 'Sold'} ${f.qty} <b class="mono">${f.symbol}</b> @ ${price(f.price)}`;

// ── the index, minute by minute, with both sides' trades ──────────────
function indexChart(s, plan, app) {
  const ix = s.index;
  if (!ix || !ix.ohlc.some(Boolean)) return empty('', 'No index candles for this day.');
  const H = 260, w = 1000 / N;
  const vals = ix.ohlc.filter(Boolean).flatMap(c => [c[1], c[2]]);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.06 || 1;
  const L = lo - pad, Hh = hi + pad;
  const y = v => H - (v - L) / (Hh - L) * H;
  const from = plan ? minIdx(plan.from) : null;
  const amb = plan && plan.ambiguity ? minIdx(plan.ambiguity.minute) : null;
  let body = '';
  ix.ohlc.forEach((c, i) => {
    if (!c) return;
    const [o, h, l, cl] = c, x = i * w, up = cl >= o;
    const col = up ? 'var(--g)' : 'var(--r)';
    body += `<line x1="${(x + w / 2).toFixed(2)}" x2="${(x + w / 2).toFixed(2)}" y1="${y(h).toFixed(1)}" y2="${y(l).toFixed(1)}" stroke="${col}" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
    const top = y(Math.max(o, cl)), bot = y(Math.min(o, cl));
    body += `<rect x="${(x + w * 0.15).toFixed(2)}" y="${top.toFixed(1)}" width="${(w * 0.7).toFixed(2)}" height="${Math.max(0.8, bot - top).toFixed(1)}" fill="${col}"/>`;
  });
  const close = i => { for (let k = i; k >= 0; k--) if (ix.ohlc[k]) return ix.ohlc[k][3]; return null; };
  // sells above the candle, buys below; more than one in a minute stack outwards
  const stack = {};
  const mark = (f, who) => {
    const i = minIdx(f.minute), c = close(i);
    if (i < 0 || i >= N || c == null) return '';
    const k = `${f.minute}:${f.action}`, n = (stack[k] = (stack[k] ?? -1) + 1);
    const off = (f.action === 'sell' ? -1 : 1) * (12 + n * 13);
    const topPct = (y(c) / H) * 100;
    return html`<i class="rc-mk ${who} ${f.action}" style="left:${xPct(i)}%;top:calc(${topPct}% + ${off}px)" data-step="${who}:${f.minute}:${f.symbol}"
      data-tip="${`${who === 'rep' ? 'The strategy, replayed' : 'The app'} · ${f.minute}\n${f.action === 'buy' ? 'Bought' : 'Sold'} ${f.qty} ${f.symbol} @ ${price(f.price)}\n${f.reason || ''}${why(f) ? `\n${why(f)}` : ''}`}"></i>`;
  };
  return html`<div class="rc-chart" data-chart="index">
    <svg viewBox="0 0 1000 ${H}" preserveAspectRatio="none" style="width:100%;height:${H}px" role="img" aria-label="${ix.label}, 1-minute candles">
      ${from != null ? html`<rect x="${(from * w).toFixed(1)}" y="0" width="${(1000 - from * w).toFixed(1)}" height="${H}" fill="var(--ys)"/>` : ''}
      ${amb != null ? html`<rect x="${(amb * w).toFixed(1)}" y="0" width="${Math.max(w, 3).toFixed(1)}" height="${H}" fill="var(--y)" opacity=".7"/>` : ''}
      <g>${raw(body)}</g>
    </svg>
    <div class="rc-marks">${app.map(f => mark(f, 'app'))}${(plan ? plan.fills : []).map(f => mark(f, 'rep'))}</div>
    ${from != null ? html`<span class="rc-from" style="left:${xPct(from)}%">Repaired from ${plan.from}</span>` : ''}
    <div class="rc-axis mono tiny mute"><span>09:15</span><span>11:00</span><span>12:30</span><span>14:00</span><span>15:29</span></div>
    <div class="rc-read small mono" data-read>${ix.label} · hover for a minute</div>
  </div>
  <div class="rc-legend small"><span><i class="rc-mk app sell"></i>The app’s trade</span><span><i class="rc-mk rep sell"></i>The strategy’s: sold</span><span><i class="rc-mk rep buy"></i>bought</span>
    <span class="mute">Sells sit above the candle, buys below</span>
    ${from != null ? html`<span><b class="rc-sw shade"></b>The repaired stretch</span>` : ''}${amb != null ? html`<span><b class="rc-sw amb"></b>Reads two ways</span>` : ''}</div>`;
}

// ── each leg's price ───────────────────────────────────────────────────
function legsChart(s, plan) {
  const names = Object.keys(s.legs || {}).filter(k => s.legs[k].some(v => v != null));
  if (!names.length) return '';
  const fillGaps = vs => { let last = vs.find(v => v != null); return vs.map(v => (v == null ? last : (last = v))); };
  const series = names.map((n, i) => ({ label: n, values: fillGaps(s.legs[n]), color: LEG_COLORS[i % LEG_COLORS.length], width: 1.6 }));
  const from = plan ? minIdx(plan.from) : null;
  return html`<div class="rc-chart" data-chart="legs">${line({ series, h: 170, rule: true })}
    ${from != null ? html`<span class="rc-from" style="left:${xPct(from)}%"></span>` : ''}</div>
    <div class="rc-legend small">${names.map((n, i) => html`<span><b class="rc-sw" style="background:${LEG_COLORS[i % LEG_COLORS.length]}"></b><span class="mono">${n}</span></span>`)}</div>`;
}

// ── each step from where it parted, and why ───────────────────────────
function steps(plan) {
  // by minute; within one, as the strategy did them (a buy-back before the new sell)
  const rows = [
    ...plan.fills.map((f, n) => ({ t: f.minute, kind: 'trade', f, n: 1000 + n })),
    ...plan.decisions.map((d, n) => ({ t: d.at.slice(0, 5), kind: 'check', d, n })),
  ].sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : a.n - b.n));
  if (!rows.length) return empty('', 'From there to the close its strategy made no trade and noted no check.');
  return html`<div class="rc-steps">${rows.map(r => r.kind === 'trade'
    ? html`<div class="trade" data-step-row="rep:${r.f.minute}:${r.f.symbol}"><span class="mono">${r.t}</span>
        <span>${r.f.action === 'buy' ? 'Bought' : 'Sold'} ${r.f.qty} <b class="mono">${r.f.symbol}</b> @ ${price(r.f.price)}${r.f.phase && r.f.phase !== 'open' && r.f.phase !== 'close' ? html` <span class="tiny mute">at the minute’s ${r.f.phase === 'up' ? 'high' : 'low'} side</span>` : ''}</span>
        <span><b>${r.f.reason || 'trade'}</b>${why(r.f) ? html`<span class="tiny mute"> — ${why(r.f)}</span>` : ''}</span></div>`
    : html`<div class="check"><span class="mono">${r.t}</span><span class="small">${r.d.summary}</span>
        <span class="tiny mute">${r.d.rule} · ${r.d.outcome === 'blocked' ? 'held back' : 'no action'}${kv(r.d.data) ? ` — ${kv(r.d.data)}` : ''}</span></div>`)}
  </div>`;
}

// ── a minute that reads two ways ───────────────────────────────────────
function choice(plan) {
  const a = plan.ambiguity;
  if (!a) return '';
  const card = k => {
    const p = a.paths[k];
    return html`<div class="rc-path ${a.natural === k ? 'nat' : ''}">
      <div class="row" style="gap:8px;align-items:baseline"><b>${ORDER_WORDS[k]}</b>${a.natural === k ? html`<span class="tiny mute">the usual reading of this candle</span>` : ''}</div>
      <span class="small mute">${k === 'up_first' ? 'The index went to its high, then its low' : 'The index went to its low, then its high'} — every CE at its ${k === 'up_first' ? 'high' : 'low'} first, every PE at its ${k === 'up_first' ? 'low' : 'high'}.</span>
      ${p.fills.length ? html`<div class="col" style="gap:4px">${p.fills.map(f => html`<div class="small">${fillLine(f)} <span class="tiny mute">${f.reason}${why(f) ? ` — ${why(f)}` : ''}</span></div>`)}</div>`
        : html`<span class="small">No trade this minute.</span>`}
      <span class="small">P&amp;L at the minute’s close: <b class="${tone(p.pnl)}">${inr(p.pnl)}</b> · then holding ${p.open.length ? p.open.map(o => o.symbol).join(', ') : 'nothing'}</span>
      <button type="button" class="btn btn-sm btn-primary" data-act="choose" data-m="${a.minute}" data-k="${k}">Take this path</button>
    </div>`;
  };
  return html`<section class="rc-choice" aria-label="Choose a path">
    <h3 class="t-h3">${a.minute} reads two ways</h3>
    <p class="small">A 1-minute candle says how far each way the price went, not which way first — and here that changes what the strategy does. Pick what happened; the plan carries on from your path to the close (or the next such minute).</p>
    <div class="rc-candles mono small">${a.candles.map(c => html`<div><b>${c.label}</b><span>O ${price(c.open)}</span><span>H ${price(c.high)}</span><span>L ${price(c.low)}</span><span>C ${price(c.close)}</span></div>`)}</div>
    <div class="rc-paths">${['up_first', 'down_first'].map(card)}</div>
  </section>`;
}

function endStates(plan) {
  const legs = xs => (xs && xs.length ? xs.map(l => html`<div class="mono small">${l.side === 'short' ? 'Short' : 'Long'} ${l.qty} ${l.symbol}</div>`) : html`<div class="small mute">Flat</div>`);
  const s = plan.summary || {};
  return html`<div class="dc-end ${plan.complete ? 'diff' : ''}">
    <div><span class="l">Now (as the app left it)</span>${legs(plan.end.app)}</div>
    <div><span class="l">After the repair</span>${legs(plan.end.after)}</div>
  </div>
  <div class="rc-nums">
    <div><span class="l">P&amp;L from ${plan.from}, repaired</span><b class="${tone(s.pnl)}">${inr(s.pnl)}</b></div>
    <div><span class="l">As the app left it</span><b class="${tone(s.left)}">${inr(s.left)}</b></div>
    <div><span class="l">Change</span><b class="${tone(s.change)}">${inr(s.change)}</b></div>
    <div><span class="l">Booked · charges</span><b>${inr(s.booked)} · ${inr(-(s.charges || 0))}</b></div>
  </div>`;
}

export default {
  title: p => `Repair · ${dDay(fromDay(p.day))}`,
  render(main, params) {
    const { day, dep } = params;
    let c = null, err = null, timer = null, requested = false, busyPush = false;

    async function load() {
      try { c = await api.repairCase(day, dep); err = null; } catch (e) { err = e; }
      clearTimeout(timer);
      const waiting = c && ((c.plan && ['queued', 'running'].includes(c.plan.status)) || (c.apply && ['queued', 'running'].includes(c.apply.status)));
      if (waiting) timer = setTimeout(load, 1500);
      // nothing planned for the paths chosen so far: plan them (a dry run)
      if (c && !c.applied && !waiting && !c.plan_is_draft && !requested && ['rule', 'mismatch', 'ambiguous'].includes(c.check.status)) {
        requested = true;
        plan(c.draft.choices);
        return;
      }
      paint();
    }
    async function plan(choices) {
      try { await api.planRepair(day, dep, choices); } catch (e) { fail(e, 'Couldn’t plan it'); }
      load();
    }

    function head() {
      return html`<div class="page-head">
        <div class="col" style="gap:4px">
          <a class="small" href="${href('repair')}">‹ Repair</a>
          <h1 class="t-h1">${c ? c.deployment.name : 'Repair'}</h1>
          <span class="mute">${dDay(fromDay(day))}${c ? ` · ${c.deployment.strategy} · ${c.deployment.mode}` : ''}</span>
        </div></div>`;
    }

    function stateBar() {
      const pl = c.plan, ap = c.apply;
      if (c.applied) {
        return html`<div class="rc-state t-ok"><b>Repaired</b><span>Applied ${hhmm(c.applied.applied_at)} from ${hhmm(c.applied.from)}: ${plural(c.applied.summary.fills || 0, 'trade')} in place of the app’s ${c.applied.replaced.length}.</span>
          <span class="grow"></span><button type="button" class="btn btn-sm" data-act="undo">Undo the repair</button></div>`;
      }
      if (ap && ['queued', 'running'].includes(ap.status)) return html`<div class="rc-state"><i class="spin"></i><b>Pushing to the deployment…</b><span>It’s paused until this is done.</span></div>`;
      if (ap && ap.status === 'failed') return html`<div class="rc-state t-bad"><b>The push didn’t go through</b><span>${ap.error}</span></div>`;
      if (!pl || ['queued', 'running'].includes(pl.status)) return html`<div class="rc-state"><i class="spin"></i><b>Planning…</b><span>Replaying the strategy from where it parted, on Kite’s 1-minute candles. The first plan of a day fetches them, so it takes a moment.</span></div>`;
      if (pl.status === 'failed') return html`<div class="rc-state t-bad"><b>Couldn’t plan it</b><span>${pl.error}</span><span class="grow"></span><button type="button" class="btn btn-sm" data-act="replan">Try again</button></div>`;
      const r = pl.result;
      if (r.ambiguity) return html`<div class="rc-state t-warn"><b>Your choice: ${r.ambiguity.minute}</b><span>That minute reads two ways. Choose below; nothing changes until you push.</span></div>`;
      if (!r.can_apply) return html`<div class="rc-state t-bad"><b>Can’t be pushed</b><span>${(r.why_not || []).join(' ') || 'The replay couldn’t be trusted.'}</span></div>`;
      return html`<div class="rc-state t-ready"><b>Ready</b><span>The plan below is a draft: the deployment is unchanged until you push it.</span>
        <span class="grow"></span><button type="button" class="btn btn-primary" data-act="push" ${busyPush ? 'disabled' : ''}>Push to the deployment</button></div>`;
    }

    function choicesBar() {
      const ch = Object.entries(c.draft.choices || {}).sort();
      if (!ch.length) return '';
      return html`<div class="rc-choices small"><span class="mute">Paths you chose:</span>
        ${ch.map(([m, k]) => html`<span class="chip">${m} ${ORDER_WORDS[k].toLowerCase()} <button type="button" class="x" data-act="unchoose" data-m="${m}" aria-label="Choose ${m} again">✕</button></span>`)}
        <button type="button" class="btn btn-xs btn-quiet" data-act="reset">Start over</button></div>`;
    }

    function paint() {
      if (err) { mount(main, html`${head()}<div class="page">${empty('Couldn’t load this repair', err.message)}</div>`); return; }
      if (!c) { mount(main, html`${head()}<div class="page">${loading(6)}</div>`); return; }
      const pl = c.plan && c.plan.status === 'done' ? c.plan.result : null;
      const shown = c.applied ? null : pl;
      const p = PILL[c.check.status];
      mount(main, html`${head()}<div class="page" style="padding-top:4px">
        <div class="rc-found"><span class="dc-pill ${p[1]}">${p[0]}</span><span>${c.check.headline}</span></div>
        ${(c.check.rules || []).map(r => html`<div class="dc-rule ${r.severity}"><b>${r.title}</b><span>${r.detail}</span></div>`)}
        ${stateBar()}
        ${choicesBar()}
        ${shown ? html`
          ${choice(shown)}
          ${sect('The day, minute by minute', `${shown.series.index ? shown.series.index.label : 'The index'} on Kite’s 1-minute candles; the shaded stretch is what the repair replays`)}
          ${indexChart(shown.series, shown, shown.app_fills)}
          ${sect('Each leg', 'Its price each minute (the last trade carried over a minute with none)')}
          ${legsChart(shown.series, shown)}
          ${sect('End of the day', shown.complete ? 'Both ways' : `So far — up to ${shown.ambiguity.minute}`)}
          ${endStates(shown)}
          ${sect(`From ${shown.from}: each step, and why`, `Its own strategy’s code, from its state ${shown.start}`)}
          ${steps(shown)}
          ${shown.replaced.length ? html`${sect('The app’s trades this replaces', `Everything it did from ${shown.from} on`)}
            <div class="rc-replaced">${shown.replaced.map(f => html`<div class="small">${fillLine(f)} <span class="tiny mute">${f.reason}</span></div>`)}</div>` : ''}
          ${(shown.notes || []).length || (shown.estimated || []).length ? html`${sect('Notes')}<div class="dc-notes small mute">${(shown.notes || []).map(n => html`<span>${n}</span>`)}</div>` : ''}`
        : c.applied ? html`${sect('What it did')}<div class="rc-steps">${c.applied.fills.map(f => html`<div class="trade"><span class="mono">${f.minute}</span><span>${fillLine(f)}</span><span><b>${f.reason}</b></span></div>`)}</div>
            ${c.applied.replaced.length ? html`${sect('In place of the app’s')}<div class="rc-replaced">${c.applied.replaced.map(f => html`<div class="small">${fillLine(f)}</div>`)}</div>` : ''}`
        : ''}
      </div>`);
      wireChart();
    }

    // the readout under the index chart
    function wireChart() {
      const ch = $('[data-chart="index"]', main);
      if (!ch || !c || !c.plan || !c.plan.result) return;
      const s = c.plan.result.series, read = $('[data-read]', ch);
      ch.onmousemove = e => {
        const r = ch.getBoundingClientRect(), i = Math.max(0, Math.min(N - 1, Math.floor((e.clientX - r.left) / r.width * N)));
        const o = s.index.ohlc[i];
        const legs = Object.entries(s.legs).map(([k, v]) => (v[i] != null ? `${k} ${price(v[i])}` : '')).filter(Boolean).join(' · ');
        read.textContent = `${s.t[i]} · ${s.index.label} ${o ? `O ${price(o[0])} H ${price(o[1])} L ${price(o[2])} C ${price(o[3])}` : '—'}${s.vix[i] != null ? ` · VIX ${s.vix[i].toFixed(2)}` : ''}${legs ? ` · ${legs}` : ''}`;
      };
    }

    const offActs = actions(main, {
      choose: x => { say(`${x.m}: ${ORDER_WORDS[x.k].toLowerCase()}. Planning on from there…`); plan({ ...c.draft.choices, [x.m]: x.k }); },
      unchoose: x => { const ch = { ...c.draft.choices }; Object.keys(ch).forEach(m => { if (m >= x.m) delete ch[m]; }); plan(ch); },
      reset: () => plan({}),
      replan: () => plan(c.draft.choices),
      push: () => {
        const r = c.plan.result;
        openDialog({
          kicker: 'CONFIRM', title: `Push this repair to ${c.deployment.name}?`,
          lines: [
            `From ${r.from} to the close, the app’s ${plural(r.replaced.length, 'trade')} are replaced by the strategy’s ${r.fills.length}.`,
            `It ends the day ${r.end.after.length ? `holding ${r.end.after.map(l => l.symbol).join(', ')}` : 'flat'}; P&L from ${r.from} ${inr(r.summary.pnl)} (was ${inr(r.summary.left)}).`,
            c.deployment.status === 'active' ? 'The deployment is paused while it’s applied, then resumed.' : 'The deployment isn’t running, so nothing is paused.',
            'It’s replayed once more and applied only if it comes out exactly like this. You can undo it.',
          ],
          okLabel: 'Push to the deployment',
          onOk: async () => {
            busyPush = true;
            try { await api.pushRepair(day, dep); say('Pushing the repair…'); } catch (e) { fail(e, 'Couldn’t push it'); }
            busyPush = false;
            load();
            return true;
          },
        });
      },
      undo: () => openDialog({
        kicker: 'CONFIRM', title: 'Undo this repair?',
        lines: ['The strategy’s trades come out and the app’s own go back, as they were. The deployment is paused meanwhile.'],
        okLabel: 'Undo the repair',
        onOk: async () => {
          try { await api.undoRepair(c.applied.id); say('Repair undone. The app’s own trades are back.'); requested = true; } catch (e) { fail(e, 'Couldn’t undo it'); }
          load();
          return true;
        },
      }),
    });
    const offMk = on(main, 'click', '[data-step]', (e, el) => {
      const row = $(`[data-step-row="${el.dataset.step}"]`, main);
      if (row) { row.scrollIntoView({ block: 'center', behavior: 'smooth' }); row.classList.add('hl'); setTimeout(() => row.classList.remove('hl'), 1600); }
    });
    paint();
    load();
    return () => { offActs(); offMk(); clearTimeout(timer); };
  },
};
