// Repair: is everything that has to work, working — and what needs fixing.
// The health monitor (app/ops_health.py) checks Kite, the database, the
// 15:45 option-history export and every deployment each minute; every
// problem is an issue that alerts every minute until it clears. Here they
// are: open first, with snooze and dismiss, then the recently cleared.

import { api } from '../core/api.js';
import { html, mount, actions } from '../core/dom.js';
import { hhmm, hhmmss, dStamp, dayKey, plural } from '../core/format.js';
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
           crit ? 'bad' : d.open.length ? 'warn' : 'ok')}
  </div>`;
}

function issueCard(x) {
  const zz = snoozed(x);
  return html`<article class="ops-issue ${x.severity} ${zz ? 'zz' : ''}" data-issue="${x.id}">
    <div class="ops-head">
      <span class="ops-sev">${x.severity === 'critical' ? 'Critical' : 'Warning'}</span>
      <h4>${x.title}</h4>
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
    let d = null, err = null, busy = false;
    async function load(fresh = false) {
      try {
        d = fresh ? await api.opsCheck() : await api.ops();
        err = null;
      } catch (e) { err = e; }
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
      dismiss: async x => {
        try { await api.dismissIssue(x.id); say('Dismissed. If the problem is still there, the next check opens it again.'); await load(); pollStatus(); } catch (e) { fail(e, 'Couldn’t dismiss it'); }
      },
    });
    const offEv = listen('event', ev => { if (/^health_/.test(ev.event_type || '')) load(); });
    const timer = setInterval(() => load(), 30000);
    paint();
    load();
    return () => { offActs(); offEv(); clearInterval(timer); };
  },
};
