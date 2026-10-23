// The Alerts panel: everything that needs you, from the header's Alerts
// button on every page. Each item does its one thing (Restart, Resume,
// Reconnect, Open) or is dismissed; dismissals hold on every device
// (data/alerts.js). The health monitor's open alerts (app/ops_health.py)
// are here too, with snooze and dismiss. Dismissed items that are still
// true can be brought back.

import { api } from '../core/api.js';
import { html, mount, on } from '../core/dom.js';
import { dStamp } from '../core/format.js';
import { go } from '../core/router.js';
import { listen } from '../core/store.js';
import * as book from '../data/book.js';
import { health, pollStatus } from '../data/live.js';
import { alerts, dismissedAlerts, dismiss, dismissAll, restore } from '../data/alerts.js';
import { kiteDialog, resume, restartDeployment } from '../actions.js';
import { say, fail } from './toast.js';

const SNOOZES = [['30m', '30 min'], ['2h', '2 hours'], ['today', 'Until tomorrow']];
const awake = x => !x.snoozed_until || new Date(x.snoozed_until) <= new Date();

// The monitor's open alerts that aren't snoozed, worst first.
export const monitorAlerts = () => health.issues.filter(awake)
  .sort((a, b) => (a.severity === 'critical' ? 0 : 1) - (b.severity === 'critical' ? 0 : 1));
// What the header counts.
export const alertCount = () => alerts().length + monitorAlerts().length;

// Do an item's one thing.
export function runAlert(a) {
  const ac = a.action;
  if (ac.type === 'kite') kiteDialog();
  else if (ac.type === 'open') go(`d/${ac.id}${ac.tab ? `/${ac.tab}` : ''}`);
  else if (ac.type === 'dismiss') dismiss(a).catch(e => fail(e, 'Couldn’t dismiss it'));
  else if (ac.type === 'resume') { const v = book.find(ac.id); if (v) resume([v]); }
  else if (ac.type === 'restart') restartDeployment(ac.id);
}

let el = null, offs = [], showOff = false;

function item(a) {
  return html`<article class="ap-item k-${a.kind}" data-key="${a.key}">
    <div class="col" style="gap:2px;min-width:0"><b>${a.title}</b><span class="small mute pretty">${a.body}</span></div>
    <div class="ap-acts">
      <button type="button" class="btn btn-sm" data-ap="run" data-key="${a.key}">${a.act}</button>
      <button type="button" class="btn btn-sm btn-quiet" data-ap="dismiss" data-key="${a.key}">Dismiss</button>
    </div>
  </article>`;
}

function monitorItem(x) {
  return html`<article class="ap-item k-${x.severity === 'critical' ? 'error' : 'health'}" data-issue="${x.id}">
    <div class="col" style="gap:2px;min-width:0"><b>${x.title}</b><span class="small mute pretty">${x.detail || ''}</span>
      <span class="tiny mute mono">Health monitor · open since ${dStamp(x.opened_at)}</span></div>
    <div class="ap-acts">
      <span class="tiny mute">Snooze</span>${SNOOZES.map(([k, l]) => html`<button type="button" class="btn btn-sm" data-ap="snooze" data-id="${x.id}" data-p="${k}">${l}</button>`)}
      <button type="button" class="btn btn-sm btn-quiet" data-ap="close" data-id="${x.id}">Dismiss</button>
    </div>
  </article>`;
}

function paint() {
  if (!el) return;
  const A = alerts(), M = monitorAlerts(), off = dismissedAlerts();
  const n = A.length + M.length;
  mount(el, html`<aside class="apanel" role="dialog" aria-modal="true" aria-labelledby="apTitle">
    <header>
      <h2 id="apTitle">Alerts <span class="ap-n">${n}</span></h2>
      <span class="grow"></span>
      ${n ? html`<button type="button" class="btn btn-sm" data-ap="all">Dismiss all</button>` : ''}
      <button type="button" class="ap-x" data-ap="close-panel" aria-label="Close">×</button>
    </header>
    <div class="ap-body">
      ${!n ? html`<p class="ap-ok mute">Nothing needs you.</p>` : ''}
      ${M.map(monitorItem)}
      ${A.map(item)}
      ${off.length ? html`<div class="ap-off">
        <button type="button" class="linkish small" data-ap="toggle-off" aria-expanded="${showOff}">${showOff ? 'Hide' : 'Show'} ${off.length} dismissed</button>
        ${showOff ? off.map(a => html`<div class="ap-item off"><div class="col" style="gap:2px;min-width:0"><b>${a.title}</b><span class="small mute pretty">${a.body}</span></div>
          <div class="ap-acts"><button type="button" class="btn btn-sm" data-ap="restore" data-key="${a.key}">Bring back</button></div></div>`) : ''}
      </div>` : ''}
    </div>
  </aside>`);
}

export function closeAlerts() {
  if (!el) return;
  offs.forEach(f => f());
  offs = [];
  el.remove();
  el = null;
}

export function openAlerts() {
  if (el) { paint(); return; }
  el = document.createElement('div');
  el.className = 'ap-scrim';
  document.body.appendChild(el);
  const find = k => alerts().find(a => a.key === k) || dismissedAlerts().find(a => a.key === k);
  const acts = {
    'close-panel': () => closeAlerts(),
    run: d => { const a = find(d.key); if (a) { if (a.action.type !== 'dismiss') closeAlerts(); runAlert(a); } },
    dismiss: async d => {
      const a = find(d.key);
      if (!a) return;
      try { await dismiss(a); say('Dismissed on all your devices.', () => restore(a)); } catch (e) { fail(e, 'Couldn’t dismiss it'); }
    },
    restore: async d => { const a = find(d.key); if (a) try { await restore(a); } catch (e) { fail(e, 'Couldn’t bring it back'); } },
    all: async () => {
      const A = alerts(), M = monitorAlerts();
      try {
        if (A.length) await dismissAll(A);
        await Promise.all(M.map(x => api.dismissIssue(x.id)));
        if (M.length) pollStatus();
        say(`Dismissed ${A.length + M.length} on all your devices.`, A.length ? () => Promise.all(A.map(restore)) : null);
      } catch (e) { fail(e, 'Couldn’t dismiss them all'); }
    },
    snooze: async d => {
      try { await api.snoozeIssue(d.id, d.p); await pollStatus(); say(`Snoozed ${SNOOZES.find(s => s[0] === d.p)[1].toLowerCase()}.`); }
      catch (e) { fail(e, 'Couldn’t snooze it'); }
    },
    close: async d => {
      try { await api.dismissIssue(d.id); await pollStatus(); say('Dismissed. If the problem is still there, it opens again.'); }
      catch (e) { fail(e, 'Couldn’t dismiss it'); }
    },
    'toggle-off': () => { showOff = !showOff; paint(); },
  };
  offs.push(on(el, 'click', '[data-ap]', (e, b) => { e.preventDefault(); const fn = acts[b.dataset.ap]; if (fn) fn(b.dataset); }));
  el.addEventListener('click', e => { if (e.target === el) closeAlerts(); });
  const onKey = e => { if (e.key === 'Escape') closeAlerts(); };
  document.addEventListener('keydown', onKey);
  offs.push(() => document.removeEventListener('keydown', onKey));
  offs.push(listen('alerts', paint), listen('data', paint), listen('status', paint));
  offs.push(() => window.removeEventListener('hashchange', closeAlerts));
  window.addEventListener('hashchange', closeAlerts);
  paint();
  const first = el.querySelector('button');
  if (first) first.focus();
}
