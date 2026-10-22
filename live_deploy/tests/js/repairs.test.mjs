import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { repairsNote, repairChip, spanMark, repairsLine, cycleName, cyclesMark, repairedCycles } from '../../static/linck/js/ui/repairs.js';
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

// done: [day, or [day, {from, to, current}]] — a day alone is a one-day cycle, not current
const dep = (done, open = []) => ({ id: 'd1', name: 'Nifty Strangle A', repairs: { done: done.map(x => (Array.isArray(x) ? { day: x[0], cycle: x[1] } : { day: x, cycle: { from: x, to: x, current: false } })), open } });
const flag = (day, headline = 'Traded differently from 13:31') => ({ day, status: 'mismatch', headline });
const now = (day, from = day, to = day) => [day, { from, to, current: true }];

test('a cycle is named by its days', () => {
  assert.equal(cycleName({ from: '2026-10-14', to: '2026-10-14' }), '14 Oct');
  assert.equal(cycleName({ from: '2026-10-01', to: '2026-10-09' }), '1 Oct – 9 Oct');
  assert.equal(cycleName({ from: '2026-10-12', to: null }), '12 Oct – now', 'still open');
});

test('a deployment is marked Repaired only while the repaired cycle is its current one', () => {
  assert.equal(repairChip(dep([])), '');
  assert.equal(repairChip({ id: 'x' }), '', 'no book data yet');
  assert.equal(repairChip(dep(['2026-10-07', '2026-10-14'])), '', 'older cycles: no mark on the deployment');
  const r = String(repairChip(dep(['2026-10-07', now('2026-10-16', '2026-10-12', null)])));
  assert.match(r, /<a class="fix-tag" href="#\/repair\/2026-10-16\/d1"/);
  assert.match(r, /Its current cycle \(12 Oct – now\) was repaired\. Marked until the next cycle begins/);
  assert.match(String(repairChip(dep([now('2026-10-14')]), { link: false })), /^<span class="fix-tag"[^>]*14 Oct/);
});

test('a flagged day marks the deployment however old, until it’s repaired or dismissed', () => {
  const f = String(repairChip(dep(['2026-10-14'], [flag('2026-09-20'), flag('2026-10-21', 'Still holding after 15:15')])));
  assert.match(f, /<a class="fix-flag" href="#\/repair\/2026-10-21\/d1"[^>]*>Flagged</);
  assert.match(f, /20 Sept?: Traded differently from 13:31\n21 Oct: Still holding after 15:15/);
  assert.match(String(repairChip(dep([], [flag('2026-10-21')]), { link: false })), /^<span class="fix-flag"/);
});

test('a day or a cycle row is marked by the repairs in it', () => {
  const v = dep([['2026-10-14', { from: '2026-10-12', to: '2026-10-16', current: false }]], [flag('2026-10-20')]);
  assert.match(String(spanMark(v, '2026-10-14')), /fix-tag" href="#\/repair\/2026-10-14\/d1"[^>]*Repaired cycle: 12 Oct – 16 Oct/);
  assert.equal(spanMark(v, '2026-10-15'), '', 'another day of the cycle: only the day repaired');
  assert.match(String(spanMark(v, '2026-10-12', '2026-10-16')), /Repaired cycle: 12 Oct – 16 Oct/, 'the cycle’s own row');
  assert.match(String(spanMark(v, '2026-10-20')), /fix-flag" href="#\/repair\/2026-10-20\/d1"/);
  assert.match(String(spanMark(v, '2026-10-10', '2026-10-21')), /fix-flag/, 'flagged wins');
});

test('figures over many cycles count the repaired cycles, once each', () => {
  assert.equal(repairsLine([dep([])]), '');
  const c = { from: '2026-10-12', to: '2026-10-16', current: false };
  const one = String(repairsLine([dep([['2026-10-13', c], ['2026-10-14', c]])]));
  assert.match(one, /Includes a repaired cycle, rebuilt from Kite’s 1-minute candles/);
  assert.match(one, /href="#\/repair\/2026-10-13\/d1"[^>]*>12 Oct – 16 Oct</);
  assert.equal((one.match(/class="fix-tag"/g) || []).length, 1, 'two repaired days of one cycle: one cycle');
  const two = String(repairsLine([dep(['2026-10-07', ['2026-10-13', c]])]));
  assert.match(two, /Includes 2 repaired cycles/);
  const many = String(repairsLine([dep(['2026-10-14']), { ...dep([], [flag('2026-10-21')]), id: 'd2', name: 'Bankex' }]));
  assert.match(many, /Includes a repaired cycle, and a day the end-of-day check flagged/);
  assert.match(many, /fix-flag[^>]*>Bankex · 21 Oct<[\s\S]*fix-tag[^>]*>Nifty Strangle A · 14 Oct</, 'flagged first, named when several');
  assert.equal(repairsLine([dep(['2026-09-14'])], '2026-10-01', '2026-11-01'), '', 'only the period shown');
});

test('a ranking row: the repaired cycles in its period, and any flagged day', () => {
  const v = dep(['2026-09-10', '2026-10-07', '2026-10-14']);
  assert.match(String(cyclesMark(v, '2026-10-01')), />2 repaired cycles</);
  assert.match(String(cyclesMark(v, '2026-10-01')), /Repaired in this period — 7 Oct, 14 Oct/);
  assert.match(String(cyclesMark(v)), />3 repaired cycles</, 'all time');
  assert.match(String(cyclesMark(dep(['2026-10-14']), '2026-10-01')), />Repaired cycle</);
  assert.equal(String(cyclesMark(v, '2026-11-01')), '', 'none this period');
  assert.match(String(cyclesMark(dep([], [flag('2026-08-01')]), '2026-10-01')), /fix-flag/, 'a flagged day shows whatever the period');
  assert.deepEqual(repairedCycles(v, '2026-10-01').map(x => x.day), ['2026-10-07', '2026-10-14']);
});
