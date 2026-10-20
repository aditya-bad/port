import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parse } from '../../static/linck/js/core/router.js';
import { supports, hasOwn, GEEK } from '../../static/linck/js/data/geek.js';

test('geek routes win over deployment tabs', () => {
  const g = parse('#/d/abc/geek/cycles');
  assert.equal(g.route.path, 'd/:id/geek/:view?');
  assert.equal(g.route.geek, true, 'the shell hides the app chrome for it');
  assert.deepEqual(g.params, { id: 'abc', view: 'cycles' });
  assert.deepEqual(parse('#/d/abc/geek').params, { id: 'abc' }, 'view is optional (Overview)');
  assert.equal(parse('#/d/abc/now').route.path, 'd/:id/:tab?');
  assert.ok(!parse('#/d/abc/now').route.geek);
});

test('every deployment offers Geek mode; some strategies have views of their own', () => {
  assert.ok(GEEK.has('strangle_monthly_v2'));
  assert.equal(supports({ strategy: 'strangle_monthly_v2' }), true);
  assert.equal(supports({ strategy: 'intraday_dtt_simple' }), true);
  assert.equal(supports({ strategy: 'pivot_supertrend_options' }), true);
  assert.equal(supports(null), false);
  assert.equal(hasOwn({ strategy: 'strangle_monthly_v2' }), true);
  assert.equal(hasOwn({ strategy: 'intraday_dtt_simple' }), false);
});

test('the view menu: shared views for all, the strangle adds its own between Live and Settings', async () => {
  const { viewsFor } = await import('../../static/linck/js/pages/geek/views.js');
  const keys = s => viewsFor(s).map(v => v[0]);
  const shared = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'settings'];
  assert.deepEqual(keys('intraday_dtt_simple'), shared);
  assert.deepEqual(keys('no_such_strategy'), shared);
  const st = keys('strangle_monthly_v2');
  assert.deepEqual(st.filter(k => shared.includes(k)), shared);
  for (const k of ['cycles', 'cycle', 'adjustments', 'premium', 'entries', 'map']) assert.ok(st.includes(k), k);
  assert.equal(st[st.length - 1], 'settings');
  for (const s of ['intraday_dtt_simple', 'strangle_monthly_v2']) {
    const ks = viewsFor(s).map(v => v[3]).filter(Boolean);
    assert.equal(new Set(ks.map(k => k.toLowerCase())).size, ks.length, `${s}: shortcut keys are unique`);
  }
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

test('a P&L line breaks where prices are missing, and the breaks come back dotted', async () => {
  const { gapped, steps } = await import('../../static/linck/js/pages/geek/ui.js');
  const pts = [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]];
  assert.deepEqual(gapped(pts), { solid: 'M0.0 0.0L1.0 1.0L2.0 2.0L3.0 3.0L4.0 4.0', gaps: '' });
  assert.deepEqual(gapped(pts, [2]), { solid: 'M0.0 0.0L1.0 1.0M2.0 2.0L3.0 3.0L4.0 4.0', gaps: 'M1.0 1.0L2.0 2.0' });
  // a lone point between two gaps is only reached by the dotted lines
  assert.deepEqual(gapped(pts, [2, 3]), { solid: 'M0.0 0.0L1.0 1.0M3.0 3.0L4.0 4.0', gaps: 'M1.0 1.0L2.0 2.0M2.0 2.0L3.0 3.0' });
  assert.deepEqual(steps([[0, 0], [2, 5], [3, 1]]), [[0, 0], [2, 0], [2, 5], [3, 5], [3, 1]]);
});

test('spotlight filters pick which past cycles are drawn', async () => {
  const { spotPast, spotText } = await import('../../static/linck/js/pages/geek/cycles.js');
  const p = [[0, 0], [10, 5]];
  const rows = [
    { cycle_id: 8, status: 'open', ended_by: 'open', path: p },
    ...[7, 6, 5, 4, 3, 2].map(id => ({ cycle_id: id, status: 'closed', ended_by: id === 5 ? 'convergence_stop' : id === 3 ? 'contract_expiry_backstop' : 'checkpoint_target', path: p, realized_pnl: 100, rolls: 1 })),
    { cycle_id: 1, status: 'closed', ended_by: 'checkpoint_target', path: [[0, 0]] },   // no journey
  ];
  assert.deepEqual(spotPast(rows, 'all').map(r => r.cycle_id), [7, 6, 5, 4, 3, 2], 'closed with a path; the running one is drawn separately');
  assert.deepEqual(spotPast(rows, 'last5').map(r => r.cycle_id), [7, 6, 5, 4, 3]);
  assert.deepEqual(spotPast(rows, 'checkpoint').map(r => r.cycle_id), [7, 6, 4, 2]);
  assert.deepEqual(spotPast(rows, 'stopped').map(r => r.cycle_id), [5, 3]);
  assert.equal(spotText({ cycle_id: 5, ended_by: 'convergence_stop', realized_pnl: -3852, path: [[0, 0], [218.4, -3852]], rolls: 2 }),
    '#5 · convergence stop · −₹3,852 after 9.1 days · 2 rolls · click to open');
});
