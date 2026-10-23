import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { dayPnl, cyclePnl } from '../../static/linck/js/data/book.js';

const OPEN = Date.parse('2026-10-20T09:15:00+05:30');
const legs = [{ instrument_token: 1 }, { instrument_token: 2 }];
// gross basis (the default): equity_open/realized_open read their "gross"
const base = {
  realized: 1000, overall: 1000 + 4200, legs,
  equityOpen: { gross: 1000, net: 0, net_slip: 0 },          // a snapshot that missed the legs' value
  realizedOpen: { gross: 1000, net: 0, net_slip: 0 }, openAt: OPEN, stopped: false,
};

test('a holiday with Kite down: legs unpriced since the open have not moved', () => {
  const r = dayPnl(base, () => null);
  assert.deepEqual(r, { today: 0, stale: true, fresh: false }, 'not the legs’ whole unrealized ₹4,200');
});

test('a price from before the open (yesterday’s last tick) is not fresh', () => {
  assert.equal(dayPnl(base, () => OPEN - 60_000).stale, true);
});

test('a trade closed today still counts on a stale day', () => {
  const r = dayPnl({ ...base, realized: 1600, overall: 1600 + 4200 }, () => null);
  assert.deepEqual(r, { today: 600, stale: true, fresh: false });
});

test('once any leg has a fresh tick, today is P&L now minus P&L at the open', () => {
  const r = dayPnl({ ...base, equityOpen: { gross: 1000 + 4000 } }, tok => (tok === 2 ? OPEN + 60_000 : null));
  assert.deepEqual(r, { today: 200, stale: false, fresh: true });
});

test('no open legs: closed trades against the open, as before', () => {
  assert.deepEqual(dayPnl({ ...base, legs: [], overall: 1500, equityOpen: { gross: 1000 } }, () => null), { today: 500, stale: false, fresh: false });
});

test('a stopped deployment has no day', () => {
  assert.deepEqual(dayPnl({ ...base, stopped: true }, () => null), { today: 0, stale: false, fresh: false });
});

test('an older server without realized_open falls back to the old rule', () => {
  assert.deepEqual(dayPnl({ ...base, realizedOpen: null }, () => null), { today: 4200, stale: false, fresh: false });
});

// Cycle P&L, what every page follows: an intraday deployment's cycle is
// the day; a positional one's is the open episode (entry, adjustments,
// rolls, exit): what its closed legs realized plus the open legs now.
test('intraday: the cycle is the day', () => {
  const c = cyclePnl({ positional: false, today: -750, unreal: 300, hasLegs: true });
  assert.equal(c.cycle, -750);
  assert.equal(c.sum, -750);
  assert.equal(c.since, null);
  assert.deepEqual(c.parts, [{ kind: 'day', value: -750, open: true }]);
});

test('positional: the open cycle is what its closed legs realized plus the open legs, from its entry', () => {
  const c = cyclePnl({ positional: true, today: 0, unreal: 1200, hasLegs: true, open: { realized: 750, start: '2026-10-19T10:00:00+05:30' } });
  assert.equal(c.cycle, 1950, 'not the day’s ₹0: the cycle holds since Monday');
  assert.equal(c.sum, 1950);
  assert.equal(c.since, '2026-10-19T10:00:00+05:30');
  assert.equal(c.parts.length, 1);
});

test('the switch day: a cycle closed today shows beside the new one, the headline is the new one', () => {
  const c = cyclePnl({ positional: true, today: 0, unreal: -200, hasLegs: true,
    open: { realized: 0, start: '2026-10-23T13:00:00+05:30' },
    closedToday: [{ value: 6000, from: '2026-10-14T09:30:00+05:30', to: '2026-10-23T12:55:00+05:30' }] });
  assert.equal(c.cycle, -200, 'the new cycle');
  assert.equal(c.sum, 5800, 'totals add both on the day it switched');
  assert.deepEqual(c.parts.map(q => q.kind), ['closed', 'open']);
  assert.equal(c.since, '2026-10-23T13:00:00+05:30');
});

test('the next day only the new cycle is left (no cycle closed today)', () => {
  const c = cyclePnl({ positional: true, today: 0, unreal: 400, hasLegs: true, open: { realized: -100, start: '2026-10-23T13:00:00+05:30' } });
  assert.equal(c.cycle, 300);
  assert.deepEqual(c.parts.map(q => q.kind), ['open']);
});

test('positional and flat after exiting today: the closed cycle is what it shows', () => {
  const c = cyclePnl({ positional: true, today: 0, unreal: 0, hasLegs: false, closedToday: [{ value: -1400, from: 'a', to: 'b' }] });
  assert.equal(c.cycle, -1400);
  assert.equal(c.since, null);
});

test('positional and flat with nothing closed today: no cycle', () => {
  assert.equal(cyclePnl({ positional: true, today: 0, unreal: 0, hasLegs: false }).cycle, null);
});

test('positional legs the server has no cycle for yet (just entered): their unrealized', () => {
  assert.equal(cyclePnl({ positional: true, today: 0, unreal: 250, hasLegs: true }).cycle, 250);
});

test('a stopped deployment has no cycle', () => {
  assert.deepEqual(cyclePnl({ positional: true, stopped: true, today: 5, unreal: 9, hasLegs: true }), { cycle: null, parts: [], sum: 0, since: null });
});

test('the Desk’s "If VIX +1": the total, each deployment, and what was left out', async () => {
  const { vixSub } = await import('../../static/linck/js/pages/desk.js');
  const out = String(vixSub({ total: -8200, missing: 1, deployments: [{ name: 'Nifty Strangle', per_point: -9000 }, { name: 'ST Inverse', per_point: 800 }] }));
  assert.match(out, /If VIX \+1/);
  assert.match(out, /−₹8,200/);
  assert.match(out, /Nifty Strangle  −₹9,000/);
  assert.match(out, /1 leg left out/);
  assert.equal(vixSub(null), '');
  assert.equal(vixSub({ total: 0, deployments: [] }), '', 'nothing open: nothing shown');
});
