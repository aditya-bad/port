import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import * as M from '../../static/linck/js/data/metrics.js';

test('trade metrics: win rate, profit factor, streak', () => {
  const m = M.tradeMetrics([100, -50, 200, -25, -25]);
  assert.equal(m.n, 5);
  assert.equal(m.pnl, 200);
  assert.equal(m.win, 40);
  assert.equal(m.pf, 3);
  assert.equal(m.aw, 150);
  assert.equal(m.al, -100 / 3);
  assert.equal(m.best, 200); assert.equal(m.worst, -50);
  assert.equal(m.streak, 2, 'two losses running at the end');
  assert.equal(M.tradeMetrics([10]).pf, 99, 'no losses');
  assert.deepEqual(M.tradeMetrics([]), { n: 0, pnl: 0, win: 0, pf: 0, aw: 0, al: 0, best: 0, worst: 0, streak: 0 });
});

test('drawdown from day P&Ls', () => {
  const d = M.drawdown([100, -30, -50, 200, -10]);
  assert.deepEqual(d.cumul, [100, 70, 20, 220, 210]);
  assert.deepEqual(d.dd, [0, -30, -80, 0, -10]);
  assert.equal(d.worst, -80);
  assert.deepEqual(M.ddSeries([5, 3, 8, 1]), [0, -2, 0, -7]);
});

test('verdicts', () => {
  assert.equal(M.verdictRdd(4), 'excellent'); assert.equal(M.verdictRdd(2), 'good');
  assert.equal(M.verdictRdd(1), 'fair'); assert.equal(M.verdictRdd(0.5), 'weak');
  assert.equal(M.verdictRdd(-1), 'losing'); assert.equal(M.verdictRdd(NaN), '');
  assert.equal(M.verdictPf(2.5), 'strong edge'); assert.equal(M.verdictPf(1.5), 'healthy edge');
  assert.equal(M.verdictPf(1.1), 'thin edge'); assert.equal(M.verdictPf(0.8), 'losing');
  assert.equal(M.fmtRatio(1.234), '1.23'); assert.equal(M.fmtRatio(99), '—'); assert.equal(M.fmtRatio(NaN), '—');
});

test('weekdays and daily series fill gaps with zero', () => {
  const w = M.weekdays(new Date('2026-10-16T10:00:00+05:30'), new Date('2026-10-20T10:00:00+05:30'));
  assert.deepEqual(w, ['2026-10-16', '2026-10-19', '2026-10-20'], 'no weekend');
  const rows = [{ day: '2026-10-16', pnl: 100 }, { day: '2026-10-20', pnl: -40 }, { day: '2026-10-20', pnl: 10 }];
  const s = M.dailySeries(rows, r => r.pnl, null, new Date('2026-10-20T12:00:00+05:30'));
  assert.deepEqual(s.map(x => [x.key, x.day, x.has, x.cum]),
    [['2026-10-16', 100, true, 100], ['2026-10-19', 0, false, 100], ['2026-10-20', -30, true, 70]]);
});

test('period metrics: return, drawdown %, return/DD, consistency', () => {
  const trades = [{ closed_at: '2026-09-10T10:00:00Z', pnl: 5000 }, { closed_at: '2026-10-10T10:00:00Z', pnl: -2000 }];
  const daily = [{ day: '2026-09-10', pnl: 5000 }, { day: '2026-10-10', pnl: -2000 }];
  const m = M.periodMetrics({ trades, daily, cap: 100000, pnlOf: r => r.pnl });
  assert.equal(m.pnl, 3000); assert.equal(m.ret, 3); assert.equal(m.dd, -2000); assert.equal(m.ddp, -2);
  assert.equal(m.rdd, 1.5); assert.equal(m.cons, 50); assert.equal(m.months, 2); assert.equal(m.n, 2);
});

test('correlation needs enough shared days', () => {
  const a = {}, b = {}, c = {};
  for (let i = 1; i <= 10; i++) { a[`d${i}`] = i; b[`d${i}`] = 2 * i; c[`d${i}`] = -i; }
  assert.ok(Math.abs(M.correlation(a, b) - 1) < 1e-9);
  assert.ok(Math.abs(M.correlation(a, c) + 1) < 1e-9);
  assert.equal(M.correlation({ x: 1 }, { x: 2 }), null);
});

test('analytics builds the full performance stack without throwing', () => {
  const days = M.dailySeries([{ day: '2026-09-01', pnl: 1000 }, { day: '2026-09-02', pnl: -500 }, { day: '2026-10-01', pnl: 700 }],
    r => r.pnl, null, new Date('2026-10-02T12:00:00+05:30'));
  const trades = [{ date: new Date('2026-09-01T10:00:00Z'), pnl: 1000, reason: 'target' },
                  { date: new Date('2026-09-02T10:00:00Z'), pnl: -500, reason: 'stop' },
                  { date: new Date('2026-10-01T10:00:00Z'), pnl: 700, reason: 'target' }];
  const a = M.analytics(days, trades, { cap: 100000 });
  for (const k of ['yRows', 'weeks', 'hist', 'roll', 'keyNums', 'insights', 'legend']) assert.ok(k in a, k);
  assert.ok(a.histSub.startsWith('3 trades'));
  const empty = M.analytics([], [], { cap: 100000 });
  assert.ok(Array.isArray(empty.insights));
});
