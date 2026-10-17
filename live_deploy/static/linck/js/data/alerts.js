// "Things that need you." Each rule turns state into zero or more items
// that name the problem and offer the one action that fixes it. Items
// disappear when resolved. Add a rule by appending to RULES.

import { kite, health } from './live.js';
import { deployments } from './book.js';
import { STREAK_ALERT } from './config.js';
import { ui } from '../core/store.js';
import { plural } from '../core/format.js';

const dismissed = ui('alerts', { off: {} });

export const RULES = [
  // Active but nothing is running its strategy: no entries, no exits.
  () => health.notRunning.map(x => ({
    key: `down:${x.id}`, kind: 'error', id: x.id,
    title: `${x.name} isn't running`,
    body: `Marked active, but its strategy isn't running, so it won't enter, adjust or exit${x.open_positions ? `. ${plural(x.open_positions, 'open position')} will stay open` : ''}. Reason: ${x.reason}.`,
    act: 'Restart', action: { type: 'restart', id: x.id },
  })),
  () => (kite.state === 'connected' ? [] : [{
    key: 'kite', kind: 'kite',
    title: kite.state === 'connecting' ? 'Connecting to Kite…' : 'Kite session expired',
    body: 'Prices are frozen and no deployment can trade until you log in again.',
    act: 'Reconnect', action: { type: 'kite' },
  }]),
  list => list.filter(v => v.status === 'error').map(v => ({
    key: `err:${v.id}`, kind: 'error', id: v.id,
    title: `${v.name} errored`,
    body: `${v.issue.text}${v.legs.length ? ` ${plural(v.legs.length, 'leg')} open.` : ''}`,
    act: 'Review', action: { type: 'open', id: v.id },
  })),
  list => list.filter(v => v.rawStatus === 'paused' && v.legs.length).map(v => ({
    key: `paused:${v.id}`, kind: 'paused', id: v.id,
    title: `${v.name} is paused with ${plural(v.legs.length, 'open leg')}`,
    body: 'Positions are not being managed while paused.',
    act: 'Resume', action: { type: 'resume', id: v.id },
  })),
  list => list.filter(v => v.rawStatus === 'active' && v.streak >= STREAK_ALERT && !v.isLab)
    .filter(v => dismissed.get().off[v.id] !== v.streak)
    .map(v => ({
      key: `streak:${v.id}`, kind: 'streak', id: v.id, canDismiss: true, streak: v.streak,
      title: `${v.name}: losing streak of ${v.streak}`,
      body: `The last ${v.streak} trades closed at a loss.`,
      act: 'Open', action: { type: 'open', id: v.id, tab: 'performance' },
    })),
];

export function alerts(list = deployments()) {
  return RULES.flatMap(rule => rule(list)).map((a, i) => ({ ...a, n: String(i + 1).padStart(2, '0') }));
}
export function dismiss(a) {
  if (!a.canDismiss) return;
  dismissed.set({ off: { ...dismissed.get().off, [a.id]: a.streak } });
}
// How much a deployment needs you, for sorting the Desk.
export function attention(v) {
  return (v.status === 'error' || v.status === 'down' ? 8 : 0) + (v.rawStatus === 'paused' && v.legs.length ? 4 : 0)
    + (v.streak >= STREAK_ALERT ? 2 : 0) + (v.quiet ? 1 : 0);
}
