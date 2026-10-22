// The Repair page's health half (pages/repair.js; app/ops_health.py): the
// header's Repair link counts the open issues, the page shows what's
// watched and each open issue with snooze and dismiss, and what cleared.
// The server seeds two open issues (one critical) and one cleared, and its
// monitor loop does nothing (tests/e2e/serve.py: seed_ops).
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, api, until } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('on a phone the page fits, and More says how many are open', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.ops-issue');
  const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(over <= 1, `no sideways scroll (${over}px)`);
  await p.click('#bnav [data-act="more"]');
  await p.waitForSelector('text=Repair & health · 2 open');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the header counts the open issues; the page shows them, worst first', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  const badge = p.locator('.nav a[href$="#/repair"] .nav-badge');
  await badge.waitFor();
  assert.equal((await badge.innerText()).trim(), '2');
  assert.ok(await badge.evaluate(el => el.classList.contains('hot')), 'red: a critical one is open');
  await p.click('.nav a[href$="#/repair"]');
  await p.waitForSelector('.ops-issue');
  const cards = p.locator('.ops-issue');
  assert.equal(await cards.count(), 2);
  assert.match(await cards.nth(0).innerText(), /critical[\s\S]*Kite isn't connected/i);
  assert.match(await cards.nth(1).innerText(), /warning[\s\S]*haven't traded today[\s\S]*Open deployment/i);
  assert.match(await cards.nth(0).innerText(), /14 alerts/);
  const stats = await p.locator('.ops-stats').innerText();
  assert.match(stats, /Open issues\s*2\s*1 critical · 1 warning/i);
  assert.match(stats, /Today’s export/i);
  assert.match(await p.locator('.ops-watched').innerText(), /The 15:45 export/);
  assert.match(await p.locator('.ops-cleared').innerText(), /Kite prices stopped[\s\S]*Cleared/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('snooze quiets an issue but keeps it; dismiss closes it and the count follows', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.ops-issue.warning');
  const warn = p.locator('.ops-issue.warning');
  await warn.locator('[data-act="snooze"][data-p="30m"]').click();
  await p.waitForSelector('.ops-issue.warning.zz');
  assert.match(await p.locator('.ops-issue.warning').innerText(), /Snoozed until/);
  const { open } = await api(ctx, '/v2/ops');
  assert.ok(open.find(x => x.severity === 'warning').snoozed_until, 'stored');
  await p.locator('.ops-issue.warning [data-act="snooze"][data-p="clear"]').click();
  await p.waitForSelector('.ops-issue.warning:not(.zz)');
  await p.locator('.ops-issue.warning [data-act="dismiss"]').click();
  await until(async () => (await p.locator('.ops-issue').count()) === 1);
  assert.match(await p.locator('.ops-cleared').innerText(), /haven't traded today[\s\S]*Dismissed/);
  const badge = p.locator('.nav a[href$="#/repair"] .nav-badge');
  await until(async () => (await badge.innerText()).trim() === '1');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
