// Turning kinds of alert off (data/alert_types.js, ui/alertprefs.js): on
// a deployment's Settings tab for that one, and on its strategy's page for
// every deployment of it. Kept on the server (alerts_off), so it holds on
// a reload. Uses BankNifty DTT Advanced, the only one of its strategy and
// one no other spec's alerts depend on; it's put back after.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api, until } from './helper.mjs';

const NAME = 'BankNifty DTT Advanced';
let b;
test.before(async () => { b = await browser(); });
test.after(async () => {
  const ctx = await context(b);
  await ctx.request.patch(`${BASE}/deployments/${IDS[NAME]}`, { data: { alerts_off: [] } });
  await ctx.close();
  await b.close();
});

const sw = (p, kind) => p.locator(`[data-alert-kind="${kind}"] [role="switch"]`);
const offOf = async ctx => (await api(ctx, `/deployments/${IDS[NAME]}`)).alerts_off;

test('a deployment’s Settings tab turns one kind off, and it stays off', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${IDS[NAME]}/settings`);
  await p.waitForSelector('[data-alert-prefs]');
  const text = (await p.innerText('[data-alert-prefs]')).toLowerCase();   // group names are set in capitals
  for (const s of ['Trading', 'Entries and exits', 'How it’s doing', 'Deepest drawdown yet', 'VIX spike', 'Always on', 'Isn’t running']) assert.ok(text.includes(s.toLowerCase()), s);
  assert.equal(await sw(p, 'drawdown').getAttribute('aria-checked'), 'true');
  await sw(p, 'drawdown').click();
  await until(async () => (await offOf(ctx)).includes('drawdown'));
  await until(async () => (await sw(p, 'drawdown').getAttribute('aria-checked')) === 'false');
  assert.equal(await sw(p, 'win_rate').getAttribute('aria-checked'), 'true', 'only that one');
  await p.reload();
  await p.waitForSelector('[data-alert-prefs]');
  assert.equal(await sw(p, 'drawdown').getAttribute('aria-checked'), 'false', 'kept on the server');
  assert.equal(await p.locator('[data-alert-prefs] [role="switch"]').count(), 13, 'the always-on ones have no switch');
  await ctx.close();
});

test('the strategy’s page sets a kind for its deployments', async () => {
  const ctx = await context(b);
  await ctx.request.patch(`${BASE}/deployments/${IDS[NAME]}`, { data: { alerts_off: ['drawdown'] } });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/s/intraday_dtt_advanced`);
  await p.waitForSelector('[data-alert-prefs]');
  assert.match(await p.innerText('#main'), /Alerts\s*which ones its deployment raise/);
  assert.equal(await sw(p, 'drawdown').getAttribute('aria-checked'), 'false', 'what the deployment has');
  await sw(p, 'drawdown').click();
  await until(async () => (await offOf(ctx)).length === 0);
  await sw(p, 'trades').click();
  await until(async () => (await offOf(ctx)).join() === 'trades');
  await until(async () => (await sw(p, 'trades').getAttribute('aria-checked')) === 'false');
  await ctx.close();
});
