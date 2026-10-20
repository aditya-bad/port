// Account: an overview with every area's state, one section per page with
// what it's for, the emergency stop moved here out of the header, and
// risky actions kept to their own sections.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const SECTIONS = ['kite', 'emergency', 'appearance', 'notifications', 'security', 'app', 'users', 'tags', 'history', 'log', 'server'];

test('the header has no Close everything button; Account and More have it', async () => {
  for (const width of [1920, 1440, 1100]) {
    const ctx = await context(b, { width, height: 900 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/desk`);
    await p.waitForSelector('.hdr .wordmark');
    assert.equal(await p.locator('.hdr [data-act=closeAll]').count(), 0, `not in the header at ${width}px`);
    assert.doesNotMatch(await p.locator('.hdr').innerText(), /Close everything/);
    await ctx.close();
  }
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.click('#bnav [data-act=more]');
  assert.match(await p.locator('.dialog, [role=dialog]').first().innerText(), /Close everything/, 'still two taps away on a phone');
  await ctx.close();
});

test('the overview shows every section with its state, and each card opens it', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/account`);
  await p.waitForSelector('.ov-card[data-sec="log"]:not(:has-text("…"))');
  assert.deepEqual(await p.$$eval('.ov-card', cs => cs.map(c => c.dataset.sec)), SECTIONS);
  assert.deepEqual(await p.$$eval('.ov-grp > h3', hs => hs.map(h => h.textContent)), ['Trading', 'You', 'Workspace', 'Danger zone']);
  // every card says what the section is for and what state it is in
  for (const c of await p.$$('.ov-card')) {
    assert.ok((await c.$eval('.ov-what', e => e.textContent.trim())).length > 20);
    assert.ok((await c.$eval('.ov-state', e => e.textContent.trim())).length > 2);
  }
  assert.match(await p.innerText('.ov-card[data-sec="kite"]'), /Not connected|Connected|Connecting/);
  assert.match(await p.innerText('.ov-card[data-sec="emergency"]'), /open legs? in \d+ deployments?|No open positions/);
  await p.click('.ov-card[data-sec="tags"]');
  await p.waitForURL(/#\/account\?s=tags$/);
  await p.waitForSelector('.acct-head h2:text-is("Tags")');
  assert.equal(await p.getAttribute('.acct-nav a.on', 'href'), '#/account?s=tags');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('every section has a title, says what it is for, and renders cleanly', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  for (const s of SECTIONS) {
    await p.goto(`${BASE}/linck/#/account?s=${s}`);
    await p.waitForSelector(`.acct-main[data-sec="${s}"] .acct-head p`);
    await p.waitForTimeout(300);
    const t = await p.innerText('.acct-main');
    assert.doesNotMatch(t, /undefined|NaN|\[object/, s);
    assert.ok((await p.innerText('.acct-head p')).length > 30, `${s} explains itself`);
    assert.equal(await p.locator('.acct-nav a[aria-current=page]').count(), 1);
  }
  // the risky actions live only in their own sections
  for (const [act, home] of [['closeAll', 'emergency'], ['redeploy', 'server'], ['clearAll', 'server']]) {
    for (const s of SECTIONS) {
      await p.goto(`${BASE}/linck/#/account?s=${s}`);
      await p.waitForSelector(`.acct-main[data-sec="${s}"]`);
      assert.equal(await p.locator(`.acct-main [data-act=${act}]`).count(), s === home ? 1 : 0, `${act} on ${s}`);
    }
  }
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('emergency stop shows what it would close, then asks for CLOSE ALL', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/account?s=emergency`);
  await p.waitForSelector('.em-tile');
  assert.deepEqual(await p.$$eval('.em-tile span', s => s.map(x => x.textContent)), ['Open legs', 'Deployments holding them', 'Running deployments', 'Would book at current prices']);
  const legs = Number(await p.innerText('.em-tile:first-child b'));
  assert.ok(legs > 0, 'the seeded deployments hold legs');
  await p.click('.acct-main [data-act=closeAll]');
  const dlg = p.locator('[role=dialog]').last();
  await dlg.waitFor();
  assert.match(await dlg.innerText(), /Close every open position, everywhere[\s\S]*Type CLOSE ALL to arm/);
  const ok = dlg.locator('button', { hasText: /Close \d+ legs? now/ });
  assert.ok(await ok.isDisabled(), 'not armed until CLOSE ALL is typed');
  await p.keyboard.press('Escape');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone: the overview is the menu, and sections have a way back', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/account`);
  await p.waitForSelector('.ov-card');
  assert.ok(!(await p.isVisible('.acct-nav')), 'no side list on a phone');
  await p.click('.ov-card[data-sec="notifications"]');
  await p.waitForSelector('.acct-head h2:text-is("Notifications")');
  assert.ok(await p.isVisible('.acct-back'));
  await p.click('.acct-back');
  await p.waitForURL(/#\/account$/);
  await p.waitForSelector('.ov-card');
  assert.equal(await p.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the market holiday mute lives in one place: Notifications', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  for (const s of SECTIONS) {
    await p.goto(`${BASE}/linck/#/account?s=${s}`);
    await p.waitForSelector(`.acct-main[data-sec="${s}"]`);
    await p.waitForTimeout(200);
    assert.equal(await p.locator('.acct-main [data-act=mute]').count(), s === 'notifications' ? 1 : 0, s);
  }
  await ctx.close();
});
