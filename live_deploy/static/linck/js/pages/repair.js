// Repair: is everything that has to work, working — and what needs fixing.
// The health monitor (app/ops_health.py) checks Kite, the database, the
// 15:45 option-history export and every deployment each minute; every
// problem is an issue that alerts every minute until it clears. Here they
// are: open first, with snooze and dismiss, then the recently cleared.
// After the 15:45 export, the day check (app/repair/daycheck.py) replays
// each deployment's day and checks its end: its findings are here too.

import { api } from '../core/api.js';
import { html, mount, actions, on } from '../core/dom.js';
import { hhmm, hhmmss, dStamp, dDay, dayKey, fromDay, plural, price } from '../core/format.js';
import { href } from '../core/router.js';
import { listen } from '../core/store.js';
import { sect, empty, loading } from '../ui/components.js';
import { say, fail } from '../ui/toast.js';
import { pollStatus } from '../data/live.js';

const WATCHED = [
  ['Kite', 'Logged in from 09:05 on a market day; ticks arriving (a silent feed for 90s is a stall).'],
  ['Database', 'Answers every minute.'],
  ['Each running deployment', 'Its strategy is running, and acting on prices while they flow (not silent for 3 min).'],
  ['Strategy errors', 'Any in the last 5 minutes.'],
  ['What deployments report', 'A strategy that keeps failing or can’t start, a fill that failed, legs it lost track of, an entry it couldn’t make, prices it couldn’t trust — critical ones alert every minute, warnings once.'],
  ['Open legs', 'Have traded today (after 09:45): a leg priced from an old trade can’t be managed.'],
  ['The 15:45 export', 'Today’s option candles saved from Kite — it says when it’s done, and alerts if it fails.'],
];
const SNOOZES = [['30m', '30 min'], ['2h', '2 hours'], ['today', 'Until tomorrow']];
const snoozed = x => x.snoozed_until && new Date(x.snoozed_until) > new Date();

function kiteStat(k) {
  if (!k) return ['—', ''];
  if (!k.connected) return [html`<span class="neg">Not connected</span>`, 'Log in from the header'];
  if (k.ticks_stalled_s) return [html`<span class="neg">Prices stopped</span>`, `No ticks for ${Math.round(k.ticks_stalled_s / 60)} min · reconnecting`];
  return ['Connected', k.last_tick_at ? `Last tick ${hhmmss(k.last_tick_at)}` : 'No ticks yet today'];
}

function exportStat(e) {
  const today = dayKey(new Date());
  if (e && e.running) return ['Running', `${e.running.done} of ${e.running.total || '…'} contracts`];
  const last = e && e.last;
  if (last && last.day === today) {
    if (last.error) return [html`<span class="neg">Failed</span>`, String(last.error)];
    return ['Saved', `${hhmm(last.finished_at)} · ${plural(last.instruments || 0, 'contract')} · ${last.rows || 0} min`];
  }
  return ['Waiting', last ? `Runs at 15:45 · last ${last.day}` : 'Runs at 15:45 on market days'];
}

function statusRow(d) {
  const [kv, ks] = kiteStat(d.kite);
  const [ev, es] = exportStat(d.export);
  const crit = d.open.filter(x => x.severity === 'critical').length, warn = d.open.length - crit;
  const cell = (l, v, s, cls = '') => html`<div class="ops-stat ${cls}"><span class="l">${l}</span><b class="v">${v}</b><span class="s">${s}</span></div>`;
  return html`<div class="ops-stats">
    ${cell('Kite', kv, ks)}
    ${cell('Deployments running', String(d.running), 'Strategies with a live runner')}
    ${cell('Today’s export', ev, es)}
    ${cell('Open issues', d.open.length ? String(d.open.length) : 'None', d.open.length ? [crit && `${crit} critical`, warn && plural(warn, 'warning')].filter(Boolean).join(' · ') : 'Everything watched is fine',
           crit ? 't-bad' : d.open.length ? 't-warn' : 't-ok')}
  </div>`;
}

