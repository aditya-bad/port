import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { deltaText, barPct, underlyingPanel } from '../../static/linck/js/ui/underlying.js';

test('the book’s delta in words', () => {
  assert.equal(deltaText(-42.4), 'Loses ₹42 for every point the future rises');
  assert.equal(deltaText(15), 'Makes ₹15 for every point the future rises');
  assert.match(deltaText(0.2), /flat/);
  assert.equal(deltaText(null), null);
});

test('scenario bars fill up to half the track each way', () => {
  assert.equal(barPct(-500, 1000), 25);
  assert.equal(barPct(2000, 1000), 50);
  assert.equal(barPct(5, 0), 0);
});

const U = {
  available: true, index: 'NIFTY 50',
  spot: { price: 25100, change_today: 40 },
  future: { symbol: 'NIFTY26NOVFUT', price: 25210, change_today: 20, source: 'stored', as_of: '2026-10-21T15:29:00+05:30' },
  basis: { pts: 110, pct: 0.438, annual_pct: 4.7, dte: 34, change_today: -20 },
  legs: [{ symbol: 'NIFTY26NOV25600CE', kind: 'CE', strike: 25600, side: 'short', from_spot: 500, from_fut: 390, iv: 13.2, delta: -18.4 }],
  net: { delta: -18.4, gamma: -0.02, legs: 1, legs_valued: 1, scenarios: [{ move: -100, pnl: 1700 }, { move: 100, pnl: -2000 }] },
};

test('the panel: spot, future, basis, delta, each leg from both, and the scenarios', () => {
  const s = String(underlyingPanel(U));
  assert.match(s, /data-underlying/);
  assert.match(s, /NIFTY26NOVFUT/); assert.match(s, /\+110 pts/); assert.match(s, /34 days/);
  assert.match(s, /as of/, 'a stored price says when it is from');
  assert.match(s, /Loses ₹18 for every point/);
  assert.match(s, /\+390/); assert.match(s, /\+500/);
  assert.equal((s.match(/class="ul-b"/g) || []).length, 2);
  assert.match(s, /data-tip="Future \+100 pts/);
});

test('loading, unavailable and failed all keep one wrapper', () => {
  assert.match(String(underlyingPanel(null)), /data-underlying.*skel/s);
  assert.match(String(underlyingPanel({ available: false, why: 'No futures' })), /No futures/);
  assert.match(String(underlyingPanel(new Error('x'))), /data-underlying/);
});
