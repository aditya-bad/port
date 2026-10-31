import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { rows, curves, differences, styleOf, show } from '../../static/linck/js/data/variations.js';

const pnlOf = t => t.pnl;
const dep = (id, cfg, trades = [], extra = {}) => ({ id, name: id, cap: 100000, raw: { config: cfg }, trades, daily: [], variationLabel: extra.label || null, ...extra });
const tr = (day, pnl) => ({ closed_at: `2026-10-${day}T10:00:00+05:30`, pnl });

test('side by side: each one’s figures, the primary first', () => {
  const F = [dep('P', {}, [tr(20, 1000), tr(21, -400)]), dep('V', {}, [tr(20, 2500)], { label: 'wide' })];
  const R = rows(F, pnlOf, v => ({ overall: v.trades.reduce((a, t) => a + t.pnl, 0), cycle: null }));
  assert.deepEqual(R.map(r => [r.label, r.primary, r.overall, r.trades, Math.round(r.win)]), [['Primary', true, 600, 2, 50], ['wide', false, 2500, 1, 100]]);
  assert.equal(R[1].ret, 2.5);
});

test('their P&L on one time axis, carried flat where one didn’t trade', () => {
  const F = [dep('P', {}, [tr(20, 100), tr(22, 50)]), dep('V', {}, [tr(21, -30)])];
  const c = curves(F, pnlOf);
  assert.equal(c.days.length, 3);
  assert.deepEqual(c.series, [[100, 100, 150], [0, -30, -30]]);
});

test('only the settings a variation changed, including one it dropped', () => {
  const F = [dep('P', { a: 1, b: 'x', c: [1] }), dep('V1', { a: 2, b: 'x', c: [1] }), dep('V2', { a: 1, b: 'x' })];
  assert.deepEqual(differences(F), [{ key: 'a', primary: 1, values: [2, 1] }, { key: 'c', primary: [1], values: [[1], undefined] }]);
  assert.deepEqual(differences([F[0]]), []);
  // a key the primary leaves to the strategy's default shows that default
  assert.deepEqual(differences([dep('P', {}), dep('V', { k: 0.0075 })], { k: 0.005 }), [{ key: 'k', primary: 0.005, values: [0.0075] }]);
  assert.deepEqual(differences([dep('P', {}), dep('V', { k: 0.005 })], { k: 0.005 }), [], 'setting the default is no change');
  assert.equal(show(undefined), '—');
  assert.equal(show([1]), '[1]');
});

test('the primary in ink; past five hues a variation is dashed', () => {
  assert.deepEqual(styleOf(0), { color: 'var(--ink)', dash: '' });
  assert.equal(styleOf(5).dash, '6 4');
});

test('the book lists primaries, counting their variations; a variation is still found', async () => {
  const { api } = await import('../../static/linck/js/core/api.js');
  const book = await import('../../static/linck/js/data/book.js');
  const d = (id, of = null, label = null) => ({ id, deployment_name: id, strategy_name: 's', mode: 'positional', status: 'active', initial_capital: 100000,
    current_cash: 100000, config: {}, tags: [], created_at: '2026-10-01T00:00:00Z', variation_of: of, variation_label: label });
  api.deployments = async () => [d('P'), d('V1', 'P', 'a'), d('V2', 'P', 'b'), d('Q')];
  await book.load(['deployments']);
  assert.deepEqual(book.deployments().map(v => [v.id, v.variations]), [['P', 2], ['Q', 0]]);
  assert.equal(book.everything().length, 4);
  assert.equal(book.find('V2').variationLabel, 'b');
  assert.deepEqual(book.family('V1').map(v => v.id), ['P', 'V1', 'V2']);
  assert.deepEqual(book.family('Q').map(v => v.id), ['Q']);
});
