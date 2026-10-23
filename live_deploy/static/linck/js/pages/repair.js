// Repair: what needs fixing. The day check (app/repair/daycheck.py) runs
// once, after the 15:45 export, replaying each deployment's day and
// checking its end; below it, what the deployments reported about
// themselves. The health monitor (app/ops_health.py) keeps checking in the
// background and alerts your phone; its open alerts show here only while
// one is open, so it can be snoozed or dismissed.

import { api } from '../core/api.js';
import { html, mount, actions, on } from '../core/dom.js';
import { hhmm, hhmmss, dStamp, dDay, fromDay, plural, price } from '../core/format.js';
import { href } from '../core/router.js';
import { listen } from '../core/store.js';
import { sect, empty, loading } from '../ui/components.js';
import { say, fail } from '../ui/toast.js';
import * as book from '../data/book.js';
import { pollStatus } from '../data/live.js';

const SNOOZES = [['30m', '30 min'], ['2h', '2 hours'], ['today', 'Until tomorrow']];
const snoozed = x => x.snoozed_until && new Date(x.snoozed_until) > new Date();

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

// A flagged day is done once it's repaired, or ignored (its numbers left as
// they are): it moves out of the to-do list into Done, where it can still
// be opened, and an ignored one brought back.
const REPAIRABLE = ['rule', 'mismatch', 'ambiguous'];
const isDone = x => !!(x.repaired || x.dismissed_at);
function doneTag(x) {
  if (x.repaired) return html`<span class="dc-done-tag ok" data-tip="The repair was applied: its trades replaced the app’s. Undo it from its case">Repaired ${dStamp(x.repaired.applied_at)}</span>`;
  if (x.dismissed_at) return html`<span class="dc-done-tag" data-tip="You left its numbers as they are: no longer marked as flagged">Ignored ${dStamp(x.dismissed_at)}</span>`;
  return '';
}

function checkRow(x, open, day) {
  const diff = x.diff || {}, rep = x.replay || {};
  const done = isDone(x);
  return html`<details class="dc-row t-${done ? (x.repaired ? 'ok' : 'mute') : CHECK[x.status][1]} ${done ? 'is-done' : ''}" ${open ? 'open' : ''} data-dc="${x.deployment_id}">
    <summary>${pill(x.status)}<b>${x.name}</b><span class="small mute">${x.strategy} · ${x.mode}</span>${doneTag(x)}<span class="dc-head">${x.headline}</span></summary>
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
        ${x.repaired ? html`<a class="btn btn-sm" href="${href(`repair/${day}/${x.deployment_id}`)}">Open the repair ›</a>`
          : REPAIRABLE.includes(x.status) ? html`<a class="btn btn-sm ${x.dismissed_at ? '' : 'btn-primary'}" href="${href(`repair/${day}/${x.deployment_id}`)}">Review and repair ›</a>` : ''}
        ${!x.repaired && REPAIRABLE.includes(x.status) ? (x.dismissed_at
          ? html`<button type="button" class="btn btn-sm btn-quiet" data-act="unignoreDay" data-dep="${x.deployment_id}">Undo ignore</button>`
          : html`<button type="button" class="btn btn-sm btn-quiet" data-act="ignoreDay" data-dep="${x.deployment_id}" data-tip="${'You’ve looked at it and its numbers stand.\nIt moves to Done and stops being marked as flagged on every page.\nA repair stays possible.'}">Ignore</button>`) : ''}
        <a class="small" href="${href(`d/${x.deployment_id}`)}">Open deployment ›</a></div>
    </div>
  </details>`;
}

function dayCheckSection(c, day) {
  if (!c) return loading(3);
  const results = c.results || [];
  const flagged = results.filter(x => x.status !== 'ok');
  const todo = flagged.filter(x => !isDone(x)), done = flagged.filter(isDone);
  const fine = results.filter(x => x.status === 'ok');
  // what's left to do, by finding; then how many are done
  const tally = {};
  for (const x of [...todo, ...fine]) tally[x.status] = (tally[x.status] || 0) + 1;
  const counts = Object.entries(tally).sort((a, b) => Object.keys(CHECK).indexOf(a[0]) - Object.keys(CHECK).indexOf(b[0]));
  const days = c.days || [];
  return html`<div class="dc">
    <div class="dc-bar">
      ${days.length ? html`<select class="input" data-f="dcDay" aria-label="Day">${days.map(k => html`<option value="${k}" ${k === (day || c.day) ? 'selected' : ''}>${dDay(fromDay(k))}</option>`)}</select>` : ''}
      ${runLine(c.run)}
      <span class="grow"></span>
      <button type="button" class="btn btn-sm" data-act="runCheck" ${c.run && c.run.status === 'running' ? 'disabled' : ''}>Run the day check${c.day ? ` for ${dDay(fromDay(day || c.day))}` : ' now'}</button>
    </div>
    ${counts.length || done.length ? html`<div class="dc-counts">${counts.map(([k, n]) => html`<span>${pill(k)} ${n}</span>`)}${done.length ? html`<span><span class="dc-pill t-done" data-tip="Repaired, or ignored with its numbers left as they are">Done</span> ${done.length}</span>` : ''}</div>` : ''}
    ${!results.length ? empty('', c.day ? 'No results for this day yet.' : 'The day check runs by itself once the 15:45 export succeeds. Each deployment’s day is replayed from its open on Kite’s 1-minute candles and checked against what the app did.')
      : html`<div class="dc-list">${todo.map(x => checkRow(x, todo.length <= 3, c.day))}
        ${!todo.length && done.length ? html`<p class="small mute dc-clear" style="margin:0">Nothing left to do for this day: ${done.length === 1 ? 'the flagged deployment is' : `all ${done.length} flagged are`} repaired or ignored.</p>` : ''}
        ${done.length ? html`<details class="dc-fine dc-done"><summary>${done.length} done: repaired or ignored</summary>${done.map(x => checkRow(x, false, c.day))}</details>` : ''}
        ${fine.length ? html`<details class="dc-fine"><summary>${plural(fine.length, 'deployment')} as expected</summary>${fine.map(x => checkRow(x, false, c.day))}</details>` : ''}</div>`}
  </div>`;
}

