// The Repair page's day check (pages/repair.js; app/repair/daycheck.py):
// yesterday's results worst first — the rule broken, the trades the replay
// made that the app didn't side by side, the end states — the ones as
// expected folded away, another day picked, and a run started by hand
// (the real worker, in its own process: with no Kite session it fails,
// and the page says why). Seeded by tests/e2e/serve.py: seed_day_checks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, api, until } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('yesterday’s check: what broke, side by side, worst first', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-row');
  assert.match(await p.locator('.dc-bar').innerText(), /Ran after the export, 15:52–16:03/);
  const counts = await p.locator('.dc-counts').innerText();
  assert.match(counts, /Rule broken\s*1/i);
  assert.match(counts, /Traded differently\s*1/i);
  assert.match(counts, /To review\s*1/i);
  assert.match(counts, /As expected\s*6/i);
  const rows = p.locator('.dc-list > .dc-row');
  assert.equal(await rows.count(), 3, 'the flagged ones, open');
  const rule = rows.nth(0);
  assert.match(await rule.innerText(), /BankNifty DTT Plain[\s\S]*Still holding 2 leg\(s\) after its 15:00 cut-off/);
  assert.equal(await rule.locator('.dc-trades > .missed').count(), 2);
  assert.match(await rule.locator('.dc-trades').innerText(), /15:00 Bought 35 BANKNIFTY26OCT55700CE[\s\S]*The app didn’t/);
  assert.match(await rule.locator('.dc-trades').innerText(), /₹\+1\.2 vs the candle/);
  assert.match(await rule.locator('.dc-end').innerText(), /would have ended with\s*Flat[\s\S]*The app ended with\s*Short 35 BANKNIFTY26OCT55700CE/i);
  assert.match(await rows.nth(1).innerText(), /Nifty Strangle A[\s\S]*continuous_50pct_trigger/);
  assert.match(await rows.nth(2).innerText(), /Nifty Monthly Strangle[\s\S]*Read the usual way at 13:05/);
  const fine = p.locator('.dc-fine');
  assert.match(await fine.locator('> summary').innerText(), /6 deployments as expected/);
  assert.equal(await fine.evaluate(el => el.open), false, 'folded away');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('another day, and a run started by hand reports how it went', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('[data-f="dcDay"]');
  const days = await p.locator('[data-f="dcDay"] option').evaluateAll(os => os.map(o => o.value));
  assert.equal(days.length, 2);
  await p.selectOption('[data-f="dcDay"]', days[1]);
  await until(async () => (await p.locator('.dc-fine > summary').innerText().catch(() => '')).includes('9 deployments as expected'));
  assert.equal(await p.locator('.dc-list > .dc-row').count(), 0, 'nothing flagged that day');
  // the real worker: no Kite session in this server, so it fails and says so
  await p.click('[data-act="runCheck"]');
  await until(async () => /Failed[\s\S]*No Kite session/.test(await p.locator('.dc-bar').innerText()), 60000);
  const r = await api(ctx, `/v2/day-checks?day=${days[1]}`);
  assert.equal(r.run.status, 'failed');
  assert.equal(r.results.length, 9, 'the day’s results are untouched');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone the day check stacks and fits', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-row');
  const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(over <= 1, `no sideways scroll (${over}px)`);
  const label = await p.locator('.dc-trades .missed [data-l]').first().evaluate(el => getComputedStyle(el, '::before').content);
  assert.match(label, /Strategy ·/, 'each stacked cell says whose it is');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
