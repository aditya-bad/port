import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parse } from '../../static/linck/js/core/router.js';
import { supports, GEEK } from '../../static/linck/js/data/geek.js';

test('geek routes win over deployment tabs', () => {
  const g = parse('#/d/abc/geek/cycles');
  assert.equal(g.route.path, 'd/:id/geek/:view?');
  assert.equal(g.route.geek, true, 'the shell hides the app chrome for it');
  assert.deepEqual(g.params, { id: 'abc', view: 'cycles' });
  assert.deepEqual(parse('#/d/abc/geek').params, { id: 'abc' }, 'view is optional (Overview)');
  assert.equal(parse('#/d/abc/now').route.path, 'd/:id/:tab?');
  assert.ok(!parse('#/d/abc/now').route.geek);
});

test('only strategies with a Geek module offer it', () => {
  assert.ok(GEEK.has('strangle_monthly_v2'));
  assert.equal(supports({ strategy: 'strangle_monthly_v2' }), true);
  assert.equal(supports({ strategy: 'intraday_dtt_simple' }), false);
  assert.equal(supports(null), false);
});

test('checkpoint chip: a percentage only while in profit, "In loss" (red) while behind', async () => {
  const { stateChip } = await import('../../static/linck/js/pages/geek/ui.js');
  const s = r => String(stateChip({ key: 'checkpoint_target', ...r }));
  assert.match(s({ state: 'ok', pct: 40 }), /class="st-chip ok">40% there/);
  assert.match(s({ state: 'behind', pct: null }), /class="st-chip behind">In loss/);
  assert.doesNotMatch(s({ state: 'behind', pct: null }), /%/);
});

test('chart y labels skip values that would print on top of each other', async () => {
  const { yLabels } = await import('../../static/linck/js/pages/geek/ui.js');
  const y = v => 200 - v / 10;                     // 10 ₹ per px
  const s = yLabels([0, 100, 5000], y, 40);
  assert.equal((s.match(/<text/g) || []).length, 2, '₹100 sits 10px from ₹0: dropped');
  assert.match(s, /₹0/);
  assert.match(s, /₹5,000/);
});
