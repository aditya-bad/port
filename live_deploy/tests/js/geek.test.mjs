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
  assert.equal(hasOwn({ strategy: 'trend_credit_spread' }), false);
  assert.equal(hasOwn({ strategy: 'intraday_dtt_simple' }), true);
});

test('the view menu: shared views for all, the strangle adds its own between Live and Settings', async () => {
  const { viewsFor } = await import('../../static/linck/js/pages/geek/views.js');
  const keys = s => viewsFor(s).map(v => v[0]);
  const shared = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'conditions', 'settings'];
  assert.deepEqual(keys('trend_credit_spread'), shared);
  assert.deepEqual(keys('no_such_strategy'), shared);
  const st = keys('strangle_monthly_v2');
  assert.deepEqual(st.filter(k => shared.includes(k)), shared);
  for (const k of ['cycles', 'cycle', 'adjustments', 'premium', 'entries', 'map']) assert.ok(st.includes(k), k);
  assert.equal(st[st.length - 1], 'settings');
  for (const s of ['trend_credit_spread', 'strangle_monthly_v2', 'intraday_dtt_simple', 'pivot_supertrend_options']) {
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

test('SuperTrend options: its own views, with the one-trade page under Signals', async () => {
  const { viewsFor, hasView } = await import('../../static/linck/js/pages/geek/views.js');
  const vs = viewsFor('pivot_supertrend_options');
  assert.deepEqual(vs.map(v => v[0]), ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'signals', 'trade', 'exits', 'days', 'conditions', 'settings']);
  assert.equal(vs.find(v => v[0] === 'trade')[6], 'signals', 'hidden from the menu, Signals stays lit');
  assert.ok(hasView('pivot_supertrend_options', 'signals') && !hasView('pivot_supertrend_options', 'cycles'));
  assert.ok(!hasView('pivot_supertrend_options_inverse', 'signals'), 'the inverse one has the shared views only');
  assert.equal(viewsFor('strangle_monthly_v2').find(v => v[0] === 'cycle')[6], 'cycles');
  const ks = vs.map(v => v[3]).filter(Boolean);
  assert.equal(new Set(ks.map(k => k.toLowerCase())).size, ks.length);
});

test('SuperTrend ladder and formats', async () => {
  const { ladderScale, spread, mins, pts, exitLabel, ladder } = await import('../../static/linck/js/pages/geek/st.js');
  const levels = ['S3', 'S2', 'S1', 'P', 'R1', 'R2', 'R3'].map((key, i) => ({ key, value: 24700 + i * 100 }));
  const x = ladderScale(levels, [25050, 24950]);
  assert.ok(x(24700) > 0 && x(25300) < 1, 'padded both ends');
  assert.ok(x(24700) < x(25000) && x(25000) < x(25300));
  const wide = ladderScale(levels, [26000]);
  assert.ok(wide(26000) < 1 && wide(25300) < wide(26000), 'a close off the ladder still fits');
  assert.deepEqual(spread([100, 105, 50], 12), [100, 112, 50], 'labels pushed apart, order kept');
  assert.deepEqual(spread([10, 10, 10], 12), [10, 22, 34]);
  assert.equal(mins(42.4), '42m');
  assert.equal(mins(59.6), '1h 00m', 'never "60m"');
  assert.equal(mins(133), '2h 13m');
  assert.equal(mins(null), '—');
  assert.equal(pts(21.4), '+21');
  assert.equal(pts(-1500), '−1,500');
  assert.equal(pts(0), '0');
  assert.equal(exitLabel('st_flip'), 'SuperTrend flip');
  assert.equal(exitLabel('force_exit'), 'Square-off');
  assert.equal(exitLabel(null), 'Open');
  const s = String(ladder({ ladder: levels, close: 25050, line: 24950, trigger: { key: 'R1', value: 25100 }, trend: 'up' }));
  assert.equal((s.match(/>(S3|S2|S1|P|R1|R2|R3)</g) || []).length, 7, 'every level labelled');
  assert.match(s, /index 25,050/);
  assert.match(s, /ST 24,950/);
  assert.equal(String(ladder({ ladder: [], close: 1, line: 1 })), '', 'nothing to draw without levels');
});

test('DTT straddle: its own views, the one-straddle page under Straddles', async () => {
  const { viewsFor } = await import('../../static/linck/js/pages/geek/views.js');
  const vs = viewsFor('intraday_dtt_simple');
  assert.deepEqual(vs.map(v => v[0]), ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'straddles', 'straddle', 'timing', 'conditions', 'settings']);
  assert.equal(vs.find(v => v[0] === 'straddle')[6], 'straddles');
  for (const st of ['intraday_dtt_adjusted', 'intraday_dtt_advanced']) {
    const vs = viewsFor(st);
    assert.deepEqual(vs.map(v => v[0]), ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'sessions', 'session', 'moves', 'conditions', 'settings'], st);
    assert.equal(vs.find(v => v[0] === 'session')[6], 'sessions', 'one day sits under Sessions');
  }
});

