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
  await p.waitForSelector('.ov-card[data-sec="history"] .ov-state:not(:text-is("…"))');
  assert.deepEqual(await p.$$eval('.ov-card', cs => cs.map(c => c.dataset.sec)), SECTIONS);
  assert.deepEqual(await p.$$eval('.ov-grp > h3', hs => hs.map(h => h.textContent)), ['Trading', 'You', 'Workspace', 'Danger zone']);
  // every card says what the section is for and what state it is in
  for (const c of await p.$$('.ov-card')) {
    assert.ok((await c.$eval('.ov-what', e => e.textContent.trim())).length > 20);
    assert.ok((await c.$eval('.ov-state', e => e.textContent.trim())).length > 2);
  }
  assert.match(await p.innerText('.ov-card[data-sec="kite"]'), /Not connected|Connected|Connecting/);
  assert.match(await p.innerText('.ov-card[data-sec="history"]'), /Last saved|Saving now|Not run yet/, 'says where the history stands, even before the first run');
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
  // a settings list: name, a short state and a chevron per row; the explanations wait inside each section
  await p.waitForSelector('.ov-card[data-sec="kite"] .ov-short:not(:empty)');
  assert.ok(!(await p.isVisible('.ov-card .ov-what')) && !(await p.isVisible('.ov-card .ov-state')));
  assert.match(await p.innerText('.ov-card[data-sec="kite"] .ov-short'), /^(Not connected|Connected|Connecting)$/);
  assert.match(await p.innerText('.ov-card[data-sec="emergency"] .ov-short'), /^(\d+ open legs?|Nothing open)$/);
  const h = await p.$$eval('.ov-card', cs => cs.map(c => c.getBoundingClientRect().height));
  assert.ok(h.every(x => x < 64), `one-line rows (${h})`);
  // who you are, and what needs you, come first
  assert.ok(await p.isVisible('.ov-me'));
  if (await p.isVisible('.ov-attn [data-act=kite]')) {
    await p.click('.ov-attn [data-act=kite]');
    await p.locator('[role=dialog]').last().waitFor();
    await p.keyboard.press('Escape');
  }
  await p.click('.ov-card[data-sec="notifications"]');
  await p.waitForSelector('.acct-head h2:text-is("Notifications")');
  assert.ok(await p.isVisible('.acct-back'));
  assert.ok(!(await p.isVisible('.page-head')), 'a section shows its own title, not the page header again');
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

test('on a desktop the phone-only pieces stay hidden', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/account`);
  await p.waitForSelector('.ov-card');
  for (const sel of ['.ov-me', '.ov-attns', '.ov-card .ov-short', '.ov-card .ov-chev', '.acct-back']) assert.ok(!(await p.isVisible(sel)), sel);
  assert.ok(await p.isVisible('.ov-card .ov-what') && await p.isVisible('.acct-nav'));
  await ctx.close();
});

test('a section whose data comes late doesn’t paint over the one opened since', async () => {
  // found by this suite: each section's loads ended in a repaint, even after
  // you'd moved on, so a slow one (here the audit log, held 1.5 s) drew
  // itself over the next section (the service worker off, so the hold applies)
  const ctx = await context(b, { blockSW: true });
  const p = await ctx.newPage();
  await p.route('**/auth/audit-log*', async r => { await new Promise(res => setTimeout(res, 1500)); await r.continue(); });
  await p.goto(`${BASE}/linck/#/account?s=log`);
  await p.waitForSelector('.acct-main[data-sec="log"]');
  await p.goto(`${BASE}/linck/#/account?s=server`);
  await p.waitForSelector('.acct-main[data-sec="server"] [data-act=redeploy]');
  await p.waitForTimeout(2200);
  assert.deepEqual(await p.$$eval('.acct-main', els => els.map(e => e.dataset.sec)), ['server']);
  assert.equal(await p.locator('.acct-main .audit').count(), 0, 'the log didn’t come back');
  assert.equal(await p.locator('.acct-main [data-act=redeploy]').count(), 1);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
