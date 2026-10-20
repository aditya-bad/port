import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { runningCycle } from '../../static/linck/js/pages/deployment/days.js';
import { setBasis } from '../../static/linck/js/core/basis.js';

const now = Date.parse('2026-10-20T12:00:00+05:30');
const legs = [{ opened_at: '2026-10-16T09:20:00+05:30' }, { opened_at: '2026-10-17T10:00:00+05:30' }];
const cycles = [
  { status: 'closed', start: '2026-09-01T09:20:00+05:30', realized_pnl: 900, realized_pnl_net: 800, realized_pnl_net_slip: 780, adjustments: 1 },
  { status: 'open', start: '2026-10-16T09:20:00+05:30', realized_pnl: -1200, realized_pnl_net: -1300, realized_pnl_net_slip: -1350, adjustments: 2 },
];

test('the running cycle: what it booked (rolled legs) plus the open legs now', () => {
  setBasis('gross');
  const r = runningCycle(cycles, legs, 500, now);
  assert.equal(+r.start, Date.parse('2026-10-16T09:20:00+05:30'));
  assert.deepEqual([r.booked, r.unreal, r.p, r.adj, r.legs, r.held], [-1200, 500, -700, 2, 2, 4]);
});

test('booked follows the P&L basis', () => {
  setBasis('charges');
  assert.equal(runningCycle(cycles, legs, 500, now).booked, -1300);
  setBasis('all');
  assert.equal(runningCycle(cycles, legs, 500, now).booked, -1350);
  setBasis('gross');
});

test('nothing running: no row', () => {
  assert.equal(runningCycle(cycles.slice(0, 1), [], 0, now), null);
});

test('legs open but the cycle list not loaded yet: started with the first leg, nothing booked', () => {
  const r = runningCycle([], legs, 250, now);
  assert.equal(+r.start, Date.parse('2026-10-16T09:20:00+05:30'));
  assert.equal(r.p, 250);
});
