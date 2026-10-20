// Compare with groups: several deployments combined into one, so a whole
// strategy can be compared with another, or some of a strategy's
// deployments with the rest.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const chips = p => p.$$eval('.cmp-chip:not(.dashed)', cs => cs.map(c => ({ text: c.textContent.replace(/\s+/g, ' ').trim(), group: c.classList.contains('group') })));

test('strategy against strategy: each one is all its deployments combined', async () => {
  const ctx = await context(b);
  const deps = await api(ctx, '/deployments');
  const strangles = deps.filter(d => d.strategy_name === 'strangle_monthly_v2');
  assert.ok(strangles.length >= 3);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/compare`);
  await p.click('[data-act=mode][data-v=strategy]');
  const tiles = p.locator('.tile[data-act=toggleGroup]');
  assert.ok(await tiles.count() >= 3, 'one tile per strategy');
  const strangleTile = tiles.filter({ hasText: `${strangles.length} deployments combined` });
  await strangleTile.first().click();
  await tiles.filter({ hasNot: p.locator('text=/[2-9] deployments combined/') }).first().click();
  assert.match(await p.textContent('.cmp-bar'), /2 of 5 selected/);
  await p.click('[data-act=go]');
  await p.waitForSelector('.verdict');
  const cs = await chips(p);
  assert.equal(cs.length, 2);
  assert.ok(cs[0].group && new RegExp(`· ${strangles.length}`).test(cs[0].text), `the strangles as one: ${cs[0].text}`);
  assert.match(await p.textContent('.cmp-groups'), /as one: P&L added up day by day/);
  // capital adds up
  const capRow = p.locator('.mrow').filter({ has: p.locator('b', { hasText: /^Capital$/ }) });
  const want = strangles.reduce((a, d) => a + Number(d.initial_capital), 0);
  const shown = await capRow.locator('.mcell').first().innerText();
  assert.equal(Number(shown.replace(/[^\d.]/g, '')) * (/L/.test(shown) ? 1e5 : /Cr/.test(shown) ? 1e7 : 1), want, `capital ${shown}`);
  assert.match(await p.locator('.mrow').filter({ has: p.locator('b', { hasText: /^Deployments$/ }) }).innerText(), new RegExp(String(strangles.length)));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('some deployments of a strategy against another: tick, combine, compare', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/compare`);
  for (const n of ['Nifty Strangle A', 'Nifty Strangle B']) await p.click(`.tile[data-id="${IDS[n]}"]`);
  await p.click('[data-act=combine]');
  await p.click(`.tile[data-id="${IDS['Bankex Strangle']}"]`);
  let cs = await chips(p);
  assert.equal(cs.length, 2);
  assert.ok(cs[0].group && /Nifty Strangle A \+ Nifty Strangle B/.test(cs[0].text), cs[0].text);
  assert.match(await p.locator(`.tile[data-id="${IDS['Nifty Strangle A']}"] .tile-box`).getAttribute('class'), /in-group/);
  await p.click('[data-act=go]');
  await p.waitForSelector('.verdict');
  assert.match(await p.textContent('.cmp-groups'), /Nifty Strangle A \+ Nifty Strangle B as one/);
  // back to the picker, split the group again
  await p.click('[data-act=edit]');
  await p.click('.cmp-bar [data-act=split]');
  cs = await chips(p);
  assert.equal(cs.length, 3);
  assert.ok(cs.every(c => !c.group));
  await ctx.close();
});

test('a link can carry a group: ids joined by "."', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/compare?ids=${IDS['Nifty Strangle A']}.${IDS['Nifty Strangle B']},${IDS['Sensex Credit Spread']}`);
  await p.waitForSelector('.verdict');
  const cs = await chips(p);
  assert.equal(cs.length, 2);
  assert.ok(cs[0].group && !cs[1].group);
  await ctx.close();
});
