// "Things that need you." Each rule turns state into zero or more items
// that name the problem and offer the one action that fixes it. Items
// disappear when resolved. Add a rule by appending to RULES.

import { kite, health } from './live.js';
import { deployments, healthOf, vixSpike } from './book.js';
import { STREAK_ALERT } from './config.js';
import { ui } from '../core/store.js';
import { plural } from '../core/format.js';

const dismissed = ui('alerts', { off: {}, health: {}, spike: '' });

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
  // VIX jumped 10%+ today (app/vix.py): what holds sold premium loses
  // as it rises. Dismissed for the day.
  () => {
    const s = vixSpike();
    if (!s || dismissed.get().spike === (s.at || '').slice(0, 10)) return [];
    const ex = s.exposed || [];
    return [{
      key: 'vixspike', kind: 'vix', canDismiss: true, spikeDay: (s.at || '').slice(0, 10),
      title: `VIX up ${Math.round(s.change_pct)}% today: ${s.prev_close.toFixed(2)} → ${s.vix.toFixed(2)}`,
      body: ex.length ? `${plural(ex.length, 'deployment')} hold sold premium that loses as VIX rises: ${ex.slice(0, 3).map(e => e.name).join(', ')}${ex.length > 3 ? ` and ${ex.length - 3} more` : ''}.` : 'Nothing open is selling premium.',
      act: ex.length ? 'See its VIX' : 'Got it', action: ex.length ? { type: 'open', id: ex[0].id, tab: 'geek/volatility' } : { type: 'dismiss' },
    }];
  },
  // Drifting from how it normally trades (app/strategy_health.py): one
  // item per finding, dismissed until the next trade changes it.
  list => list.filter(v => v.rawStatus !== 'stopped' && !v.isLab).flatMap(v => healthOf(v.id)
    .filter(f => !(dismissed.get().health || {})[`${v.id}:${f.key}`])
    .map(f => ({
      key: `health:${v.id}:${f.key}`, kind: 'health', id: v.id, canDismiss: true, healthKey: `${v.id}:${f.key}`,
      title: `${v.name}: ${f.title.toLowerCase()}`, body: f.body,
      act: 'Open', action: { type: 'open', id: v.id, tab: 'performance' },
    }))),
];

export function alerts(list = deployments()) {
  return RULES.flatMap(rule => rule(list)).map((a, i) => ({ ...a, n: String(i + 1).padStart(2, '0') }));
}
export function dismiss(a) {
  if (!a.canDismiss) return;
  const cur = dismissed.get();
  if (a.spikeDay) dismissed.set({ spike: a.spikeDay });
  else if (a.healthKey) dismissed.set({ health: { ...(cur.health || {}), [a.healthKey]: Date.now() } });
  else dismissed.set({ off: { ...cur.off, [a.id]: a.streak } });
}
// How much a deployment needs you, for sorting the Desk.
export function attention(v) {
  return (v.status === 'error' || v.status === 'down' ? 8 : 0) + (v.rawStatus === 'paused' && v.legs.length ? 4 : 0)
    + (v.streak >= STREAK_ALERT ? 2 : 0) + (healthOf(v.id).length ? 2 : 0) + (v.quiet ? 1 : 0);
}
