import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { eventAt, nextEvent, prevEvent, advance, change, xOf, dayTicks, KIND } from '../../static/linck/js/pages/geek/replay.js';

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

test('a gapless axis and a tick at each new day', () => {
  assert.equal(xOf(0, 10), 64);
  assert.equal(xOf(9, 10), 1186);
  assert.equal(xOf(0, 1), 64);
  const F = ['2026-10-14T15:28:00+05:30', '2026-10-14T15:29:00+05:30', '2026-10-15T09:15:00+05:30'].map(t => ({ t }));
  assert.deepEqual(dayTicks(F).map(d => d[0]), [0, 2]);
});

test('every kind the backend sends has a label and a colour', () => {
  for (const k of ['new', 'entry', 'adjustment', 'roll', 'hedge', 'added', 'leg_closed', 'exit']) assert.ok(KIND[k] && KIND[k][0] && KIND[k][1], k);
});
