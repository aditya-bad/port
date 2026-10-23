// "Things that need you." Each rule turns state into zero or more items
// that name the problem and offer the one action that fixes it. Items
// disappear when resolved. Add a rule by appending to RULES.
//
// Every item can be dismissed. A dismissal is kept per user on the server
// (app/v2/alerts.py), so it holds on every device, and the other open
// devices hear about it at once (an alerts_dismissed event). It stores
// the item's token, what it said when dismissed: the item stays hidden
// while that's unchanged and comes back when the situation changes (a
// longer streak, a different error, the next day).

import { kite, health } from './live.js';
import { deployments, healthOf, vixSpike, shadowRejections } from './book.js';
import { STREAK_ALERT } from './config.js';
import { api } from '../core/api.js';
import { ui, emit, listen } from '../core/store.js';
import { plural, dayKey } from '../core/format.js';

const cache = ui('alertsDismissed', { map: {} });   // the server's copy, for a first paint before it answers
let dismissed = { ...cache.get().map };

// A short, stable fingerprint of what an item says.
export function fp(s) {
  let h = 5381;
  for (const c of String(s)) h = ((h * 33) ^ c.charCodeAt(0)) >>> 0;
  return h.toString(36);
}

export const RULES = [
  // Active but nothing is running its strategy: no entries, no exits.
  () => health.notRunning.map(x => ({
    key: `down:${x.id}`, token: fp(x.reason), kind: 'error', id: x.id,
    title: `${x.name} isn't running`,
    body: `Marked active, but its strategy isn't running, so it won't enter, adjust or exit${x.open_positions ? `. ${plural(x.open_positions, 'open position')} will stay open` : ''}. Reason: ${x.reason}.`,
    act: 'Restart', action: { type: 'restart', id: x.id },
  })),
  // dismissed for the day: the header's Kite chip still says it
  () => (kite.state === 'connected' ? [] : [{
    key: 'kite', token: `${kite.state === 'connecting' ? 'connecting' : 'expired'}:${dayKey(new Date())}`, kind: 'kite',
    title: kite.state === 'connecting' ? 'Connecting to Kite…' : 'Kite session expired',
    body: 'Prices are frozen and no deployment can trade until you log in again.',
    act: 'Reconnect', action: { type: 'kite' },
  }]),
  list => list.filter(v => v.status === 'error').map(v => ({
    key: `err:${v.id}`, token: fp(v.issue.text), kind: 'error', id: v.id,
    title: `${v.name} errored`,
    body: `${v.issue.text}${v.legs.length ? ` ${plural(v.legs.length, 'leg')} open.` : ''}`,
    act: 'Review', action: { type: 'open', id: v.id },
  })),
  list => list.filter(v => v.rawStatus === 'paused' && v.legs.length).map(v => ({
    key: `paused:${v.id}`, token: String(v.legs.length), kind: 'paused', id: v.id,
    title: `${v.name} is paused with ${plural(v.legs.length, 'open leg')}`,
    body: 'Positions are not being managed while paused.',
    act: 'Resume', action: { type: 'resume', id: v.id },
  })),
  list => list.filter(v => v.rawStatus === 'active' && v.streak >= STREAK_ALERT && !v.isLab).map(v => ({
    key: `streak:${v.id}`, token: String(v.streak), kind: 'streak', id: v.id,
    title: `${v.name}: losing streak of ${v.streak}`,
    body: `The last ${v.streak} trades closed at a loss.`,
    act: 'Open', action: { type: 'open', id: v.id, tab: 'performance' },
  })),
  // VIX jumped 10%+ today (app/vix.py): what holds sold premium loses
  // as it rises. Dismissed for the day.
  () => {
    const s = vixSpike();
    if (!s) return [];
    const ex = s.exposed || [];
    return [{
      key: 'vixspike', token: (s.at || '').slice(0, 10), kind: 'vix',
      title: `VIX up ${Math.round(s.change_pct)}% today: ${s.prev_close.toFixed(2)} → ${s.vix.toFixed(2)}`,
      body: ex.length ? `${plural(ex.length, 'deployment')} hold sold premium that loses as VIX rises: ${ex.slice(0, 3).map(e => e.name).join(', ')}${ex.length > 3 ? ` and ${ex.length - 3} more` : ''}.` : 'Nothing open is selling premium.',
      act: ex.length ? 'See its VIX' : 'Got it', action: ex.length ? { type: 'open', id: ex[0].id, tab: 'geek/volatility' } : { type: 'dismiss' },
    }];
  },
  // Drifting from how it normally trades (app/strategy_health.py): one
  // item per finding, dismissed until what it says changes.
  list => list.filter(v => v.rawStatus !== 'stopped' && !v.isLab).flatMap(v => healthOf(v.id).map(f => ({
    key: `health:${v.id}:${f.key}`, token: fp(`${f.title}|${f.body}`), kind: 'health', id: v.id,
    title: `${v.name}: ${f.title.toLowerCase()}`, body: f.body,
    act: 'Open', action: { type: 'open', id: v.id, tab: 'performance' },
  }))),
  // Shadow mode (app/shadow.py): an order Kite would have refused. Once
  // per deployment, rule and day; dismissed for that day.
  list => {
    const byId = new Map(list.filter(v => !v.isLab).map(v => [v.id, v]));
    return shadowRejections().filter(x => byId.has(x.deployment_id)).map(x => ({
      key: `shadow:${x.deployment_id}:${x.rule}:${x.day}`, token: x.day, kind: 'shadow', id: x.deployment_id,
      title: `${x.name}: Kite would have refused ${x.n > 1 ? `${x.n} orders` : 'an order'} today`,
      body: `${SHADOW_RULE[x.rule] || x.rule}: ${x.detail}${x.symbol ? ` ${x.n > 1 ? 'First on' : 'On'} ${x.symbol}.` : ''} The paper trade went through; live, it wouldn’t have.`,
      act: 'See its fills', action: { type: 'open', id: x.deployment_id, tab: 'now' },
    }));
  },
];
const SHADOW_RULE = { lot: 'Lot size', freeze: 'Freeze limit', band: 'Price band', book: 'Order book', follow: 'Within 60 s', margin: 'Margin', legs: 'Leg by leg', rate: 'Order rate' };

