import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { deploymentsOf, selected, presets, presetOf, toggleOff, deploymentRows, settingsSpread } from '../../static/linck/js/data/strategy.js';

const dep = (id, strategy, rawStatus = 'active', config = {}) => ({ id, name: `D${id}`, strategy, rawStatus, cap: 100000, trades: [], daily: [], raw: { config } });
const A = [dep('a', 's'), dep('b', 's', 'paused'), dep('c', 's', 'stopped'), dep('x', 'other')];

test('a strategy’s deployments, all in view until some are left out', () => {
  const D = deploymentsOf(A, 's');
  assert.deepEqual(D.map(v => v.id), ['a', 'b', 'c']);
  assert.deepEqual(selected(D, []).map(v => v.id), ['a', 'b', 'c'], 'everything by default');
  assert.deepEqual(selected(D, ['b']).map(v => v.id), ['a', 'c']);
  assert.deepEqual(selected(D, ['gone']).map(v => v.id), ['a', 'b', 'c'], 'an unknown id changes nothing');
});

test('toggling leaves one out or brings it back, but never empties the view', () => {
  const D = deploymentsOf(A, 's');
  assert.deepEqual(toggleOff(D, [], 'b'), ['b']);
  assert.deepEqual(toggleOff(D, ['b'], 'b'), []);
  assert.deepEqual(toggleOff(D, ['a', 'b'], 'c'), ['a', 'b'], 'the last one stays in');
});

test('quick picks: All, Running, Not stopped (only when they differ)', () => {
  const D = deploymentsOf(A, 's');
  assert.deepEqual(presets(D).map(p => [p[0], p[2]]), [['all', []], ['active', ['b', 'c']], ['live', ['c']]]);
  assert.equal(presetOf(D, ['c']), 'live');
  assert.equal(presetOf(D, ['c', 'b']), 'active', 'order doesn’t matter');
  assert.equal(presetOf(D, ['a']), null, 'a hand-made pick is none of them');
  const allRunning = [dep('a', 's'), dep('b', 's')];
  assert.deepEqual(presets(allRunning).map(p => p[0]), ['all'], 'no Running pick when it equals All');
});

test('each deployment’s figures for the table', () => {
  const v = { ...dep('a', 's'), trades: [{ pnl: 100, closed_at: '2026-10-01T10:00:00Z' }, { pnl: -40, closed_at: '2026-10-02T10:00:00Z' }],
    daily: [{ day: '2026-10-01', pnl: 100 }, { day: '2026-10-02', pnl: -40 }] };
  const [r] = deploymentRows([v], t => t.pnl, () => ({ overall: 60, ret: 0.06, today: 0 }));
  assert.equal(r.m.n, 2);
  assert.equal(r.m.win, 50);
  assert.equal(r.x.overall, 60);
});

test('settings: the default, what each deployment uses, and which differ', () => {
  const fields = [
    { key: 'lots', label: 'Lots', help: '', default: 1, suffix: 'lots' },
    { key: 'entry_time', label: 'Entry time', help: '', default: '09:20' },
    { key: 'tokens', label: 'Token', help: '', default: [1] },
  ];
  const D = [dep('a', 's', 'active', { lots: 2, tokens: [1] }), dep('b', 's', 'active', { lots: 1 }), dep('c', 's', 'active', { lots: 2, entry_time: '09:20' })];
  const S = Object.fromEntries(settingsSpread(fields, D).map(f => [f.key, f]));
  assert.equal(S.lots.def, '1');
  assert.deepEqual(S.lots.values.map(x => [x.v, x.n]), [['2', 2], ['1', 1]], 'most used first');
  assert.deepEqual(S.lots.values[0].names, ['Da', 'Dc']);
  assert.ok(S.lots.differs && S.lots.mixed);
  assert.ok(!S.entry_time.differs && !S.entry_time.mixed, 'a missing key means the default');
  assert.equal(S.tokens.def, '[1]', 'lists shown as JSON');
  assert.ok(!S.tokens.differs);
  assert.deepEqual(settingsSpread(fields, []).map(f => f.values.length), [0, 0, 0], 'no deployments: defaults only');
});
