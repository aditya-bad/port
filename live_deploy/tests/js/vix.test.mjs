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

test('what VIX did between entry and exit, in words', async () => {
  const { vixMove, vixText, sessionOf } = await import('../../static/linck/js/pages/geek/vix.js');
  const t = s => Date.parse(`2026-10-14T${s}:00+05:30`);
  const series = [[t('09:15'), 14.0], [t('10:00'), 14.3], [t('11:00'), 15.1], [t('14:00'), 15.6]];
  const m = vixMove(series, '2026-10-14T10:02:00+05:30', '2026-10-14T13:30:00+05:30');
  assert.deepEqual(m, { from: 14.3, to: 15.1, change: 15.1 - 14.3 }, 'the last value at or before each moment');
  assert.equal(vixText(m, false), 'VIX was 14.30 at entry and rose to 15.10 by the exit (+0.80).');
  assert.equal(vixText(vixMove(series, t('11:30'), null), true), 'VIX was 15.10 at entry and rose to 15.60 since (+0.50).', 'still open: to the latest');
  assert.match(vixText({ from: 15, to: 14.2, change: -0.8 }, false), /fell to 14\.20 by the exit \(−0\.80\)/);
  assert.match(vixText({ from: 15, to: 15.01, change: 0.01 }, false), /held at/);
  assert.equal(vixMove([], t('10:00'), null), null);
  assert.deepEqual(sessionOf('2026-10-14T04:30:00Z'), { start: '2026-10-14T09:15:00+05:30', end: '2026-10-14T15:30:00+05:30' });
});