const all = list => RULES.flatMap(rule => rule(list)).map(a => ({ ...a, canDismiss: true }));
const isOff = a => dismissed[a.key] === a.token;

// What needs you now: everything not dismissed.
export function alerts(list = deployments()) {
  return all(list).filter(a => !isOff(a)).map((a, i) => ({ ...a, n: String(i + 1).padStart(2, '0') }));
}
// Dismissed but still true, so they can be brought back.
export function dismissedAlerts(list = deployments()) {
  return all(list).filter(isOff);
}

function apply(map) {
  dismissed = map || {};
  cache.set({ map: dismissed });
  emit('alerts');
}
export async function loadDismissed() {
  try { apply(await api.alertDismissals()); } catch (e) { /* signed out or offline: keep the cached copy */ }
}
async function change(items, restore) {
  const before = dismissed;
  const next = { ...dismissed };
  items.forEach(a => { next[a.key] = a.token; });
  restore.forEach(k => { delete next[k]; });
  apply(next);   // at once, then the server's answer
  try { apply(await api.changeAlertDismissals(items.map(a => ({ key: a.key, token: a.token })), restore)); }
  catch (e) { apply(before); throw e; }
}
export const dismiss = a => change([a], []);
export const dismissAll = list => change(list, []);
export const restore = a => change([], [a.key]);

// Another device dismissed or brought one back.
listen('event', ev => { if (ev && ev.event_type === 'alerts_dismissed') loadDismissed(); });

// How much a deployment needs you, for sorting the Desk.
export function attention(v) {
  return (v.status === 'error' || v.status === 'down' ? 8 : 0) + (v.rawStatus === 'paused' && v.legs.length ? 4 : 0)
    + (v.streak >= STREAK_ALERT ? 2 : 0) + (healthOf(v.id).length ? 2 : 0) + (v.quiet ? 1 : 0);
}
