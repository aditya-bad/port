// What the deployments report about themselves (app/deployments/health.py)
// on the Repair page (pages/repair.js): open ones first and worst first,
// what each is and who reported it, a link to the day's case, which shows
// them too (pages/repair-case.js), and a dismiss. Seeded by
// tests/e2e/serve.py: seed_strategy_issues.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, api, until } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('the Repair page lists what the deployments reported, open and worst first', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.rp-reported .tr');
  const rows = p.locator('.rp-reported .tr');
  assert.equal(await rows.count(), 3);
  const first = await rows.nth(0).innerText();
  assert.match(first, /Unhedged[\s\S]*Nifty Strangle A: A PE short is open without its hedge/i);
  assert.match(first, /by the strategy[\s\S]*open/);
  assert.match(await rows.nth(1).innerText(), /No prices[\s\S]*Sensex Credit Spread: No prices from 11:00 to 11:20[\s\S]*by the runner[\s\S]*noted/i);
  const cleared = rows.nth(2);
  assert.match(await cleared.innerText(), /Entry late[\s\S]*BankNifty DTT Plain[\s\S]*3×[\s\S]*cleared itself/i);
  assert.ok(await cleared.evaluate(el => el.classList.contains('done')));
  assert.equal(await cleared.locator('[data-act="dismissReport"]').count(), 0, 'nothing to dismiss once closed');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a report links to its day, and the case shows what was reported', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.rp-reported .tr');
  await p.locator('.rp-reported .tr', { hasText: 'BankNifty DTT Plain' }).locator('a', { hasText: /^Review/ }).click();
  await p.waitForSelector('.rc-reported');
  const rep = await p.locator('.rc-reported').innerText();
  assert.match(rep, /10:00[\s\S]*The 10:00 entry hasn't gone through yet/);
  assert.match(rep, /cleared/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('dismissing a report closes it', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  const row = p.locator('.rp-reported .tr', { hasText: 'Nifty Strangle A' });
  await row.locator('[data-act="dismissReport"]').click();
  await until(async () => /dismissed/.test(await p.locator('.rp-reported .tr', { hasText: 'Nifty Strangle A' }).innerText()));
  const open = (await api(ctx, '/v2/strategy-issues')).issues;
  assert.equal(open.filter(x => x.name === 'Nifty Strangle A').length, 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
