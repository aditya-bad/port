// How a cycle reads on every page (static/linck/js/ui/cycle.js).
import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { cycleText, cycleWhat, closedToday, cycleTip } from '../../static/linck/js/ui/cycle.js';

const v = { rawStatus: 'active' };
const open = (value, from = '2026-10-19T10:00:00+05:30') => ({ kind: 'open', value, from, open: true });
const shut = value => ({ kind: 'closed', value, from: '2026-10-09T09:30:00+05:30', to: '2026-10-23T12:55:00+05:30', open: false });

test('no cycle reads as a dash, not ₹0', () => {
  assert.equal(cycleText({ cycle: null }), '—');
});

test('which cycle it is', () => {
  assert.equal(cycleWhat(v, { positional: false, cycleParts: [] }), 'today’s cycle');
  assert.match(cycleWhat(v, { positional: true, cycleParts: [open(500)] }), /^cycle since /);
  assert.equal(cycleWhat(v, { positional: true, cycleParts: [shut(900)] }), 'cycle closed today');
  assert.equal(cycleWhat(v, { positional: true, cycleParts: [], lastCycle: null }), 'flat');
  assert.equal(cycleWhat({ rawStatus: 'stopped' }, { positional: true, cycleParts: [] }), 'stopped');
});

test('the switch day only: the closed cycle beside an open one', () => {
  assert.equal(closedToday({ cycleParts: [shut(6000), open(-200)] }), 6000);
  assert.equal(closedToday({ cycleParts: [shut(6000)] }), null, 'flat after the exit: the closed one is the figure itself');
  assert.equal(closedToday({ cycleParts: [open(-200)] }), null);
});

test('the tip lists each cycle shown and says what a cycle is', () => {
  const t = cycleTip(v, { positional: true, cycleParts: [shut(6000), open(-200)] });
  assert.match(t, /Closed today .*6,000[\s\S]*Open cycle since .*200[\s\S]*one entry, its adjustments and its exit/);
  assert.match(cycleTip(v, { positional: false, cycle: 300, cycleParts: [] }), /An intraday cycle is the day/);
});
