import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../../static/linck/js/core/api.js';
import * as book from '../../static/linck/js/data/book.js';
import { RULES, healthAlertsOf, attention } from '../../static/linck/js/data/alerts.js';
import { switched, isOn, KEYS } from '../../static/linck/js/data/alert_types.js';

// A deployment as data/book.js shapes it, with the kinds it turned off.
const dep = (id, off = [], more = {}) => ({
  id, name: `dep ${id}`, raw: { alerts_off: off }, rawStatus: 'active', status: 'active', legs: [], streak: 0, isLab: false, ...more,
});

async function seed() {
  api.strategyHealth = async () => ({ deployments: {
    a: [{ rule: 'drawdown', key: 'drawdown:1', title: 'Deepest drawdown yet', body: 'b' }, { rule: 'win_rate', key: 'win_rate:1', title: 'Winning less often', body: 'w' }],
    b: [{ rule: 'drawdown', key: 'drawdown:2', title: 'Deepest drawdown yet', body: 'b' }],
  } });
  api.vixSpike = async () => ({ spiking: true, change_pct: 12, prev_close: 14, vix: 15.7, at: '2026-10-21T11:00', exposed: [{ id: 'a', name: 'dep a' }, { id: 'b', name: 'dep b' }] });
  api.shadowRejections = async () => ({ rejections: [{ deployment_id: 'a', name: 'dep a', rule: 'lot', day: '2026-10-21', n: 1, detail: 'x' }] });
  await book.load(['health', 'spike', 'shadow']);
}
const items = list => RULES.flatMap(r => r(list));

test('a kind turned off makes no item; the rest still do', async () => {
  await seed();
  const on = [dep('a', [], { rawStatus: 'paused', legs: [1], streak: 6 }), dep('b')];
  const all = items(on).map(x => x.key);
  for (const k of ['paused:a', 'health:a:drawdown:1', 'health:a:win_rate:1', 'health:b:drawdown:2', 'vixspike', 'shadow:a:lot:2026-10-21']) assert.ok(all.includes(k), k);

  const off = [dep('a', ['paused', 'drawdown', 'shadow'], { rawStatus: 'paused', legs: [1] }), dep('b', ['drawdown'])];
  const some = items(off).map(x => x.key);
  assert.deepEqual(some.filter(k => k.startsWith('health:')), ['health:a:win_rate:1'], 'only drawdown went');
  assert.ok(!some.includes('paused:a') && !some.some(k => k.startsWith('shadow:')));
  assert.ok(some.includes('vixspike'), 'VIX still on for both');
});

test('a streak turned off, and the VIX item when every exposed one turned it off', async () => {
  await seed();
  const streaky = dep('a', ['streak'], { streak: 9 });
  assert.ok(!items([streaky, dep('b')]).some(x => x.key === 'streak:a'));
  assert.ok(items([dep('a', [], { streak: 9 }), dep('b')]).some(x => x.key === 'streak:a'));
  const one = items([dep('a', ['vix']), dep('b')]).find(x => x.key === 'vixspike');
  assert.match(one.body, /^1 deployment hold sold premium that loses as VIX rises: dep b\./, 'the one that turned it off isn’t named');
  assert.ok(!items([dep('a', ['vix']), dep('b', ['vix'])]).some(x => x.key === 'vixspike'));
});

test('the critical ones can’t be turned off, and the Desk sorts by what still alerts', async () => {
  await seed();
  const broken = dep('a', KEYS, { status: 'error', issue: { text: 'boom' } });
  assert.ok(items([broken]).some(x => x.key === 'err:a'), 'an error alerts with every kind off');
  assert.deepEqual(healthAlertsOf(dep('a', ['drawdown'])).map(f => f.rule), ['win_rate']);
  assert.equal(attention(dep('b', ['drawdown'])), 0);
  assert.equal(attention(dep('b')), 2);
});

test('switching one kind keeps the others, in the catalogue’s order', () => {
  const v = dep('x', ['vix']);
  assert.deepEqual(switched(v, 'drawdown', false), ['drawdown', 'vix']);
  assert.deepEqual(switched(v, 'vix', true), []);
  assert.ok(isOn(dep('y'), 'trades') && !isOn(v, 'vix'));
  assert.ok(isOn({ raw: {} }, 'drawdown'), 'a row from before the column: all on');
});
