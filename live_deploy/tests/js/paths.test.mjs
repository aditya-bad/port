import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { atHour, smooth, quantile, band, place, dayTable, hoursOf } from '../../static/linck/js/pages/geek/paths.js';

const cyc = (id, path, extra = {}) => ({ cycle_id: id, status: 'closed', ended_by: 'checkpoint_target', path, ...extra });

test('a cycle’s P&L at an hour is its last point then, and nothing once it ended', () => {
  const r = cyc(1, [[0, 0], [5, 100], [30, 250]]);
  assert.equal(hoursOf(r), 30);
  assert.equal(atHour(r, 0), 0);
  assert.equal(atHour(r, 4.9), 0);
  assert.equal(atHour(r, 5), 100);
  assert.equal(atHour(r, 29), 100);
  assert.equal(atHour(r, 30), 250);
  assert.equal(atHour(r, 31), null, 'ended: not counted later');
  assert.equal(atHour(cyc(2, [[0, 0]]), 0), null, 'no path');
});

test('smoothing keeps one point per bucket, and both ends', () => {
  const p = [[0, 0], [0.5, 5], [1.9, 9], [2.1, 3], [3.9, 7], [4.2, 1]];
  assert.deepEqual(smooth(p, 2), [[0, 0], [1.9, 9], [3.9, 7], [4.2, 1]]);
  assert.deepEqual(smooth([[0.4, 1], [0.6, 2]], 2), [[0.4, 1], [0.6, 2]], 'the first point stays');
  assert.deepEqual(smooth([]), []);
});

test('quantiles interpolate between neighbours', () => {
  assert.equal(quantile([1, 2, 3, 4], 0.5), 2.5);
  assert.equal(quantile([10], 0.25), 10);
  assert.equal(quantile([0, 100], 0.25), 25);
  assert.equal(quantile([], 0.5), null);
});

test('the band covers past cycles only, and stops where fewer than 3 were open', () => {
  const rows = [
    cyc(1, [[0, 0], [24, 100], [48, 300]]),
    cyc(2, [[0, 0], [24, 200]]),
    cyc(3, [[0, 0], [24, -100], [72, 50]]),
    cyc(4, [[0, 0], [24, 400], [96, 900]]),
    { cycle_id: 5, status: 'open', ended_by: 'open', path: [[0, 0], [24, -5000]] },
  ];
  const B = band(rows, 24);
  assert.deepEqual(B.map(b => [b.h, b.n]), [[0, 4], [24, 4], [48, 3]], 'at 72h only 2 were open');
  assert.deepEqual([B[1].lo, B[1].med, B[1].hi], [-100, 150, 400], 'the running cycle isn’t part of it');
  assert.equal(B[1].q1, quantile([-100, 100, 200, 400], 0.25));
});

test('where a value sits among past cycles', () => {
  const s = { lo: -10, q1: 0, med: 5, q3: 10, hi: 20 };
  assert.equal(place(25, s), 'above every past cycle');
  assert.equal(place(15, s), 'top quarter');
  assert.equal(place(5, s), 'middle half');
  assert.equal(place(-5, s), 'bottom quarter');
  assert.equal(place(-20, s), 'below every past cycle');
  assert.equal(place(5, { ...s, med: null }), '', 'too few past cycles to say');
  assert.equal(place(null, s), '');
});

test('the day table: each day, past cycles against the running one, and a row for now', () => {
  const rows = [
    cyc(1, [[0, 0], [24, 100], [40, 750]]),                                       // checkpoint on day 2
    cyc(2, [[0, 0], [24, 200], [48, 300], [60, 800]]),                           // checkpoint on day 3
    cyc(3, [[0, 0], [24, -100], [72, -600]], { ended_by: 'convergence_stop' }),
    { cycle_id: 4, status: 'open', ended_by: 'open', path: [[0, 0], [24, -300], [36, 50]] },
  ];
  const T = dayTable(rows);
  assert.deepEqual(T.map(r => r.label), ['Day 1', 'Now · day 1.5', 'Day 2', 'Day 3']);
  const d1 = T[0];
  assert.deepEqual([d1.open, d1.closed, d1.med, d1.lo, d1.hi], [3, 3, 100, -100, 200]);
  assert.equal(d1.run, -300);
  assert.equal(d1.place, 'below every past cycle');
  const now = T[1];
  assert.equal(now.now, true);
  assert.equal(now.run, 50);
  assert.deepEqual([T[2].open, T[2].med, T[2].reached], [2, null, 1], 'day 2: one reached it, too few for a typical');
  assert.equal(T[2].run, null, 'the running cycle isn’t 2 days old yet');
  assert.equal(T[3].reached, 2);
});
