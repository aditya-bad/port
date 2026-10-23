// One strategy's page: every deployment of it combined, any of them left
// out, each side by side, and the settings each uses.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

// the seeded ones; other specs on the same server may add more (flow.e2e creates one)
const SEEDED = ['Nifty Strangle A', 'Nifty Strangle B', 'Nifty Monthly Strangle', 'Bankex Strangle'];
const capOf = async p => p.innerText('.st-pick > .row .mute');
const strangles = async ctx => (await api(ctx, '/deployments')).filter(d => d.strategy_name === 'strangle_monthly_v2');

test('reached from Strategies and from a deployment; every deployment of it is in view by default', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/strategies`);
  await p.click('a.str-link:text-is("Monthly strangle")');
  await p.waitForURL(/#\/s\/strangle_monthly_v2$/);
  await p.waitForSelector('.pick-chip');
  assert.equal(await p.innerText('.dep-title'), 'Monthly strangle');
  const D = await strangles(ctx);
  const chips = await p.$$eval('.pick-chip', cs => cs.map(c => c.textContent.trim()));
  assert.deepEqual([...chips].sort(), D.map(d => d.deployment_name).sort(), 'every deployment of it, nothing else');
  assert.ok(SEEDED.every(n => chips.includes(n)));
  assert.ok(await p.$$eval('.pick-chip', cs => cs.every(c => c.getAttribute('aria-checked') === 'true')), 'all in view');
  assert.match(await p.innerText('.st-pick'), new RegExp(`All ${D.length} deployments`));
  assert.match(await capOf(p), /of capital, combined as one/);
  // the deployment page links back to its strategy
  await p.goto(`${BASE}/linck/#/d/${IDS['Nifty Strangle A']}/now`);
  await p.click('.dep-head a[href="#/s/strangle_monthly_v2"]');
  await p.waitForSelector('.pick-chip');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('leaving deployments out changes every figure, and is remembered', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/s/strangle_monthly_v2`);
  await p.waitForSelector('.pick-chip');
  const n = (await strangles(ctx)).length;
  const before = { cap: await capOf(p), trades: await p.innerText('.st-tbl .tr.tot') };
  await p.click('.pick-chip:has-text("Nifty Strangle A")');
  await p.waitForSelector('.pick-chip:has-text("Nifty Strangle A")[aria-checked="false"]');
  assert.match(await p.innerText('.st-pick'), new RegExp(`${n - 1} of ${n} deployments`));
  assert.notEqual(await capOf(p), before.cap, 'its 10L of capital is out');
  assert.notEqual(await p.innerText('.st-tbl .tr.tot'), before.trades, 'the combined row follows');
  assert.match(await p.innerText('.st-tbl .tr.tot'), new RegExp(`^${n - 1} in view, combined`));
  assert.equal(await p.locator('.st-tbl .tr.out').count(), 1, 'its row is dimmed, still listed');
  // the table's box does the same
  await p.click('.st-tbl .tr.out .box');
  await p.waitForSelector('.pick-chip:has-text("Nifty Strangle A")[aria-checked="true"]');
  // quick pick: Running leaves the paused one out
  await p.click('.st-pick [data-act=preset][data-k=active]');
  await p.waitForSelector('.pick-chip:has-text("Nifty Strangle B")[aria-checked="false"]');
  assert.equal(await p.getAttribute('.st-pick [data-k=active]', 'aria-pressed'), 'true');
  // remembered on the next visit
  await p.reload();
  await p.waitForSelector('.pick-chip');
  assert.equal(await p.getAttribute('.pick-chip:has-text("Nifty Strangle B")', 'aria-checked'), 'false');
  // the last one in view can't be left out
  for (const c of await p.$$('.pick-chip')) {
    if (await c.getAttribute('aria-checked') === 'true') await (await p.$(`.pick-chip[data-id="${await c.getAttribute('data-id')}"]`)).click();
  }
  assert.ok((await p.$$eval('.pick-chip', cs => cs.filter(c => c.getAttribute('aria-checked') === 'true').length)) === 1);
  await p.click('.st-pick [data-act=preset][data-k=all]');
  await p.waitForFunction(() => [...document.querySelectorAll('.pick-chip')].every(c => c.getAttribute('aria-checked') === 'true'));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the whole picture: strip, equity, each deployment, risk, exits and settings', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/s/strangle_monthly_v2`);
  await p.waitForSelector('.st-strip');
  assert.deepEqual(await p.$$eval('.st-strip .stat .l', s => s.map(x => x.textContent)),
    ['Overall P&L', 'Cycle', 'Unrealized', 'Return on capital', 'Return / drawdown', 'Win rate']);
  const text = await p.innerText('#main');
  for (const want of [/Equity, combined/, /Deployments/, /Risk and quality/, /How trades ended/, /What stands out/, /Settings in use/]) assert.match(text, want);
  assert.doesNotMatch(text, /undefined|NaN|\[object/);
  assert.equal(await p.locator('.st-tbl .tr:not(.th):not(.tot)').count(), (await strangles(ctx)).length);
  // each deployment's line on the equity chart
  const lines = async () => p.$$eval('svg[data-scrub=steq] path[stroke]', ps => ps.length);
  const n0 = await lines();
  await p.click('[data-act=each]');
  await p.waitForFunction(n => document.querySelectorAll('svg[data-scrub=steq] path[stroke]').length > n, n0);
  // settings: the ones a deployment changed, then all of them
  const changed = await p.locator('.st-set .tr.chg').count();
  assert.ok(changed >= 1, 'the seeded deployments differ somewhere');
  assert.equal(await p.locator('.st-set .tr:not(.th)').count(), changed, 'changed only, by default');
  await p.click('[data-act=setAll][data-v=all]');
  await p.waitForFunction(c => document.querySelectorAll('.st-set .tr:not(.th)').length > c, changed);
  // a deployment's row opens its page
  await p.click('.st-tbl a.cell-name:has-text("Nifty Strangle B")');
  await p.waitForURL(new RegExp(`#/d/${IDS['Nifty Strangle B']}`));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('compare and deploy from here', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/s/strangle_monthly_v2`);
  await p.waitForSelector('.pick-chip');
  // the seeded four (another spec may have added one)
  for (const d of await strangles(ctx)) if (!SEEDED.includes(d.deployment_name)) await p.click(`.pick-chip[data-id="${d.id}"]`);
  await p.click('[data-act=compare]');
  await p.waitForURL(/#\/compare/);
  await p.waitForSelector('.verdict');
  assert.equal(await p.locator('.cmp-chip:not(.dashed)').count(), 4, 'the four in view, side by side');
  await p.goto(`${BASE}/linck/#/s/strangle_monthly_v2`);
  await p.waitForSelector('[data-act=deploy]');
  await p.click('.dep-head [data-act=deploy]');
  await p.waitForSelector('.wiz-steps');
  assert.match(await p.innerText('.wiz-steps'), /Monthly strangle|Name/i, 'the wizard opens with this strategy');
  await ctx.close();
});

test('a strategy nobody runs yet: what it is, its defaults and a way to deploy', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/s/calendar_btst`);
  await p.waitForSelector('.dep-title');
  assert.match(await p.innerText('#main'), /Not deployed yet[\s\S]*Settings/);
  await p.click('[data-act=setAll][data-v=all]');
  await p.waitForSelector('.st-set .tr:not(.th)');
  assert.equal(await p.locator('.st-pick, .st-strip').count(), 0);
  await p.goto(`${BASE}/linck/#/s/no_such_strategy`);
  await p.waitForSelector('.empty');
  assert.match(await p.innerText('#main'), /No such strategy/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone: cards instead of the table, settings stacked, nothing sideways', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/s/strangle_monthly_v2`);
  await p.waitForSelector('.st-card');
  assert.equal(await p.locator('.st-card').count(), (await strangles(ctx)).length);
  assert.ok(!(await p.isVisible('.st-tbl')));
  await p.click('.st-card .box >> nth=0');
  await p.waitForSelector('.st-card.out');
  assert.equal(await p.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
