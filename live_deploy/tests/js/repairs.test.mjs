import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { repairsNote, repairChip, spanMark, repairsLine } from '../../static/linck/js/ui/repairs.js';
import { fixTag } from '../../static/linck/js/ui/components.js';

test('the repaired tag only where a repair wrote, linking to its repair when it knows the day', () => {
  assert.match(String(fixTag(true)), /<span class="fix-tag"[^>]*data-tip="Repaired/);
  assert.match(String(fixTag('2026-10-14', 'dep-1')), /<a class="fix-tag" href="#\/repair\/2026-10-14\/dep-1"[^>]*Repair of 2026-10-14 — open it ›/);
  assert.match(String(fixTag('2026-10-14')), /^<span/, 'no deployment: nothing to link to');
  assert.equal(fixTag(false), '');
  assert.equal(fixTag(undefined), '');
});

test('one note per repair: window, trades, P&L against as left, paths chosen', () => {
  const s = String(repairsNote([{ from: '2026-10-21T12:16:00+05:30', to: '2026-10-21T15:30:00+05:30', fills: 2, pnl: -15071, left: 881,
    day: '2026-10-21', choices: { '13:05': 'up_first' }, trades: [{ minute: '13:05', phase: 'up', action: 'buy', symbol: 'NIFTY26OCT2025000CE', qty: 75, price: 546.8, reason: 'leg_spike_stop', realized: -25335 }] }], 'dep-1'));
  assert.match(s, /12:16–15:30/); assert.match(s, /2 trades replayed from 1-minute candles/);
  assert.match(s, /as left by the stall/); assert.match(s, /13:05 up first/);
  assert.match(s, /13:05 candle high/); assert.match(s, /Bought 75 NIFTY26OCT2025000CE/);
  assert.match(s, /href="#\/repair\/2026-10-21\/dep-1">Open the repair: its proof, and undo ›/);
  assert.doesNotMatch(s, /repair script/, 'undone from its case now, not the script');
  assert.match(String(repairsNote([{ from: '2026-10-21T12:16:00+05:30', to: '2026-10-21T15:30:00+05:30', fills: 0, pnl: 0, left: 0, choices: {}, trades: [] }])), /nothing to trade/);
  assert.equal(String(repairsNote([])), '<div data-repairs></div>');
});

const dep = (done, open = []) => ({ id: 'd1', name: 'Nifty Strangle A', repairs: { done, open } });
const flag = (day, headline = 'Traded differently from 13:31') => ({ day, status: 'mismatch', headline });

test('a deployment’s mark: a flagged day first, else its latest repair, each opening its case', () => {
  assert.equal(repairChip(dep([])), '');
  assert.equal(repairChip({ id: 'x' }), '', 'no book data yet');
  const r = String(repairChip(dep(['2026-10-07', '2026-10-14'])));
  assert.match(r, /<a class="fix-tag" href="#\/repair\/2026-10-14\/d1"/);
  assert.match(r, /Repaired on 2 days: 7 Oct, 14 Oct/);
  const f = String(repairChip(dep(['2026-10-14'], [flag('2026-10-20'), flag('2026-10-21', 'Still holding after 15:15')])));
  assert.match(f, /<a class="fix-flag" href="#\/repair\/2026-10-21\/d1"[^>]*>Flagged</);
  assert.match(f, /20 Oct: Traded differently from 13:31\n21 Oct: Still holding after 15:15/);
  // inside a row that's a link of its own: a plain mark
  assert.match(String(repairChip(dep([], [flag('2026-10-21')]), { link: false })), /^<span class="fix-flag"/);
  assert.match(String(repairChip(dep(['2026-10-14']), { link: false })), /^<span class="fix-tag"[^>]*Repaired on 14 Oct/);
});

test('a day or a cycle is marked by the days it spans', () => {
  const v = dep(['2026-10-14'], [flag('2026-10-16')]);
  assert.match(String(spanMark(v, '2026-10-14')), /fix-tag" href="#\/repair\/2026-10-14\/d1"/);
  assert.equal(spanMark(v, '2026-10-15'), '');
  assert.match(String(spanMark(v, '2026-10-16')), /fix-flag" href="#\/repair\/2026-10-16\/d1"/);
  assert.match(String(spanMark(v, '2026-10-10', '2026-10-15')), /Repair of 14 Oct/, 'a cycle across it');
  assert.match(String(spanMark(v, '2026-10-10', '2026-10-20')), /fix-flag/, 'flagged wins');
});

test('figures over many days say which of them were repaired or flagged', () => {
  assert.equal(repairsLine([dep([])]), '');
  const one = String(repairsLine([dep(['2026-10-14'])]));
  assert.match(one, /Includes days repaired from Kite’s 1-minute candles/);
  assert.match(one, /href="#\/repair\/2026-10-14\/d1"[^>]*>14 Oct</);
  const many = String(repairsLine([dep(['2026-10-14']), { ...dep([], [flag('2026-10-21')]), id: 'd2', name: 'Bankex' }]));
  assert.match(many, /Includes repaired days, and a day the end-of-day check flagged/);
  assert.match(many, /fix-flag[^>]*>Bankex · 21 Oct<[\s\S]*fix-tag[^>]*>Nifty Strangle A · 14 Oct</, 'flagged first, named when several');
  assert.equal(repairsLine([dep(['2026-09-14'])], '2026-10-01', '2026-11-01'), '', 'only the period shown');
});
