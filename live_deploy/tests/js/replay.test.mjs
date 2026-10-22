import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  eventAt, nextEvent, prevEvent, advance, change, frameOf, positionsAt, stepsFor, stepTo, asOf,
  payoff, breakevens, extremes, rangeOf, mismatched, payoffChart, grid, KIND, payoffHeight, pnlPct,
} from '../../static/linck/js/pages/geek/replay.js';
import { bs, black76, yearsTo } from '../../static/linck/js/data/options.js';

const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg || ''} ${a} vs ${b}`);

test('option prices match the server’s (app/geek/optionmath.py)', () => {
  // reference values from the Python functions
  near(bs('CE', 25000, 25200, 7 / 365, 0.15, 0.065), 134.358095, 0.01);
  near(bs('PE', 25000, 24800, 7 / 365, 0.15, 0.065), 111.006026, 0.01);
  near(black76('CE', 25100, 25200, 7 / 365, 0.15, 0.065), 162.021651, 0.01);
  assert.equal(bs('PE', 25000, 25200, 0, 0.15, 0.065), 200, 'at expiry: what it’s in the money');
  near(yearsTo('2026-10-28', '2026-10-22T10:00:00+05:30'), 0.0170662100, 1e-9);
});

const EV = [{ frame: 0, kind: 'new' }, { frame: 5, kind: 'adjustment' }, { frame: 9, kind: 'exit' }];

test('the trade shown is the last one at or before the step; next and previous jump over it', () => {
  assert.equal(eventAt(EV, 4).kind, 'new');
  assert.equal(eventAt(EV, 5).kind, 'adjustment');
  assert.equal(eventAt([{ frame: 3 }], 1), null);
  assert.equal(nextEvent(EV, 5).frame, 9);
  assert.equal(nextEvent(EV, 9), null);
  assert.equal(prevEvent(EV, 5).frame, 0);
  assert.equal(prevEvent(EV, 0), null);
});

test('step sizes are whole multiples of the stored prices, and a day', () => {
  assert.deepEqual(stepsFor('minute').map(s => s[0]), ['1m', '5m', '15m', '30m', '1h', '1D']);
  assert.deepEqual(stepsFor('5minute').map(s => s[0]), ['5m', '15m', '30m', '1h', '1D']);
  assert.deepEqual(stepsFor('15minute').map(s => s[0]), ['15m', '30m', '1h', '1D']);
  assert.deepEqual(stepsFor('60minute').map(s => s[0]), ['1h', '1D']);
});

// two trading days of 1-minute steps: a Monday 09:15–15:29, the Tuesday 09:15–12:00
const at = (d, h, m) => `2026-10-${d}T${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00+05:30`;
const DAYS = [];
for (let m = 9 * 60 + 15; m < 15 * 60 + 30; m++) DAYS.push({ t: at(19, Math.floor(m / 60), m % 60) });
for (let m = 9 * 60 + 15; m <= 12 * 60; m++) DAYS.push({ t: at(20, Math.floor(m / 60), m % 60) });
const idx = t => DAYS.findIndex(f => f.t === t);

test('a step moves by its minutes; nights don’t count; a day keeps the time of day', () => {
  assert.equal(stepTo(DAYS, idx(at(19, 10, 0)), 5, 1), idx(at(19, 10, 5)));
  assert.equal(stepTo(DAYS, idx(at(19, 10, 0)), 1, 1), idx(at(19, 10, 1)));
  assert.equal(stepTo(DAYS, idx(at(19, 15, 20)), 30, 1), idx(at(20, 9, 15)), 'past the close: the next open');
  assert.equal(stepTo(DAYS, idx(at(20, 9, 20)), 30, -1), idx(at(19, 15, 29)), 'back past the open: the close before');
  assert.equal(stepTo(DAYS, idx(at(20, 11, 50)), 30, 1), DAYS.length - 1, 'never past the end');
  assert.equal(stepTo(DAYS, 3, 60, -1), 0, 'never before the start');
  assert.equal(stepTo(DAYS, idx(at(19, 10, 0)), 0, 1), idx(at(20, 10, 0)), 'a day on: the same time');
  assert.equal(stepTo(DAYS, idx(at(19, 14, 0)), 0, 1), DAYS.length - 1, 'the next day ends sooner: its last step');
  assert.equal(stepTo(DAYS, idx(at(20, 10, 0)), 0, -1), idx(at(19, 10, 0)));
  assert.equal(stepTo(DAYS, idx(at(20, 10, 0)), 0, 1), DAYS.length - 1, 'no day after: the end');
});

test('play takes a step at a time, pausing on a trade it passes when asked', () => {
  const ev = [{ frame: idx(at(19, 10, 3)), kind: 'adjustment' }];
  const s = advance(DAYS, ev, idx(at(19, 10, 0)), 5, true);
  assert.equal(s.i, idx(at(19, 10, 3))); assert.equal(s.stop, true); assert.equal(s.event.kind, 'adjustment');
  assert.deepEqual(advance(DAYS, ev, idx(at(19, 10, 0)), 5, false), { i: idx(at(19, 10, 5)), stop: false, event: null });
  assert.deepEqual(advance(DAYS, [], DAYS.length - 2, 5, false), { i: DAYS.length - 1, stop: true, event: null });
});

test('changes between steps, unknown when either side is', () => {
  const F = [{ idx: 100, vix: 13 }, { idx: 120, vix: null }, { idx: 90, vix: 14 }];
  assert.equal(change(F, 0, 2, 'idx'), -10);
  assert.equal(change(F, 0, 1, 'vix'), null);
});

// three 5-minute steps; leg 0 open throughout, leg 1 opened in the second and closed in the third
const T = m => `2026-10-14T10:${String(m).padStart(2, '0')}:00+05:30`;
const SMALL = {
  interval: '5minute', index_name: 'NIFTY 50',
  frames: [
    { t: T(0), idx: 100, pnl: 50, booked: 0, legs: [[0, 95, 50, false, 14.2]] },
    { t: T(5), idx: 102, pnl: 30, booked: 0, legs: [[0, 97, 20, false, 14.5], [1, 52, 10, false, null]] },
    { t: T(10), idx: 101, pnl: 60, booked: 25, legs: [[0, 94, 35, false, 14.1]] },
  ],
  legs: [
    { symbol: 'X CE', kind: 'CE', strike: 105, side: 'short', qty: 10, entry: 100, t0: T(0), t1: null, realized: null, exit: null },
    { symbol: 'X PE', kind: 'PE', strike: 95, side: 'short', qty: 10, entry: 53, t0: '2026-10-14T10:06:30+05:30', t1: '2026-10-14T10:12:00+05:30', realized: 25, exit: 50.5 },
  ],
  events: [],
};

test('a time falls in the step that started at or before it; prices are as of the step’s end', () => {
  assert.equal(frameOf(SMALL.frames, '2026-10-14T10:06:30+05:30'), 1);
  assert.equal(frameOf(SMALL.frames, T(30)), 2);
  assert.equal(asOf(SMALL, 1).toISOString(), new Date(T(10)).toISOString());
});

test('positions: new ones join as they open, closed ones stay with what they booked', () => {
  assert.deepEqual(positionsAt(SMALL, 0).map(p => p.l.symbol), ['X CE']);
  assert.deepEqual(positionsAt(SMALL, 1).map(p => [p.l.symbol, p.fresh, p.closed, p.now, p.pnl, p.iv]),
    [['X CE', false, false, 97, 20, 14.5], ['X PE', true, false, 52, 10, null]]);
  assert.deepEqual(positionsAt(SMALL, 2).map(p => [p.closed, p.justClosed, p.now, p.pnl]), [[false, false, 94, 35], [true, true, 50.5, 25]]);
});

// a short strangle on NIFTY at 25,000: 25200 CE sold at 100, 24800 PE sold at 90, 75 each, ₹1,000 booked
// before; each leg's price now is what its volatility gives, as the server's would
const NOW = '2026-10-22T10:00:00+05:30', EXP = '2026-10-28', R = 0.065;
const T1 = yearsTo(EXP, new Date(new Date(NOW).getTime() + 60000));
function strangle({ fut = null, fwd = false, ivs = [15, 16], expiry = EXP, extra = [] } = {}) {
  const s = 25000, u = fwd ? fut : s;
  const price = (kind, k, iv) => (fwd ? black76(kind, u, k, T1, iv / 100, R) : bs(kind, u, k, T1, iv / 100, R));
  const legs = [
    { symbol: 'NIFTY26OCT25200CE', kind: 'CE', strike: 25200, side: 'short', qty: 75, entry: 100, t0: NOW, t1: null, expiry, fwd, iv: 14 },
    { symbol: 'NIFTY26OCT24800PE', kind: 'PE', strike: 24800, side: 'short', qty: 75, entry: 90, t0: NOW, t1: null, expiry, fwd, iv: 15 },
    ...extra.map(e => e.leg),
  ];
  const marks = [price('CE', 25200, ivs[0] ?? 14), price('PE', 24800, ivs[1] ?? 15)];
  const held = [[0, marks[0], (100 - marks[0]) * 75, false, ivs[0]], [1, marks[1], (90 - marks[1]) * 75, ivs[1] == null, ivs[1]], ...extra.map((e, k) => [2 + k, ...e.held])];
  const pnl = 1000 + held.reduce((a, h) => a + h[2], 0);
  return { interval: 'minute', index_name: 'NIFTY 50', rate: R, legs, events: [], frames: [{ t: NOW, idx: s, fut, booked: 1000, pnl, legs: held }] };
}

test('the payoff at expiry: what each leg is worth in the money, plus what’s booked', () => {
  const D = strangle(), xs = [24000, 24610, 25000, 25390, 25500];
  const P = payoff(D, 0, xs);
  assert.equal(P.target, EXP);
  near(P.expiry[2], 1000 + (100 + 90) * 75, 1e-6, 'between the strikes: both premiums kept');
  near(P.expiry[4], 1000 + (100 - 300) * 75 + 90 * 75, 1e-6, 'the call 300 in the money');
  near(P.expiry[0], 1000 + 100 * 75 + (90 - 800) * 75, 1e-6);
  // breakevens of the legs alone are the strikes ± 190 of premium; the ₹1,000 booked moves them out a little
  const be = breakevens(grid(24000, 26000, 2001), payoff(D, 0, grid(24000, 26000, 2001)).expiry);
  assert.equal(be.length, 2);
  near(be[0], 24800 - 190 - 1000 / 75, 0.5); near(be[1], 25200 + 190 + 1000 / 75, 0.5);
  const ex = extremes(payoff(D, 0, grid(24000, 26000, 401)).expiry);
  near(ex.max, 1000 + 190 * 75, 1e-6);
  assert.equal(ex.maxUnlimited, false); assert.equal(ex.minUnlimited, true, 'a short strangle’s loss runs on');
});

test('the payoff today goes through the actual P&L at the index', () => {
  const D = strangle();
  const P = payoff(D, 0, [24900, 25000, 25100]);
  near(P.now[1], D.frames[0].pnl, 0.5, 'today, at where the index is');
  assert.ok(P.now[0] < P.expiry[0] && P.now[1] < P.expiry[1], 'a short strangle is worth less today than at expiry');
  assert.deepEqual(P.approx, []); assert.deepEqual(P.notes, []);
});

test('priced off the future: the basis is held across the levels', () => {
  const D = strangle({ fut: 25100, fwd: true });
  const P = payoff(D, 0, [25000]);
  near(P.now[0], D.frames[0].pnl, 0.5);
  assert.equal(P.basis, 100);
});

test('a leg with no price now uses its volatility at entry, and says so; with no expiry there’s no line for today', () => {
  const D = strangle({ ivs: [15, null] });
  const P = payoff(D, 0, [25000]);
  assert.deepEqual(P.approx, ['NIFTY26OCT24800PE']);
  assert.ok(P.now);
  const noExp = payoff(strangle({ expiry: null }), 0, [25000]);
  assert.equal(noExp.now, null);
  assert.match(noExp.notes.join(' '), /expiry isn’t known/);
  near(noExp.expiry[0], 1000 + 190 * 75, 1e-6, 'the expiry payoff needs only the strikes');
});

test('a future in the position moves one for one with the index', () => {
  const fut = { leg: { symbol: 'NIFTY26OCTFUT', kind: 'FUT', strike: null, side: 'long', qty: 75, entry: 25000, t0: NOW, t1: null, expiry: EXP }, held: [25000, 0, false, null] };
  const D = strangle({ extra: [fut] });
  const P = payoff(D, 0, [25000, 25100, 25300, 25400]);
  near(P.expiry[1] - P.expiry[0], 100 * 75, 1e-6, 'between the strikes only the future moves');
  near(P.expiry[3] - P.expiry[2], 0, 1e-6, 'past the call: the short call’s loss cancels the future’s gain');
});

test('the range covers every strike and breakeven; an index that can’t be these contracts’ is caught', () => {
  const [lo, hi, spot] = rangeOf(strangle(), 0);
  assert.equal(spot, 25000);
  assert.ok(lo < 24800 - 190 && hi > 25200 + 190);
  assert.deepEqual(mismatched(strangle(), 0), []);
  const far = strangle(); far.frames[0].idx = 12000;
  assert.deepEqual(mismatched(far, 0).map(l => l.strike), [25200, 24800]);
});

test('the chart: both curves, the index tagged, the breakevens, the P&L at the index', () => {
  const D = strangle(), [lo, hi] = rangeOf(D, 0), xs = grid(lo, hi, 241);
  const s = String(payoffChart(D, 0, payoff(D, 0, xs), xs));
  assert.match(s, /data-curve="expiry"/); assert.match(s, /data-curve="now"/);
  assert.match(s, /NIFTY 50 25,000/);
  assert.match(s, /class="rp-be"/);
  assert.match(s, /S CE 25200/); assert.match(s, /S PE 24800/);
  assert.match(s, /data-scrub=/, 'hovering it reads the curves');
  // drawn at the size it's shown at, so its text stays readable; never narrower than 420
  assert.match(String(payoffChart(D, 0, payoff(D, 0, xs), xs, 1150, 295)), /viewBox="0 0 1150 295"/);
  assert.match(String(payoffChart(D, 0, payoff(D, 0, xs), xs, 360, 280)), /viewBox="0 0 420 280"/);
});

test('the chart’s height leaves room for the controls above it and the positions under it', () => {
  assert.equal(payoffHeight(1440, 900), 295);
  assert.equal(payoffHeight(1366, 768), 236);
  assert.equal(payoffHeight(1920, 1200), 360, 'never taller than 360');
  assert.equal(payoffHeight(1280, 600), 210, 'never shorter than 210');
  assert.equal(payoffHeight(390, 844), 280, 'a phone');
});

test('a leg’s P&L as a % of what it was entered for', () => {
  assert.equal(pnlPct({ entry: 200, qty: 75 }, 1500), 10, 'a short that fell 20 points of 200');
  assert.equal(pnlPct({ entry: 200, qty: 75 }, -3000), -20);
  assert.equal(pnlPct({ entry: 200, qty: 75 }, null), null, 'no P&L yet');
  assert.equal(pnlPct({ entry: 0, qty: 75 }, 100), null, 'no entry price');
});

test('every kind the backend sends has a label and a colour', () => {
  for (const k of ['new', 'entry', 'adjustment', 'roll', 'hedge', 'added', 'leg_closed', 'exit']) assert.ok(KIND[k][0] && KIND[k][1], k);
});