export default {
  title: () => 'Repair',
  render(main) {
    let alerts = null, dc = null, dcDay = '', dcTimer = null, reported = null;
    async function loadReported() {
      try { reported = (await api.strategyIssues(true)).issues; } catch (e) { reported = []; }
      paint();
    }
    async function loadAlerts() {
      try { alerts = (await api.ops()).open; } catch (e) { alerts = null; }
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
          <div class="col"><h1 class="t-h1">Repair</h1><span class="mute">What needs fixing: each day checked once after the 15:45 export, and what the deployments reported about themselves.</span></div>
        </div>
        <div class="page" style="padding-top:4px">
          ${alerts && alerts.length ? html`
            ${sect('Open alerts', 'Each alerts your phone until it clears or you snooze it')}
            <div class="ops-list">${alerts.map(issueCard)}</div>` : ''}
          ${sect('Day check', 'Runs once after the 15:45 export: each deployment’s day replayed from its open and checked against what the app did')}
          ${dayCheckSection(dc, dcDay)}
          ${sect('Reported by the deployments', 'What each running deployment reported about itself — open ones first, then the last few days')}
          ${reportedSection(reported)}
        </div>`);
    }
    const offActs = actions(main, {
      snooze: async x => {
        try {
          await api.snoozeIssue(x.id, x.p);
          say(x.p === 'clear' ? 'Alerts are back on for this issue.' : `Snoozed ${SNOOZES.find(s => s[0] === x.p)[1].toLowerCase()}. It stays open here.`);
          await loadAlerts(); pollStatus();
        } catch (e) { fail(e, 'Couldn’t snooze it'); }
      },
      runCheck: async () => {
        try {
          await api.runDayCheck(dcDay || (dc && dc.day) ? { day: dcDay || dc.day } : {});
          say('The day check is running. It replays every deployment, so it takes a few minutes.');
          await loadChecks();
        } catch (e) { fail(e, 'Couldn’t start the day check'); }
      },
      ignoreDay: async x => {
        try {
          await api.dismissDay(dc.day, x.dep);
          say('Ignored. Its numbers stand: it’s in Done and no longer marked as flagged.');
          await Promise.all([loadChecks(), book.load(['book'])]);
        } catch (e) { fail(e, 'Couldn’t ignore it'); }
      },
      unignoreDay: async x => {
        try { await api.dismissDay(dc.day, x.dep, true); say('Back on the list, and flagged again.'); await Promise.all([loadChecks(), book.load(['book'])]); }
        catch (e) { fail(e, 'Couldn’t undo it'); }
      },
      dismissReport: async x => {
        try { await api.dismissStrategyIssue(x.id); say('Dismissed. If the deployment reports it again, it comes back.'); await loadAlerts(); await loadReported(); pollStatus(); }
        catch (e) { fail(e, 'Couldn’t dismiss it'); }
      },
      dismiss: async x => {
        try { await api.dismissIssue(x.id); say('Dismissed. If the problem is still there, it opens again.'); await loadAlerts(); pollStatus(); } catch (e) { fail(e, 'Couldn’t dismiss it'); }
      },
    });
    const offEv = listen('event', ev => {
      if (/^health_/.test(ev.event_type || '')) { loadAlerts(); loadChecks(); loadReported(); }
      else if (/^strategy_issue/.test(ev.event_type || '')) loadReported();
    });
    const offDay = on(main, 'change', '[data-f="dcDay"]', (e, el) => { dcDay = el.value; dc = null; paint(); loadChecks(); });
    paint();
    loadAlerts();
    loadChecks();
    loadReported();
    return () => { offActs(); offEv(); offDay(); clearTimeout(dcTimer); };
  },
};
