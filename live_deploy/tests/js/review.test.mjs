import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { weekName, vsUsual, bars, ruleLabel } from '../../static/linck/js/pages/review.js';

test('the week’s name, within a month and across two', () => {
  assert.equal(weekName('2025-03-10', '2025-03-14'), '10–14 Mar 2025');
  assert.equal(weekName('2026-09-28', '2026-10-02'), '28 Sep–2 Oct 2026');
});

test('this week against a usual one', () => {
  assert.equal(vsUsual(2500, 1000), '+₹1,500 vs a usual week (+₹1,000)');
  assert.equal(vsUsual(1000, 1000.2), 'same as a usual week');
  assert.equal(vsUsual(500, null), 'no earlier weeks to compare');
});

test('every bar shows its numbers on hover, and labels never collide', () => {
  const items = Array.from({ length: 9 }, (_, i) => [`${i + 1} Sep`, i % 2 ? -100 * i : 100 * i, `Week ${i}\nP&L  ₹${i}`]);
  const svg = String(bars(items));
  assert.equal((svg.match(/data-tip=/g) || []).length, 9, 'a tip per bar');
  assert.equal((svg.match(/<text/g) || []).length, 9, 'room for every label at 600 wide');
  const narrow = String(bars(items, { w: 360 }));
  assert.ok((narrow.match(/<text/g) || []).length < 9, 'every other label when narrow');
  assert.match(narrow, />9 Sep</, 'the latest week keeps its label');
  assert.equal(bars([]), '');
});

test('a rule’s key in words', () => {
  assert.equal(ruleLabel('leg_stop'), 'Leg stop');
  assert.equal(ruleLabel(''), 'Trade');
});
