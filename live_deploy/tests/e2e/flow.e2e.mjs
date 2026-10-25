// The everyday flows: open Linck from classic, pause and undo, close a
// deployment's legs, create one with the wizard, return to classic.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api, dep, until } from './helper.mjs';

let b, ctx, p;
test.before(async () => { b = await browser(); ctx = await context(b); p = await ctx.newPage(); });
test.after(async () => { await b.close(); });

test('a fresh device opens Linck, not classic', async () => {
  await p.goto(`${BASE}/`);
  await p.waitForURL(/\/linck\//);
  await p.waitForSelector('.hdr .wordmark');
});

test('classic → Linck desk', async () => {
  await p.evaluate(() => localStorage.setItem('uiExperience', 'classic'));   // chose Classic view
  await p.goto(`${BASE}/`);
  await p.click('.ux-linck-btn');
  await p.waitForURL(/\/linck\//);
  await p.waitForSelector('.lane', { timeout: 8000 });
  assert.ok(await p.locator('.lane').count() >= 5, 'a lane per running deployment');
});

test('pause, then undo', async () => {
  const id = IDS['BankNifty DTT Plain'];
  await p.goto(`${BASE}/linck/#/d/${id}`);
  await p.waitForSelector('[data-act=primary]');
  // a test server starved of CPU (the gate runs everything at once) can
  // briefly show its runner as not running: Restart / Review, not Pause
  const label = await until(async () => {
    const t = await p.textContent('[data-act=primary]').catch(() => null);
    return t === 'Pause' ? t : null;
  }, 45000) || await p.textContent('[data-act=primary]');
  assert.equal(label, 'Pause', 'the primary action of a running deployment');
  await p.click('[data-act=primary]');
  const got = await until(async () => (await dep(ctx, 'BankNifty DTT Plain')).status === 'paused' && 'paused');
  assert.equal(got, 'paused', `after clicking ${label}: ${JSON.stringify(await dep(ctx, 'BankNifty DTT Plain'))?.slice(0, 300)}`);
  await p.click('.toast [data-undo]');
  assert.equal(await until(async () => (await dep(ctx, 'BankNifty DTT Plain')).status === 'active' && 'active'), 'active');
});

test('close positions lists every exit order and keeps it running', async () => {
  const id = IDS['BankNifty DTT Plain'];
  await p.goto(`${BASE}/linck/#/d/${id}`);
  await p.waitForSelector('.dep-head [data-act=close]');
  await p.click('.dep-head [data-act=close]');
  await p.waitForSelector('.dialog .orders > div');
  assert.equal(await p.locator('.dialog .orders > div').count(), 2);
  await p.click('.dialog [data-ok]');
  const open = await until(async () => {
    const n = (await api(ctx, '/positions?status=open')).filter(x => x.deployment_id === id).length;
    return n === 0 ? 'flat' : null;
  });
  assert.equal(open, 'flat');
  assert.equal((await dep(ctx, 'BankNifty DTT Plain')).status, 'active');
});

test('wizard creates a paused deployment on the chosen index', async () => {
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.waitForSelector('[data-act=new]');
  await p.click('[data-act=new]');
  await p.click('[data-strat=strangle_monthly_v2]');
  await p.fill('[data-w=name]', 'E2E Strangle');
  await p.click('[data-idx=BANKNIFTY]');
  for (let i = 0; i < 3; i++) { await p.click('[data-next]'); await p.waitForTimeout(250); }
  const d = await until(() => dep(ctx, 'E2E Strangle'), 8000);
  assert.ok(d, 'created');
  assert.equal(d.status, 'paused');
  assert.equal(d.config.instrument, 'BANKNIFTY');
  assert.equal(d.config.instrument_tokens[0], 260105);
  await p.waitForURL(/#\/d\//);
});

test('back to classic, and it sticks', async () => {
  await p.goto(`${BASE}/linck/#/account?s=appearance`);
  await p.waitForSelector('[data-act=classic]');
  await p.click('[data-act=classic]');
  await p.waitForURL(u => !/linck/.test(u.toString()));
  await p.goto(`${BASE}/`);
  await p.waitForTimeout(800);
  assert.ok(!/linck/.test(p.url()));
  assert.deepEqual(ctx.errors, []);
});
