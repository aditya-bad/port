import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { combine, resolve, encode, decode, groupName, keyOf } from '../../static/linck/js/data/groups.js';
import { periodMetrics } from '../../static/linck/js/data/metrics.js';

const dep = (id, strategy, cap, days, trades = []) => ({
  id, name: id.toUpperCase(), strategy, strategyLabel: strategy, mode: 'Positional', modeKey: 'positional',
  cap, created: new Date('2026-01-05'),
  daily: days.map(([day, pnl]) => ({ deployment_id: id, day, pnl, pnl_net: pnl - 10, pnl_net_slip: pnl - 15, closes: 1 })),
  trades: trades.map(([closed_at, pnl]) => ({ deployment_id: id, closed_at, pnl, pnl_net: pnl - 10, pnl_net_slip: pnl - 15 })),
});
const A = dep('a', 'strangle', 100000, [['2026-02-02', 1000], ['2026-02-03', -3000]], [['2026-02-02T10:00:00Z', 1000], ['2026-02-03T10:00:00Z', -3000]]);
const B = dep('b', 'strangle', 300000, [['2026-02-03', 2500], ['2026-02-04', 500]], [['2026-02-03T11:00:00Z', 2500], ['2026-02-04T10:00:00Z', 500]]);
const C = dep('c', 'dtt', 200000, [['2026-02-02', 200]], [['2026-02-02T12:00:00Z', 200]]);

test('a group is one portfolio: daily P&L added per day in every basis, capital added', () => {
  const g = combine([A, B], 'A + B');
  assert.equal(g.cap, 400000);
  assert.deepEqual(g.daily.map(r => [r.day, r.pnl, r.pnl_net, r.pnl_net_slip, r.closes]),
    [['2026-02-02', 1000, 990, 985, 1], ['2026-02-03', -500, -520, -530, 2], ['2026-02-04', 500, 490, 485, 1]]);
  assert.equal(g.trades.length, 4);
  assert.deepEqual(g.trades.map(t => t.pnl), [1000, -3000, 2500, 500], 'pooled, in close order');
  assert.ok(g.isGroup && g.strategy === 'strangle');
});

test('its drawdown is the portfolio’s own, on the combined capital', () => {
  const g = combine([A, B], 'A + B');
  const m = periodMetrics({ trades: g.trades, daily: g.daily, cap: g.cap, pnlOf: r => r.pnl, period: 'all' });
  assert.equal(m.pnl, 1000);
  assert.equal(m.ret, 0.25, '1,000 on 4,00,000');
  assert.equal(m.dd, -500, 'B’s gain that day offsets most of A’s loss');
  assert.equal(m.n, 4);
  assert.equal(m.win, 75);
});

test('a single id is that deployment, unchanged', () => {
  const [r] = resolve([{ ids: ['c'] }], [A, B, C]);
  assert.equal(r.isGroup, false);
  assert.equal(r.name, 'C');
  assert.equal(r.daily, C.daily);
});

test('links: groups joined by ".", items by ","; order-free keys', () => {
  const items = [{ ids: ['c'] }, { ids: ['b', 'a'] }];
  assert.equal(encode(items), 'c,a.b');
  assert.deepEqual(decode('c,a.b'), [{ ids: ['c'] }, { ids: ['a', 'b'] }]);
  assert.equal(keyOf(['b', 'a']), keyOf(['a', 'b']));
  assert.deepEqual(decode('a.a.b,,'), [{ ids: ['a', 'b'] }]);
});

test('names say what was combined', () => {
  assert.equal(groupName([A, B], () => true), 'All strangle (2)');
  assert.equal(groupName([A, B], () => false), 'strangle: A + B');
  assert.equal(groupName([A, B, { ...A, name: 'A2' }], () => false), 'strangle ×3');
  assert.equal(groupName([A, C]), 'A + C');
});

test('deleted deployments drop out; an emptied group disappears', () => {
  const r = resolve([{ ids: ['a', 'gone'] }, { ids: ['gone2'] }, { ids: ['c'] }], [A, B, C]);
  assert.equal(r.length, 2);
  assert.equal(r[0].isGroup, false, 'one member left: just that deployment');
});

test('5 strangles against the other 5: same-named groups are numbered, or named', () => {
  const S = Array.from({ length: 10 }, (_, i) => dep(`s${i}`, 'strangle', 100000, [['2026-02-02', 100 * i]]));
  const items = [{ ids: S.slice(0, 5).map(v => v.id) }, { ids: S.slice(5).map(v => v.id) }];
  const [g1, g2] = resolve(items, S, { allOfStrategy: (s, n) => n === 10 });
  assert.equal(g1.name, 'strangle ×5 #1');
  assert.equal(g2.name, 'strangle ×5 #2');
  assert.equal(g1.cap, 500000);
  assert.equal(g1.daily[0].pnl, 0 + 100 + 200 + 300 + 400);
  assert.equal(g2.daily[0].pnl, 500 + 600 + 700 + 800 + 900);
  const named = resolve([{ ...items[0], name: 'Wide wings' }, { ...items[1], name: 'Tight wings' }], S);
  assert.deepEqual(named.map(r => r.name), ['Wide wings', 'Tight wings']);
});

test('a group’s name travels in the link', () => {
  const items = [{ ids: ['b', 'a'], name: 'Wide wings, v2' }, { ids: ['c'] }];
  assert.equal(encode(items), 'a.b~Wide%20wings%2C%20v2,c');
  assert.deepEqual(decode(encode(items)), [{ ids: ['a', 'b'], name: 'Wide wings, v2' }, { ids: ['c'] }]);
});