function issueCard(x) {
  const zz = snoozed(x);
  return html`<article class="ops-issue ${x.severity} ${zz ? 'zz' : ''}" data-issue="${x.id}">
    <div class="ops-head">
      <span class="ops-sev">${x.severity === 'critical' ? 'Critical' : 'Warning'}</span>
      <h4>${x.title}</h4>
      ${x.data && x.data.day && x.deployment_id ? html`<a class="small" href="${href(`repair/${x.data.day}/${x.deployment_id}`)}">Review ${dDay(fromDay(x.data.day))} ›</a>` : ''}
      ${x.deployment_id ? html`<a class="small" href="${href(`d/${x.deployment_id}`)}">Open deployment ›</a>` : ''}
    </div>
    <p>${x.detail}</p>
    <div class="ops-meta mono tiny mute">
      <span>Open since ${dStamp(x.opened_at)}</span>
      <span>Last seen ${hhmmss(x.last_seen_at)}</span>
      <span>${plural(x.alerts, 'alert')}${x.last_alert_at ? `, last at ${hhmmss(x.last_alert_at)}` : ''}</span>
      ${zz ? html`<span class="ops-zz">Snoozed until ${dStamp(x.snoozed_until)}</span>` : ''}
    </div>
    <div class="ops-acts">
      ${zz
        ? html`<button type="button" class="btn btn-sm" data-act="snooze" data-id="${x.id}" data-p="clear">Alert again now</button>`
        : html`<span class="small mute">Snooze alerts:</span>${SNOOZES.map(([k, l]) => html`<button type="button" class="btn btn-sm" data-act="snooze" data-id="${x.id}" data-p="${k}">${l}</button>`)}`}
      <span class="grow"></span>
      <button type="button" class="btn btn-sm btn-quiet" data-act="dismiss" data-id="${x.id}" data-tip="${'Close it by hand\nIf the check still finds the problem,\nit opens again as a new issue.'}">Dismiss</button>
    </div>
  </article>`;
}

// ── What the deployments report about themselves ────────────────────
const KIND_WORDS = {
  crash: 'Keeps failing', fill_failed: 'Fill failed', legs_mismatch: 'Legs out of step',
  resynced: 'Re-synced', bad_ticks: 'Bad prices', exit_at_entry: 'Closed at entry', square_off_failed: 'Square-off failed',
  cash_negative: 'Cash below zero', state_unsaved: 'State not saved', bad_order: 'Order refused', blocked: 'Blocked',
  missed_trade: 'Missed trade', entry_delayed: 'Entry late', entry_skipped: 'Entry skipped',
  state_lost: 'State lost', adjustment_delayed: 'Roll late', unhedged: 'Unhedged', feed_gap: 'No prices',
};
const CLOSED_HOW = { cleared: 'cleared itself', expired: 'went away', dismissed: 'dismissed' };

function reportedSection(rows) {
  if (rows === null) return loading(3);
  if (!rows.length) return html`<p class="small mute" style="margin:0">Nothing reported recently.</p>`;
  return html`<div class="rp-reported" role="table">${rows.map(x => html`<div class="tr ${x.resolved_at ? 'done' : ''}" role="row" data-sissue="${x.id}">
    <span class="si-sev t-${x.severity === 'critical' ? 'bad' : x.severity === 'warning' ? 'warn' : 'mute'}">${KIND_WORDS[x.kind] || x.kind.replace(/_/g, ' ')}</span>
    <span class="col" style="gap:2px;min-width:0"><b class="small">${x.name}: ${x.title}</b><span class="tiny mute">${x.detail || ''}</span></span>
    <span class="col tiny mute" style="gap:2px;text-align:right">
      <span>${x.source === 'strategy' ? 'by the strategy' : 'by the runner'} · ${dStamp(x.first_at)}${x.count > 1 ? ` · ${x.count}×` : ''}</span>
      <span>${x.resolved_at ? `${CLOSED_HOW[x.resolved_by] || x.resolved_by} ${dStamp(x.resolved_at)}` : x.severity === 'info' ? 'noted' : html`<b class="t-${x.severity === 'critical' ? 'bad' : 'warn'}">open</b>`}</span>
      ${x.day ? html`<a href="${href(`repair/${x.day}/${x.deployment_id}`)}">Review ${dDay(fromDay(x.day))} ›</a>` : ''}
      ${x.resolved_at ? '' : html`<button type="button" class="linkish" data-act="dismissReport" data-id="${x.id}">Dismiss</button>`}
    </span></div>`)}</div>`;
}

