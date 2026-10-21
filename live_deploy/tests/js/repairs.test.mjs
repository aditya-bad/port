import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { repairsNote } from '../../static/linck/js/ui/repairs.js';
import { fixTag } from '../../static/linck/js/ui/components.js';

test('the repaired tag only where a repair wrote', () => {
  assert.match(String(fixTag(true)), /fix-tag[^>]*data-tip="Repaired/);
  assert.equal(fixTag(false), '');
  assert.equal(fixTag(undefined), '');
});

test('one note per repair: window, trades, P&L against as left, paths chosen', () => {
  const s = String(repairsNote([{ from: '2026-10-21T12:16:00+05:30', to: '2026-10-21T15:30:00+05:30', fills: 2, pnl: -15071, left: 881,
    choices: { '13:05': 'up_first' }, trades: [{ minute: '13:05', phase: 'up', action: 'buy', symbol: 'NIFTY26OCT2025000CE', qty: 75, price: 546.8, reason: 'leg_spike_stop', realized: -25335 }] }]));
  assert.match(s, /12:16–15:30/); assert.match(s, /2 trades replayed from 1-minute candles/);
  assert.match(s, /as left by the stall/); assert.match(s, /13:05 up first/);
  assert.match(s, /13:05 candle high/); assert.match(s, /Bought 75 NIFTY26OCT2025000CE/);
  assert.match(String(repairsNote([{ from: '2026-10-21T12:16:00+05:30', to: '2026-10-21T15:30:00+05:30', fills: 0, pnl: 0, left: 0, choices: {}, trades: [] }])), /nothing to trade/);
  assert.equal(String(repairsNote([])), '<div data-repairs></div>');
});
