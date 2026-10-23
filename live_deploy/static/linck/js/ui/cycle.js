// How a deployment's cycle P&L reads on every page (data/book.js:
// cyclePnl). A cycle is one entry to its exit: for an intraday deployment
// the day, for a positional one the episode, entry through adjustments to
// exit. Pages show the cycle's figure, say which cycle it is, and on the
// day a positional cycle closes, show that one beside the new.

import { html } from '../core/dom.js';
import { inr, dDay, hhmm, tone } from '../core/format.js';
import { pnlOf } from '../core/basis.js';

// The figure, or a dash when there's no cycle (stopped, or flat).
export const cycleText = x => (x.cycle == null ? '—' : inr(x.cycle));
export const cycleTone = x => (x.cycle == null ? 'flat' : tone(x.cycle));

// Which cycle it is, in a few words.
export function cycleWhat(v, x) {
  if (v.rawStatus === 'stopped') return 'stopped';
  if (!x.positional) return 'today’s cycle';
  const open = x.cycleParts.find(p => p.open);
  if (open) return open.from ? `cycle since ${dDay(open.from)}` : 'open cycle';
  if (x.cycleParts.length) return 'cycle closed today';
  return x.lastCycle ? `flat · last cycle ${inr(pnlOf(x.lastCycle))}` : 'flat';
}

// The cycles that closed today when a new one is open: shown beside it.
export function closedToday(x) {
  const open = x.cycleParts.some(p => p.open);
  const closed = x.cycleParts.filter(p => p.kind === 'closed');
  return open && closed.length ? closed.reduce((a, p) => a + p.value, 0) : null;
}

// One line under a cycle figure, for the switch day.
export function closedNote(x) {
  const c = closedToday(x);
  return c == null ? '' : html`<span class="cyc-note tiny mute" data-closed-today><span>+ closed today</span> <b class="${tone(c)}">${inr(c)}</b></span>`;
}

// A tooltip: every cycle shown, and what a cycle is.
export function cycleTip(v, x) {
  if (v.rawStatus === 'stopped') return 'Stopped: no cycle';
  if (!x.positional) return `Today’s cycle  ${inr(x.cycle)}\nAn intraday cycle is the day.`;
  const lines = x.cycleParts.map(p => (p.open
    ? `Open cycle${p.from ? ` since ${dDay(p.from)}` : ''}  ${inr(p.value)}`
    : `Closed today${p.from ? ` (${dDay(p.from)} → ${hhmm(p.to)})` : ''}  ${inr(p.value)}`));
  if (!lines.length) lines.push(x.lastCycle ? `Flat. Last cycle  ${inr(pnlOf(x.lastCycle))}` : 'Flat: no cycle yet');
  return `${lines.join('\n')}\nA positional cycle is one entry, its adjustments and its exit.`;
}
