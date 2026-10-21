import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { eventAt, nextEvent, prevEvent, advance, change, windowOf, frameOf, legPnl, positionsAt, stage, KIND } from '../../static/linck/js/pages/geek/replay.js';

const EV = [{ frame: 0, kind: 'new' }, { frame: 5, kind: 'adjustment' }, { frame: 9, kind: 'exit' }];

test('the event shown is the last one at or before the playhead', () => {
  assert.equal(eventAt(EV, 0).kind, 'new');
  assert.equal(eventAt(EV, 4).kind, 'new');
  assert.equal(eventAt(EV, 5).kind, 'adjustment');
  assert.equal(eventAt(EV, 99).kind, 'exit');
  assert.equal(eventAt([{ frame: 3 }], 1), null);
});

test('next and previous event jump over the playhead', () => {
  assert.equal(nextEvent(EV, 0).frame, 5);
  assert.equal(nextEvent(EV, 5).frame, 9);
  assert.equal(nextEvent(EV, 9), null);
  assert.equal(prevEvent(EV, 9).frame, 5);
  assert.equal(prevEvent(EV, 5).frame, 0);
  assert.equal(prevEvent(EV, 0), null);
});

test('playing steps one frame, stopping at each event when asked and at the end', () => {
  assert.deepEqual(advance(3, 12, EV, true), { i: 4, stop: false, event: null });
  const s = advance(4, 12, EV, true);
  assert.equal(s.i, 5); assert.equal(s.stop, true); assert.equal(s.event.kind, 'adjustment');
  assert.deepEqual(advance(4, 12, EV, false), { i: 5, stop: false, event: null });
  assert.deepEqual(advance(10, 12, EV, false), { i: 11, stop: true, event: null });
  assert.deepEqual(advance(11, 12, EV, false), { i: 11, stop: true, event: null });
});

test('changes between frames, unknown when either side is', () => {
  const F = [{ idx: 100, vix: 13 }, { idx: 120, vix: null }, { idx: 90, vix: 14 }];
  assert.equal(change(F, 0, 2, 'idx'), -10);
  assert.equal(change(F, 0, 1, 'vix'), null);
  assert.equal(change(F, 0, 9, 'idx'), null);
});

test('the screen shows the last bars up to the playhead', () => {
  assert.deepEqual(windowOf(5, 300, 120), [0, 5]);
  assert.deepEqual(windowOf(250, 300, 120), [131, 250]);
  assert.deepEqual(windowOf(250, 300, 0), [0, 299]);
});

// three 5-minute bars; leg 0 open from the first, leg 1 opened in the second
// and closed in the third
const T = m => `2026-10-14T10:${String(m).padStart(2, '0')}:00+05:30`;
const D = {
  index_name: 'NIFTY 50',
  frames: [
    { t: T(0), idx: 100, o: 99, h: 101, l: 98, vix: 13, pnl: 50, booked: 0, legs: [[0, 95, 50, false]] },
    { t: T(5), idx: 102, o: 100, h: 103, l: 100, vix: 13.2, pnl: 30, booked: 0, legs: [[0, 97, 20, false], [1, 52, 10, false]] },
    { t: T(10), idx: 101, o: 102, h: 102, l: 100, vix: 13.1, pnl: 60, booked: 25, legs: [[0, 94, 35, false]] },
  ],
  legs: [
    { symbol: 'X CE', kind: 'CE', strike: 105, side: 'short', qty: 10, entry: 100, t0: T(0), t1: null, realized: null, exit: null, opened_by: { kind: 'new', label: 'New cycle', rule: 'Entry' } },
    { symbol: 'X PE', kind: 'PE', strike: 95, side: 'short', qty: 10, entry: 53, t0: '2026-10-14T10:06:30+05:30', t1: '2026-10-14T10:12:00+05:30', realized: 25, exit: 50.5, opened_by: { kind: 'adjustment', label: 'Adjustment', rule: 'Continuous' }, closed_by: { kind: 'exit', label: 'Exit', rule: 'Stop' } },
  ],
  events: [{ frame: 0, kind: 'new', t: T(0), rule: 'Entry' }, { frame: 1, kind: 'adjustment', t: '2026-10-14T10:06:30+05:30', rule: 'Continuous' }],
};

test('a time falls in the bar that started at or before it', () => {
  assert.equal(frameOf(D.frames, T(0)), 0);
  assert.equal(frameOf(D.frames, '2026-10-14T10:06:30+05:30'), 1);
  assert.equal(frameOf(D.frames, T(30)), 2);
});

test('each leg’s P&L: nothing before it opened, its open P&L, then what it booked', () => {
  assert.deepEqual([0, 1, 2].map(i => legPnl(D, 1, i)), [null, 10, 25]);
  assert.deepEqual([0, 1, 2].map(i => legPnl(D, 0, i)), [50, 20, 35]);
});

test('positions appear as they open, then show how they closed', () => {
  assert.deepEqual(positionsAt(D, 0).map(p => p.l.symbol), ['X CE']);
  const two = positionsAt(D, 1);
  assert.deepEqual(two.map(p => [p.l.symbol, p.fresh, p.closed, p.now, p.pnl]), [['X CE', false, false, 97, 20], ['X PE', true, false, 52, 10]]);
  const three = positionsAt(D, 2);
  assert.deepEqual(three.map(p => [p.closed, p.justClosed, p.now, p.pnl]), [[false, false, 94, 35], [true, true, 50.5, 25]]);
});

test('the stage draws only up to the playhead: candles, strikes with their distance, flags, a line per leg', () => {
  const s1 = String(stage(D, 1, 120, true));
  assert.equal((s1.match(/<rect x="[\d.-]+" y="[\d.-]+" width="[\d.]+" height="[\d.]+" fill="var\(--(paper|r)\)"/g) || []).length, 2, 'two candles so far');
  assert.match(s1, /CE 105 sold · \+3 pts/);
  assert.match(s1, />NEW</); assert.match(s1, />ADJ</);
  assert.match(s1, /data-scrub=/, 'hover when paused');
  assert.doesNotMatch(String(stage(D, 1, 120, false)), /data-scrub=/, 'no hover while playing');
  const s0 = String(stage(D, 0, 120, true));
  assert.doesNotMatch(s0, />ADJ</, 'a later event isn’t shown yet');
  assert.doesNotMatch(s0, /PE 95/);
});

test('every kind the backend sends has a label and a colour', () => {
  for (const k of ['new', 'entry', 'adjustment', 'roll', 'hedge', 'added', 'leg_closed', 'exit']) assert.ok(KIND[k] && KIND[k][0] && KIND[k][1], k);
});
