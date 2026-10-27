// The Repair page (pages/repair.js): the day check and what the deployments
// reported, and the health monitor's open alerts (app/ops_health.py) only
// while one is open. The monitor itself runs in the background; nothing on
// the page says it checks every minute, and there's no status row, list of
// what's watched, Check now, or closed issues. The header's Repair link
// counts the open alerts. The server seeds two open alerts (one critical)
// and one cleared, and its monitor loop does nothing (tests/e2e/serve.py:
// seed_ops).
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, api, until } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('on a phone the page fits, and More says how many alerts are open', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.ops-issue');
  const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(over <= 1, `no sideways scroll (${over}px)`);
  await p.click('#bnav [data-act="more"]');
  await p.waitForSelector('text=Repair · 2 open alerts');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the page is the day check and the reports; open alerts on top, worst first; no minute-by-minute monitor', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  const badge = p.locator('.nav a[href$="#/repair"] .nav-badge');
  await badge.waitFor();
  assert.equal((await badge.innerText()).trim(), '2');
  assert.ok(await badge.evaluate(el => el.classList.contains('hot')), 'red: a critical one is open');
  await p.click('.nav a[href$="#/repair"]');
  await p.waitForSelector('.ops-issue');
  await p.waitForSelector('.dc');
  const cards = p.locator('.ops-issue');
  assert.equal(await cards.count(), 2);
  assert.match(await cards.nth(0).innerText(), /critical[\s\S]*Kite isn't connected/i);
  assert.match(await cards.nth(1).innerText(), /warning[\s\S]*haven't traded today[\s\S]*Open deployment/i);
  assert.match(await cards.nth(0).innerText(), /14 alerts/);
  const text = await p.locator('#main').innerText();
  // the sections, in this order: open alerts, the day check, the reports
  assert.deepEqual(await p.locator('#main .sect h3').allInnerTexts(), ['Open alerts', 'Day check', 'Reported by the deployments']);
  assert.match(text, /checked once after the 17:00 export/i);
  // the monitor's own parts are gone
  // (a deployment's own report may say it retries every minute; the monitor's lines are what must go)
  assert.doesNotMatch(text, /Checked [\d:]+ · every minute|Checked every minute|Everything the monitor watches/i, 'nothing says the page checks every minute');
  assert.doesNotMatch(text, /What’s watched|Recently closed|Check now/i);
  assert.equal(await p.locator('.ops-stats, .ops-watched, .ops-cleared, [data-act="check"]').count(), 0);
  assert.doesNotMatch(text, /can’t start/, 'the old wording is gone');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('snooze quiets an alert but keeps it; dismiss closes it, the count follows, and with none open the block goes', async () => {
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
  const badge = p.locator('.nav a[href$="#/repair"] .nav-badge');
  await until(async () => (await badge.innerText()).trim() === '1');
  const closed = (await api(ctx, '/v2/ops')).recent;
  assert.ok(closed.find(x => /haven't traded today/.test(x.title) && x.resolved_by === 'dismissed'), 'kept as dismissed on the server');
  // the last one: the block disappears, the day check and reports stay
  await p.locator('.ops-issue [data-act="dismiss"]').click();
  await until(async () => (await p.locator('.ops-issue').count()) === 0);
  await until(async () => !/Open alerts/i.test(await p.locator('#main').innerText()));
  assert.match(await p.locator('#main').innerText(), /Day check[\s\S]*Reported by the deployments/i);
  await until(async () => (await badge.count()) === 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
