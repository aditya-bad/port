import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import * as F from '../../static/linck/js/core/format.js';

const M = F.MINUS;

test('rupees: Indian grouping, signed, true minus', () => {
  assert.equal(F.inr(123456), '+₹1,23,456');
  assert.equal(F.inr(-1230), `${M}₹1,230`);
  assert.equal(F.inr(0), '₹0');
  assert.equal(F.inr(1500, { sign: false }), '₹1,500');
  assert.equal(F.inr(12.345, { dp: 2 }), '+₹12.35');
  assert.equal(F.inr(-0.4), '₹0', 'rounds to zero without a sign');
  for (const bad of [null, undefined, '', 'x', NaN, Infinity]) assert.equal(F.inr(bad), '—');
});

test('rupees short: lakh and crore', () => {
  assert.equal(F.inrS(99999), '+₹99,999');
  assert.equal(F.inrS(240000), '+₹2.4L');
  assert.equal(F.inrS(-31000000), `${M}₹3.1Cr`);
  assert.equal(F.inrS(100000), '+₹1L');
  assert.equal(F.inrFit(5e7), '+₹5Cr');
  assert.equal(F.inrFit(5000), '+₹5,000');
});

test('axis labels', () => {
  assert.equal(F.inrAxis(45000), '₹45k');
  assert.equal(F.inrAxis(1500), '₹1.5k');
  assert.equal(F.inrAxis(-120000), `${M}₹1.2L`);
  assert.equal(F.inrAxis(250), '₹250');
  assert.equal(F.inrAxis(null), '');
});

test('percent, price, number', () => {
  assert.equal(F.pct(6.234), '+6.23%');
  assert.equal(F.pct(-0.86), `${M}0.86%`);
  assert.equal(F.pct(-0.001), '0.00%', 'no −0.00%');
  assert.equal(F.pct(5, 1, false), '5.0%');
  assert.equal(F.price(25184.3), '25,184.30');
  assert.equal(F.price(-2.5), `${M}2.50`);
  assert.equal(F.num(1234.5, 1), '1,234.5');
});

test('short option symbols put the strike first', () => {
  assert.equal(F.shortSymbol('NIFTY26NOV25600CE'), '25600 CE · 26NOV');
  assert.equal(F.shortSymbol('BANKNIFTY26O2855000PE'), '55000 PE · 26O28');
  assert.equal(F.shortSymbol('NIFTY 50'), 'NIFTY 50');
  assert.equal(F.shortSymbol(null), null);
});

test('tone and plural', () => {
  assert.equal(F.tone(10), 'pos'); assert.equal(F.tone(-10), 'neg');
  assert.equal(F.tone(0.3), 'flat'); assert.equal(F.tone(null), 'flat');
  assert.equal(F.plural(1, 'leg'), '1 leg'); assert.equal(F.plural(2, 'leg'), '2 legs');
  assert.equal(F.plural(0, 'body', 'bodies'), '0 bodies');
});

test('times and dates are always IST', () => {
  const t = new Date('2026-10-16T06:12:07Z');      // 11:42:07 IST, a Friday
  assert.equal(F.hhmm(t), '11:42');
  assert.equal(F.hhmmss(t), '11:42:07');
  assert.equal(F.dShort(t), "16 Oct '26");
  assert.equal(F.dDay(t), 'Fri 16 Oct');
  assert.equal(F.dFull(t), 'Fri 16 Oct 2026');
  assert.equal(F.dLong(t), 'Friday 16 October');
  assert.equal(F.dMonthYear(t), 'Oct 2026');
  assert.equal(F.dayKey(new Date('2026-10-15T19:00:00Z')), '2026-10-16', 'after IST midnight is the next day');
  assert.equal(F.dayKey(F.fromDay('2026-10-16')), '2026-10-16');
});

test('durations', () => {
  assert.equal(F.duration(45 * 60000), '45m');
  assert.equal(F.duration(125 * 60000), '2h 5m');
  assert.equal(F.duration(26 * 3600000), '1d 2h');
  assert.equal(F.duration(-1), '—');
});

test('market session 09:15–15:30 IST, weekdays', () => {
  const at = s => new Date(`2026-10-14T${s}:00+05:30`);   // Wednesday
  assert.equal(F.sessionFrac(at('09:15')), 0);
  assert.equal(F.sessionFrac(at('15:30')), 1);
  assert.ok(Math.abs(F.sessionFrac(at('12:22')) - 0.5) < 0.01);
  assert.equal(F.sessionLeft(at('15:00')), '0h 30m');
  assert.equal(F.sessionLeft(at('09:00')), null);
  assert.equal(F.isSessionOpen(at('10:00')), true);
  assert.equal(F.isSessionOpen(at('15:30')), false);
  assert.equal(F.isSessionOpen(new Date('2026-10-17T10:00:00+05:30')), false, 'Saturday');
  assert.equal(F.sessionFrac(new Date('2026-10-18T10:00:00+05:30')), 1, 'Sunday reads as closed');
});
