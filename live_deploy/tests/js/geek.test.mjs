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
