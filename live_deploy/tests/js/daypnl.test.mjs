import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { dayPnl, quietDay, tradedToday } from '../../static/linck/js/data/book.js';

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

// The Desk's hero: on a day nothing has traded or moved yet, today's ₹0
// would mislead, so it shows the open positions' P&L instead.
test('a quiet day: nothing priced since the open, nothing filled, nothing booked', () => {
  const dep = (id, extra = {}) => ({ id, rawStatus: 'active', legs, ...extra });
  const x = { a: { today: 0, fresh: false }, b: { today: 0, fresh: false }, s: { today: 0, fresh: true } };
  const ev = { a: [], b: [] };
  const q = list => quietDay(list, v => x[v.id], v => ev[v.id] || []);
  assert.equal(q([dep('a'), dep('b')]), true, 'holiday, weekend, before 09:15, or Kite down');
  assert.equal(q([dep('a'), dep('s', { rawStatus: 'stopped' })]), true, 'a stopped deployment doesn’t count');
  assert.equal(q([]), true);
  x.b = { today: 0, fresh: true };
  assert.equal(q([dep('a'), dep('b')]), false, 'a leg ticked since the open: today is real, even if ₹0');
  x.b = { today: 0, fresh: false };
  ev.b = [{ kind: 'fill' }];
  assert.equal(q([dep('a'), dep('b')]), false, 'a fill today');
  ev.b = [];
  x.b = { today: -750, fresh: false };
  assert.equal(q([dep('a'), dep('b')]), false, 'something booked today');
  assert.equal(tradedToday({ today: 0.2, fresh: false }, []), false, 'paise of rounding aren’t a trade');
});