// ── The day check ──────────────────────────────────────────────────
const CHECK = {
  rule: ['Rule broken', 'bad', 'The end of the day isn’t what its strategy expects'],
  mismatch: ['Traded differently', 'bad', 'The replay of its strategy did something the app didn’t, or the other way round'],
  ambiguous: ['To review', 'warn', 'It traded differently, but a minute could be read two ways: you choose'],
  error: ['Check failed', 'warn', 'The replay itself failed'],
  cant_check: ['Not replayed', 'mute', 'No start-of-day state, or no prices for a leg: only the rules were checked'],
  ok: ['As expected', 'ok', 'Traded as its strategy would have, and ended the day as expected'],
};
const pill = st => html`<span class="dc-pill t-${CHECK[st][1]}" data-tip="${CHECK[st][2]}">${CHECK[st][0]}</span>`;
const fillTxt = f => (f ? html`<span class="mono">${f.minute}</span> ${f.action === 'buy' ? 'Bought' : 'Sold'} ${f.qty} <b class="mono">${f.symbol}</b> @ ${price(f.price)}${f.reason ? html` <span class="tiny mute">${f.reason}</span>` : ''}` : html`<span class="mute">—</span>`);

function runLine(run) {
  if (!run) return html`<span class="small mute">Not run yet for this day.</span>`;
  const how = run.trigger === 'auto' ? 'after the export' : 'by hand';
  if (run.status === 'running') {
    const p = run.progress || {};
    return html`<span class="small"><i class="spin" aria-hidden="true"></i> Running ${how} — ${p.done ?? 0} of ${p.total ?? '…'}${p.current ? html` · <b>${p.current}</b>` : ''}</span>`;
  }
  if (run.status === 'failed') return html`<span class="small neg">Failed ${hhmm(run.started_at)}: ${run.error || 'no reason given'}</span>`;
  return html`<span class="small mute">Ran ${how}, ${hhmm(run.started_at)}–${hhmm(run.finished_at)}</span>`;
}

function tradesTable(diff) {
  const rows = [
    ...(diff.missed || []).map(f => ({ rep: f, app: null, k: 'missed', t: f.minute })),
    ...(diff.extra || []).map(f => ({ rep: null, app: f, k: 'extra', t: f.minute })),
    ...(diff.matched || []).map(m => ({ rep: m.replay, app: m.app, k: 'same', t: m.replay.minute, m })),
  ].sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
  if (!rows.length) return html`<p class="small mute">No trades that day, by the app or the replay.</p>`;
  return html`<div class="dc-trades">
    <div class="th"><span>Its strategy, replayed</span><span>The app</span><span></span></div>
    ${rows.map(r => html`<div class="${r.k}"><span data-l="Strategy">${fillTxt(r.rep)}</span><span data-l="App">${fillTxt(r.app)}</span>
      <span class="tiny ${r.k === 'same' ? 'mute' : 'neg'}">${r.k === 'missed' ? 'The app didn’t' : r.k === 'extra' ? 'The strategy wouldn’t'
        : `${r.m.minutes_apart ? `${r.m.minutes_apart} min apart · ` : ''}${r.m.price_diff ? `₹${r.m.price_diff > 0 ? '+' : ''}${r.m.price_diff} vs the candle` : 'same'}`}</span></div>`)}
  </div>`;
}

function endState(end) {
  const legs = xs => (xs.length ? xs.map(l => html`<div class="mono small">${l.side === 'short' ? 'Short' : 'Long'} ${l.qty} ${l.symbol}</div>`) : html`<div class="small mute">Flat</div>`);
  return html`<div class="dc-end ${end.same ? 'same' : 'diff'}">
    <div><span class="l">The strategy would have ended with</span>${legs(end.replay || [])}</div>
    <div><span class="l">The app ended with</span>${legs(end.app || [])}</div>
  </div>`;
}

