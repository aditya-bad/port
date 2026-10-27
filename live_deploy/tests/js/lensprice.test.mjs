import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { applyPrices } from '../../static/linck/js/data/lensprice.js';

// what the server sends for the made-up account (tests/e2e/lens_fixture.py)
const H = JSON.parse(readFileSync(new URL('../e2e/lens.fixture.json', import.meta.url), 'utf8')).holdings;

test('a tick reprices the share, its P&L, today, the totals and the weights', () => {
  const infy = H.equity.find(r => r.symbol === 'INFY');
  const out = applyPrices(H, { INFY: [1100, 1050] });
  const r = out.equity.find(x => x.symbol === 'INFY');
  assert.equal(r.ltp, 1100);
  assert.equal(r.value, 140 * 1100);
  assert.equal(r.pnl, Math.round((140 * 1100 - infy.invested) * 100) / 100);
  assert.equal(r.day_change, 140 * 50);
  assert.equal(r.day_change_pct, 4.76);
  const dv = 140 * 1100 - infy.value;
  assert.ok(Math.abs(out.totals.equity.value - (H.totals.equity.value + dv)) < 0.02);
  assert.ok(Math.abs(out.totals.all.value - (H.totals.all.value + dv)) < 0.02);
  assert.equal(out.totals.mf, H.totals.mf, 'funds stand at their NAV');
  const wsum = [...out.equity, ...out.mf].reduce((a, x) => a + x.weight, 0);
  assert.ok(Math.abs(wsum - 100) < 0.05, `weights still add to 100 (${wsum})`);
  assert.ok(Math.abs(out.allocation.reduce((a, x) => a + x.weight, 0) - 100) < 0.05);
  assert.equal(out.allocation.find(a => a.key === 'stock').label, 'Stocks', 'labels kept');
  assert.equal(H.equity.find(x => x.symbol === 'INFY').ltp, infy.ltp, 'the old answer is untouched');
});

test('today’s count of shares up and down follows the ticks', () => {
  const before = H.totals.equity;
  const ups = H.equity.filter(r => r.day_change > 0).map(r => r.symbol);
  const p = {};
  for (const s of ups) { const r = H.equity.find(x => x.symbol === s); p[s] = [r.prev_close * 0.99, r.prev_close]; }
  const out = applyPrices(H, p);
  assert.equal(out.totals.equity.advancing, 0);
  assert.equal(out.totals.equity.declining, before.declining + ups.length);
  assert.ok(out.totals.equity.day_change < before.day_change);
});

test('nothing new: the same object back; an unknown symbol is ignored', () => {
  const infy = H.equity.find(r => r.symbol === 'INFY');
  assert.equal(applyPrices(H, { INFY: [infy.ltp, infy.prev_close] }), H);
  assert.equal(applyPrices(H, { NOPE: [1, 1] }), H);
  assert.equal(applyPrices(null, { INFY: [1, 1] }), null);
});

test('the largest holding can change hands', () => {
  const out = applyPrices(H, { MEESHO: [100000, 82] });
  assert.equal(out.concentration.top.name, 'MEESHO');
  assert.ok(out.concentration.top.weight > 50 && out.concentration.effective < H.concentration.effective);
});
