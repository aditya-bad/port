import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { expectedMovePct, vixTip } from '../../static/linck/js/data/vix.js';

test('the move VIX implies: annualised over calendar days', () => {
  assert.equal(expectedMovePct(15).toFixed(3), '0.785');
  assert.equal(expectedMovePct(15, 7).toFixed(2), '2.08');
});

test('the VIX tip: now, since yesterday, the moves it implies, and that it’s a Nifty measure', () => {
  const t = vixTip({ price: 16.4, prevClose: 15.1, live: true });
  for (const s of ['India VIX', 'Now  16.40', 'Since yesterday  +1.30', 'Expected move today  ±0.86%', 'Over a week  ±2.27%', 'Nifty options']) assert.ok(t.includes(s), `${s} in\n${t}`);
  assert.match(vixTip({ price: 14, prevClose: null, live: false }), /India VIX \(last recorded\)/);
  assert.match(vixTip(null), /once Kite is logged in/);
});
