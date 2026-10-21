import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { perPointText, scatter, dailyLine } from '../../static/linck/js/pages/geek/volatility.js';

test('₹ per VIX point in words, with how sure', () => {
  assert.equal(perPointText({ slope: -2000, r: -0.8, n: 40 }), 'Loses about ₹2,000 a day for every point VIX rises (a strong link, over 40 days)');
  assert.equal(perPointText({ slope: 300, r: 0.35, n: 12 }), 'Makes about ₹300 a day for every point VIX rises (a loose link, over 12 days)');
  assert.match(perPointText({ slope: -50, r: -0.1, n: 15 }), /little link/);
  assert.equal(perPointText(null), null);
});

test('every dot carries its trade or day; the fit line only when there is one', () => {
  const rows = [{ dvix: 1, pnl: -500 }, { dvix: -1, pnl: 400 }, { dvix: null, pnl: 10 }, { dvix: 2, pnl: null }];
  const svgA = String(scatter(rows, { x: 'dvix', y: 'pnl', tip: r => `tip ${r.pnl}`, label: 'x' }));
  assert.equal((svgA.match(/<circle/g) || []).length, 2, 'only rows with both');
  assert.equal((svgA.match(/data-tip=/g) || []).length, 2);
  assert.doesNotMatch(svgA, /stroke-dasharray="5 4"/);
  assert.match(String(scatter(rows, { x: 'dvix', y: 'pnl', tip: () => '', label: 'x', fit: { slope: -450, intercept: 0 } })), /stroke-dasharray="5 4"/);
  assert.equal(scatter([rows[0]], { x: 'dvix', y: 'pnl', tip: () => '', label: 'x' }), '');
});

test('VIX by day: dates on the axis and a guide that names the day', () => {
  const days = [{ day: '2026-10-12', vix_close: 14.2, dvix: null }, { day: '2026-10-13', vix_close: 15.1, dvix: 0.9 }, { day: '2026-10-14', vix_close: null }];
  const out = String(dailyLine(days));
  assert.match(out, /data-scrub="gkh\d+"/, 'hover shows each day');
  assert.match(out, /Mon 12 Oct/);
  assert.match(out, /Tue 13 Oct/);
  assert.equal(dailyLine([days[0]]), '');
});

test('what it says now, and the what-if filter rows', async () => {
  const { hereText, filterRows } = await import('../../static/linck/js/pages/geek/volatility.js');
  assert.equal(hereText({ n: 3, low: 13.5, high: 15.5, pnl: 300, avg: 100, win_rate: 66.7 }, 14.5),
    'With VIX between 13.50 and 15.50, as now, its 3 trades made +₹300: +₹100 on average, 67% won.');
  assert.match(hereText({ n: 0, low: 24, high: 26 }, 25), /new ground/);
  assert.equal(hereText(null, 14), null);
  const rows = String(filterRows([{ level: 13, above_n: 4, above_pnl: 2100, above_win: 75, below_n: 2, below_pnl: -1300, below_win: 0 },
    { level: 14, above_n: 3, above_pnl: 1800, above_win: 66.7, below_n: 3, below_pnl: -1000, below_win: 33.3 }], 800));
  assert.equal((rows.match(/class="vf-row" data-tip=/g) || []).length, 2, 'a tip per level');
  assert.match(rows, /width:50\.0%/, 'the biggest fills its half');
  assert.equal(filterRows([], 0), '');
});

test('the one filter that would have helped most, or none', async () => {
  const { bestFilter } = await import('../../static/linck/js/pages/geek/volatility.js');
  const f = (level, above_pnl, below_pnl, above_n = 5, below_n = 3) => ({ level, above_pnl, below_pnl, above_n, below_n });
  assert.equal(bestFilter([f(13, 2100, -1300, 4, 2), f(14, 1800, -1000), f(18, -500, 1300, 2, 4)]),
    'Skipping its 2 trades with VIX below 13 would have added ₹1,300: they lost that much together.');
  assert.match(bestFilter([f(18, -2500, 3300, 2, 4)]), /with VIX at 18 or above would have added ₹2,500/);
  assert.match(bestFilter([f(13, 100, 200)]), /No VIX level would have helped/);
  assert.equal(bestFilter([]), null);
});

test('rich against cheap, in words', async () => {
  const { richText } = await import('../../static/linck/js/pages/geek/volatility.js');
  const g = { key: 'rich', buckets: [{ label: 'Cheapest third · up to −1.2', avg: -300, win_rate: 40 }, { label: 'Middle · −1.2 to +0.8', avg: 100, win_rate: 50 }, { label: 'Richest third · above +0.8', avg: 900, win_rate: 75 }] };
  assert.equal(richText([g]), 'Trades whose premium was richest against VIX (above +0.8 points) averaged +₹900, 75% won; the cheapest (up to −1.2) averaged −₹300, 40% won.');
  assert.equal(richText([]), null);
});

test('where the P&L came from: in words, and a column per trade with its numbers', async () => {
  const { attrText, splitBars } = await import('../../static/linck/js/pages/geek/volatility.js');
  assert.equal(attrText({ n: 2, time: 1900, move: -1300, vol: 0 }),
    'Across 2 trades it could split, time decay earned ₹1,900, the index’s moves cost ₹1,300 and changes in volatility earned ₹0.');
  assert.equal(attrText(null), null);
  const rows = [{ label: 'Entry', t: '2026-10-14T10:00:00+05:30', attr: { time: 1500, move: -400, vol: -200, total: 900 } },
    { label: 'Entry', t: '2026-10-15T10:00:00+05:30', attr: null }, { label: 'Entry', t: '2026-10-16T10:00:00+05:30', attr: { time: 400, move: -900, vol: 200, total: -300 } }];
  const svg = String(splitBars(rows));
  assert.equal((svg.match(/class="hit"/g) || []).length, 2, 'a column for each trade with a split');
  assert.match(svg, /Time decay  \+₹1,500/);
  assert.equal(splitBars([rows[1]]), '');
});

test('the three parts: a row each with its colour key and numbers', async () => {
  const { partRows } = await import('../../static/linck/js/pages/geek/volatility.js');
  const out = String(partRows({ n: 2, time: 1900, move: -1300, vol: 0, total: 600 }));
  assert.equal((out.match(/class="vp-row" data-tip=/g) || []).length, 3);
  assert.match(out, /Time decay/); assert.match(out, /−₹1,300/);
  assert.doesNotMatch(out, / · \d+%/, 'no count or win rate: they mean nothing here');
});

test('open now: its open legs per VIX point', async () => {
  const { openText } = await import('../../static/linck/js/pages/geek/volatility.js');
  const ex = { deployments: [{ id: 'a', legs: 2, per_point: -4100 }, { id: 'b', legs: 1, per_point: 650 }] };
  assert.equal(openText(ex, 'a'), 'Open now: if VIX rises a point, its 2 open legs lose about ₹4,100.');
  assert.equal(openText(ex, 'b'), 'Open now: if VIX rises a point, its 1 open leg gains about ₹650.');
  assert.equal(openText(ex, 'c'), null);
  assert.equal(openText(null, 'a'), null);
});