function checkRow(x, open, day) {
  const diff = x.diff || {}, rep = x.replay || {};
  return html`<details class="dc-row t-${CHECK[x.status][1]}" ${open ? 'open' : ''} data-dc="${x.deployment_id}">
    <summary>${pill(x.status)}<b>${x.name}</b><span class="small mute">${x.strategy} · ${x.mode}</span><span class="dc-head">${x.headline}</span></summary>
    <div class="dc-body">
      ${(x.rules || []).map(r => html`<div class="dc-rule ${r.severity}"><b>${r.title}</b><span>${r.detail}</span></div>`)}
      ${diff.end ? html`${endState(diff.end)}${tradesTable(diff)}` : ''}
      <div class="dc-notes small mute">
        ${rep.start ? html`<span>Replayed from the strategy’s state ${rep.start}.</span>` : ''}
        ${(rep.auto || []).length ? html`<span>Read the usual way at ${rep.auto.map(a => a.minute).join(', ')} — each could be read two ways.</span>` : ''}
        ${(rep.estimated || []).length ? html`<span>Priced without trades on Kite: ${rep.estimated.join(', ')}.</span>` : ''}
        ${(rep.notes || []).map(n => html`<span>${n}</span>`)}
      </div>
      <div class="row" style="gap:14px">
        ${['rule', 'mismatch', 'ambiguous'].includes(x.status) ? html`<a class="btn btn-sm btn-primary" href="${href(`repair/${day}/${x.deployment_id}`)}">Review and repair ›</a>` : ''}
        <a class="small" href="${href(`d/${x.deployment_id}`)}">Open deployment ›</a></div>
    </div>
  </details>`;
}

function dayCheckSection(c, day) {
  if (!c) return loading(3);
  const results = c.results || [];
  const flagged = results.filter(x => x.status !== 'ok');
  const fine = results.filter(x => x.status === 'ok');
  const counts = Object.entries(c.counts || {}).sort((a, b) => Object.keys(CHECK).indexOf(a[0]) - Object.keys(CHECK).indexOf(b[0]));
  const days = c.days || [];
  return html`<div class="dc">
    <div class="dc-bar">
      ${days.length ? html`<select class="input" data-f="dcDay" aria-label="Day">${days.map(k => html`<option value="${k}" ${k === (day || c.day) ? 'selected' : ''}>${dDay(fromDay(k))}</option>`)}</select>` : ''}
      ${runLine(c.run)}
      <span class="grow"></span>
      <button type="button" class="btn btn-sm" data-act="runCheck" ${c.run && c.run.status === 'running' ? 'disabled' : ''}>Run the day check${c.day ? ` for ${dDay(fromDay(day || c.day))}` : ' now'}</button>
    </div>
    ${counts.length ? html`<div class="dc-counts">${counts.map(([k, n]) => html`<span>${pill(k)} ${n}</span>`)}</div>` : ''}
    ${!results.length ? empty('', c.day ? 'No results for this day yet.' : 'The day check runs by itself once the 15:45 export succeeds. Each deployment’s day is replayed from its open on Kite’s 1-minute candles and checked against what the app did.')
      : html`<div class="dc-list">${flagged.map(x => checkRow(x, flagged.length <= 3, c.day))}
        ${fine.length ? html`<details class="dc-fine"><summary>${plural(fine.length, 'deployment')} as expected</summary>${fine.map(x => checkRow(x, false, c.day))}</details>` : ''}</div>`}
  </div>`;
}

function clearedTable(rows) {
  if (!rows.length) return empty('', 'Nothing has cleared yet.');
  return html`<div class="ops-cleared">
    <div class="th"><span>Issue</span><span>Opened</span><span>Closed</span><span>How</span><span class="right">Alerts</span></div>
    ${rows.map(x => html`<div><span><b>${x.title}</b></span><span class="mono small">${dStamp(x.opened_at)}</span>
      <span class="mono small">${dStamp(x.resolved_at)}</span>
      <span class="small ${x.resolved_by === 'dismissed' ? 'mute' : 'pos'}">${x.resolved_by === 'dismissed' ? 'Dismissed' : 'Cleared'}</span>
      <span class="mono small right">${x.alerts}</span></div>`)}
  </div>`;
}