test('DTT straddle: progress toward a line, names and the straddle panel', async () => {
  const { progress, exitName, ddPanel } = await import('../../static/linck/js/pages/geek/dtt.js');
  assert.equal(progress(100, 140, 120), 0.5, 'a leg halfway to its stop');
  assert.equal(progress(200, 180, 190), 0.5, 'the combined premium halfway down to its target');
  assert.equal(progress(100, 140, 90), 0, 'below its entry: nowhere near');
  assert.equal(progress(100, 140, 150), 1);
  assert.equal(progress(100, 140, null), 0);
  assert.equal(exitName('profit_target_decay'), 'Profit target');
  assert.equal(exitName('leg_spike_stop'), 'Leg stop');
  assert.equal(exitName(null), 'Open');
  const open = String(ddPanel({ entry_time: '10:00', force_exit: '15:00', profit_pct: 0.1, stop_pct: 0.4, entered_today: true, state: {
    legs: { CE: { symbol: 'X26OCT25000CE', entry: 100, now: 120, stop: 140, to_stop: 20 }, PE: { symbol: 'X26OCT25000PE', entry: 100, now: 80, stop: 140, to_stop: 60 } },
    combined: { entry: 200, now: 200, target: 180, to_target: 20, decay_pct: 0 } } }));
  assert.match(open, /stop 140\.00/);
  assert.match(open, /target 180\.00/);
  assert.match(open, /width:50\.0%/, 'the CE is halfway to its stop');
  const flat = String(ddPanel({ entry_time: '10:00', force_exit: '15:00', profit_pct: 0.1, stop_pct: 0.4, entered_today: false, state: null, last: null }));
  assert.match(flat, /Sells the ATM CE and PE at 10:00/);
  assert.match(flat, /10% decay/);
});

test('chart hover: the nearest point to the pointer, every series in the tip', async () => {
  const { nearest, scrubber, tipAt, hit } = await import('../../static/linck/js/pages/geek/ui.js');
  const pts = [[10, 50, 'a'], [20, 40, 'b'], [40, 30, 'c']];
  assert.equal(nearest(pts, 0)[2], 'a');
  assert.equal(nearest(pts, 16)[2], 'b');
  assert.equal(nearest(pts, 31)[2], 'c');
  assert.equal(nearest(pts, 99)[2], 'c');
  assert.equal(nearest([], 5), null);
  const f = scrubber(100, 200, {
    series: [{ name: 'CE', color: 'red', pts: [[10, 100, '120.0', '10:00'], [90, 50, '130.0', '10:05']] },
      { name: 'PE', color: 'teal', pts: [[10, 150, '80.0'], [90, 20, '75.0']] },
      { name: 'Far', color: 'x', reach: 5, pts: [[50, 10, 'n/a']] }],
    extra: () => ['Stop  168.0'],
  });
  const r = f(0.85);
  assert.equal(r.t, '10:05\nCE  130.0\nPE  75.0\nStop  168.0', 'title from the nearest point, then each series');
  assert.deepEqual(r.ys, [0.25, 0.1, null], 'dots where each series is; a series out of reach has none');
  assert.deepEqual(r.cs, ['red', 'teal', 'x']);
  assert.equal(scrubber(100, 100, { series: [{ name: 'x', color: 'c', pts: [] }] })(0.5), null);
  assert.equal(tipAt('A "b" <c>\nX  1'), ' data-tip="A &quot;b&quot; &lt;c&gt;\nX  1"', 'escaped for an SVG string');
  assert.match(hit(1, 2, 0, 4, 'tip'), /class="hit" x="1" y="2" width="1" height="4" fill="transparent" data-tip="tip"/, 'never zero-wide');
});

test('conditions: every dot of the excursion charts carries its trade, and nothing is drawn without a result', async () => {
  const { excursionChart, conditionTip, bucketBars } = await import('../../static/linck/js/pages/geek/conditions.js');
  const row = (pnl, mae, mfe, extra = {}) => ({ t: '2026-10-19T10:20:00+05:30', label: 'Trend flip up', pnl, mae, mfe, held_min: 60, symbols: ['NIFTY26OCT24900PE'],
    context: { gap_pct: -0.8, from_open_pct: 0.44, dte: 1 }, atr: 41.5, ...extra });
  const rows = [row(750, -2250, 2250), row(-400, -900, 300), row(null, -100, 50)];
  const mae = String(excursionChart(rows, 'mae'));
  assert.equal((mae.match(/<circle/g) || []).length, 2, 'open trades have no result to plot');
  assert.equal((mae.match(/data-tip=/g) || []).length, 2, 'every dot has a tip');
  assert.match(String(excursionChart(rows, 'mfe', { diagonal: true })), /stroke-dasharray/, 'the ended-at-its-best line');
  assert.equal(excursionChart([row(null, null, null)], 'mae'), '');
  const tip = conditionTip(rows[0]);
  for (const s of ['Trend flip up', 'Result  +₹750', 'Worst on the way (MAE)  −₹2,250', 'Best on the way (MFE)  +₹2,250', 'Held  1h 00m', 'Gap  -0.80%', 'Index since the open  +0.44%', 'Days to expiry  1', 'ATR  41.5', 'NIFTY26OCT24900PE']) {
    assert.ok(tip.includes(s), `${s} in:\n${tip}`);
  }
  assert.ok(conditionTip(row(null, null, null)).includes('Result  open'));
  const bare = row(-750, -750, 0, { path: false });
  assert.ok(conditionTip(bare).includes('No prices recorded on the way') && !conditionTip(bare).includes('MAE'));
  assert.equal(excursionChart([bare], 'mae'), '', 'a trade without prices on the way sits out the MAE chart');
  const bars = String(bucketBars({ label: 'Today’s gap', buckets: [{ label: 'Gap down > 0.5%', n: 2, wins: 1, win_rate: 50, pnl: 300, avg: 150 }, { label: 'Up 0–0.5%', n: 1, wins: 0, win_rate: 0, pnl: -600, avg: -600 }] }));
  assert.equal((bars.match(/data-tip=/g) || []).length, 2);
  assert.match(bars, /width:50\.0%/, 'the biggest average fills its half');
  assert.match(bars, /width:12\.5%/);
});
