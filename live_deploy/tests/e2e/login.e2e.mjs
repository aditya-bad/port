// Signing in: Linck's own sign-in, the Connect Kite step, and classic's.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, PASSWORD, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const PRE_OPEN_FRIDAY = new Date('2026-10-16T08:40:00+05:30');
const SATURDAY = new Date('2026-10-17T11:00:00+05:30');

for (const [width, label, way] of [[1440, 'desktop', 'skip'], [390, 'phone', 'holiday']]) {
  test(`Linck sign-in and Connect Kite on a market day (${label})`, async () => {
    const ctx = await context(b, { width, height: 860, signedIn: false });
    const p = await ctx.newPage();
    await p.clock.setFixedTime(PRE_OPEN_FRIDAY);
    await p.goto(`${BASE}/linck/#/deployments`);
    await p.waitForSelector('[name=user]');
    await p.fill('[name=user]', 'admin');
    await p.fill('[name=pw]', 'wrong');
    await p.click('button[type=submit]');
    await p.waitForSelector('.auth-err');
    assert.match(await p.textContent('.auth-err'), /password|username|sign/i);
    await p.fill('[name=pw]', PASSWORD);
    await p.click('button[type=submit]');
    await p.waitForSelector('[data-a=skip], [data-a=holiday]');
    assert.match(await p.textContent('.auth-title'), /Kite/i, 'asks to connect Kite after signing in');
    if (way === 'skip') {
      await p.click('[data-a=paste]');
      await p.fill('[name=token]', 'not-a-real-token');
      await p.click('form button[type=submit]');
      await p.waitForSelector('.auth-err');
      assert.match(await p.textContent('.auth-err'), /\S/, 'a rejected token says why');
    }
    await p.click(`[data-a=${way}]`);
    await p.waitForSelector('.hdr', { timeout: 8000 });
    assert.match(p.url(), /\/linck\/#\/deployments/, 'lands where it was going');
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}

test('on a weekend, sign-in goes straight in without asking for Kite', async () => {
  const ctx = await context(b, { signedIn: false });
  const p = await ctx.newPage();
  await p.clock.setFixedTime(SATURDAY);
  await p.goto(`${BASE}/linck/`);
  await p.fill('[name=user]', 'admin');
  await p.fill('[name=pw]', PASSWORD);
  await p.click('button[type=submit]');
  await p.waitForSelector('.hdr', { timeout: 8000 });
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the market countdown runs to the second', async () => {
  const ctx = await context(b, { signedIn: false });
  const p = await ctx.newPage();
  await p.clock.install({ time: new Date('2026-10-16T12:40:05+05:30') });   // Friday, market open
  await p.goto(`${BASE}/linck/`);
  await p.waitForSelector('.mk-big');
  assert.equal(await p.textContent('.mk-big'), '2h 49m 55s');
  await p.clock.runFor(3000);
  assert.equal(await p.textContent('.mk-big'), '2h 49m 52s', 'ticks every second');
  await p.clock.setFixedTime(new Date('2026-10-16T08:40:00+05:30'));
  await ctx.close();
});

test('signed out on a fresh device (or an app installed before Linck): Linck sign-in, no classic colours', async () => {
  const ctx = await context(b, { signedIn: false });
  const p = await ctx.newPage();
  const seen = [];
  p.on('framenavigated', f => { if (f === p.mainFrame()) seen.push(f.url()); });
  await p.goto(`${BASE}/`);
  await p.waitForURL(/\/linck\//);
  await p.waitForSelector('input[type=password]');
  assert.equal(await p.getAttribute('meta[name=theme-color]', 'content'), '#f6f5f0');
  assert.match(await p.title(), /Linck/);
  assert.equal((await ctx.request.get(`${BASE}/manifest.json`).then(r => r.json())).start_url, '/linck/');
  await ctx.close();
});

test('classic sign-in still works once Classic view is chosen', async () => {
  const ctx = await context(b, { signedIn: false });
  await ctx.addInitScript(() => { try { if (!localStorage.getItem('uiExperience')) localStorage.setItem('uiExperience', 'classic'); } catch (e) { /* about:blank */ } });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/`);
  await p.fill('input[type=password]', PASSWORD);
  await p.fill('input[name=username], #username', 'admin').catch(() => {});
  await Promise.all([p.waitForLoadState('load'), p.keyboard.press('Enter')]);
  await p.waitForSelector('.ux-linck-btn', { timeout: 8000 });
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('signed out, data and app code stay private', async () => {
  const ctx = await context(b, { signedIn: false });
  for (const path of ['/deployments', '/v2/book', '/linck/js/app.js']) {
    assert.equal((await ctx.request.get(BASE + path)).status(), 401, path);
  }
  assert.equal((await ctx.request.get(`${BASE}/manifest.json`)).status(), 200);
  await ctx.close();
});