export default {
  title: () => 'Repair',
  render(main) {
    let d = null, err = null, busy = false, dc = null, dcDay = '', dcTimer = null, reported = null;
    async function loadReported() {
      try { reported = (await api.strategyIssues(true)).issues; } catch (e) { reported = []; }
      paint();
    }
    async function load(fresh = false) {
      try {
        d = fresh ? await api.opsCheck() : await api.ops();
        err = null;
      } catch (e) { err = e; }
      paint();
    }
    async function loadChecks() {
      try { dc = await api.dayChecks(dcDay); } catch (e) { dc = { results: [], days: [], counts: {}, run: null, error: e.message }; }
      clearTimeout(dcTimer);
      if (dc.run && dc.run.status === 'running') dcTimer = setTimeout(loadChecks, 4000);   // follow a running check
      paint();
    }
    function paint() {
      mount(main, html`<div class="page-head">
          <div class="col"><h1 class="t-h1">Repair</h1><span class="mute">Is everything that has to work, working — and what needs fixing.</span></div>
          <span class="grow"></span>
          <div class="row" style="gap:10px;align-items:center">
            ${d && d.checked_at ? html`<span class="small mute">Checked ${hhmmss(d.checked_at)} · every minute</span>` : ''}
            <button type="button" class="btn btn-sm" data-act="check" ${busy ? 'disabled' : ''}>${busy ? 'Checking…' : 'Check now'}</button>
          </div>
        </div>
        <div class="page" style="padding-top:4px">
        ${err ? empty('Couldn’t load the health check', err.message) : !d ? loading(6) : html`
          ${statusRow(d)}
          ${sect('Open issues', d.open.length ? 'Each alerts your phone every minute until it clears or you snooze it' : '')}
          ${d.open.length ? html`<div class="ops-list">${d.open.map(issueCard)}</div>`
            : html`<div class="ops-ok"><b>Nothing needs you.</b><span>Everything the monitor watches is fine.</span></div>`}
          ${sect('Reported by the deployments', 'What each running deployment reported about itself — open ones first, then the last few days')}
          ${reportedSection(reported)}
          ${sect('Day check', 'Each deployment’s day replayed from its open and checked against what the app did')}
          ${dayCheckSection(dc, dcDay)}
          ${sect('What’s watched', 'Checked every minute')}
          <dl class="ops-watched">${WATCHED.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
          ${sect('Recently closed', 'The last 50')}
          ${clearedTable(d.recent)}`}
        </div>`);
    }
    const offActs = actions(main, {
      check: async () => { busy = true; paint(); await load(true); busy = false; paint(); pollStatus(); },
      snooze: async x => {
        try {
          await api.snoozeIssue(x.id, x.p);
          say(x.p === 'clear' ? 'Alerts are back on for this issue.' : `Snoozed ${SNOOZES.find(s => s[0] === x.p)[1].toLowerCase()}. It stays open here.`);
          await load(); pollStatus();
        } catch (e) { fail(e, 'Couldn’t snooze it'); }
      },
      runCheck: async () => {
        try {
          await api.runDayCheck(dcDay || (dc && dc.day) ? { day: dcDay || dc.day } : {});
          say('The day check is running. It replays every deployment, so it takes a few minutes.');
          await loadChecks();
        } catch (e) { fail(e, 'Couldn’t start the day check'); }
      },
      dismissReport: async x => {
        try { await api.dismissStrategyIssue(x.id); say('Dismissed. If the deployment reports it again, it comes back.'); await load(); await loadReported(); pollStatus(); }
        catch (e) { fail(e, 'Couldn’t dismiss it'); }
      },
      dismiss: async x => {
        try { await api.dismissIssue(x.id); say('Dismissed. If the problem is still there, the next check opens it again.'); await load(); pollStatus(); } catch (e) { fail(e, 'Couldn’t dismiss it'); }
      },
    });
    const offEv = listen('event', ev => {
      if (/^health_/.test(ev.event_type || '')) { load(); loadChecks(); loadReported(); }
      else if (/^strategy_issue/.test(ev.event_type || '')) loadReported();
    });
    const offDay = on(main, 'change', '[data-f="dcDay"]', (e, el) => { dcDay = el.value; dc = null; paint(); loadChecks(); });
    const timer = setInterval(() => { load(); loadReported(); }, 30000);
    paint();
    load();
    loadChecks();
    loadReported();
    return () => { offActs(); offEv(); offDay(); clearInterval(timer); clearTimeout(dcTimer); };
  },
};
