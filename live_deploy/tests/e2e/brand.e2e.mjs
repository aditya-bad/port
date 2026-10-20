// The Linck mark where people see it: boot screen, header, sign-in,
// Geek mode bar, the loaders and the tab icon.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const loaded = (p, sel) => p.$eval(sel, img => img.complete && img.naturalWidth > 0);

test('boot screen shows the mark, then gets out of the way', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.route('**/linck/js/pages/desk.js', async r => { await new Promise(res => setTimeout(res, 600)); await r.continue(); });
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('#boot .boot-mark .candle');
  assert.ok(await p.isVisible('#boot'), 'the boot screen covers the first paint');
  await p.waitForSelector('#boot', { state: 'detached', timeout: 10000 });
  await p.waitForSelector('.hdr');
  assert.ok(await p.isVisible('.hdr .wordmark img.mark'));
  assert.ok(await loaded(p, '.hdr .wordmark img.mark'), 'header mark loads');
  const fav = await p.$$eval('link[rel=icon]', ls => ls.map(l => l.getAttribute('href')));
  assert.ok(fav.includes('/icons/linck-mark.svg'));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a page that fails to load still clears the boot screen', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.route('**/linck/js/pages/leaderboard.js', r => r.abort());
  await p.goto(`${BASE}/linck/#/leaderboard`);
  await p.waitForSelector('#boot', { state: 'detached', timeout: 10000 });
  assert.match(await p.textContent('#main'), /didn’t load/);
  await ctx.close();
});

for (const th of ['paper', 'slate', 'night']) {
  test(`mark in the header, boot screen and sign-in (${th})`, async () => {
    const ctx = await context(b, { signedIn: false });
    await ctx.addInitScript(t => localStorage.setItem('linck-prefs', JSON.stringify({ theme: t })), th);
    let p = await ctx.newPage();
    await p.goto(`${BASE}/linck/`);
    await p.waitForSelector('.auth-mark');
    const [show, hide] = th === 'night' ? ['img.mark-paper', 'img.mark:not(.mark-paper)'] : ['img.mark:not(.mark-paper)', 'img.mark-paper'];
    assert.ok(await p.isVisible(`.auth-mark ${show}`) && !(await p.isVisible(`.auth-mark ${hide}`)), `sign-in shows ${show} on ${th}`);
    assert.ok(await loaded(p, `.auth-mark ${show}`), 'sign-in mark loads');
    const box = await p.$eval(`.auth-mark ${show}`, i => i.getBoundingClientRect().width);
    assert.equal(Math.round(box), 32);
    await ctx.close();
    // signed in, in a fresh context: the sign-in page installed the service
    // worker, which would serve desk.js past the route below
    const ctx2 = await context(b);
    await ctx2.addInitScript(t => localStorage.setItem('linck-prefs', JSON.stringify({ theme: t })), th);
    p = await ctx2.newPage();
    await p.route('**/linck/js/pages/desk.js', async r => { await new Promise(res => setTimeout(res, 3000)); await r.continue(); });
    await p.goto(`${BASE}/linck/?b=${th}#/desk`);
    // one read while the boot screen is up (it leaves once the page renders)
    const tile = await (await p.waitForFunction(() => {
      const t = document.querySelector('#boot:not(.done) #tile');
      return t && getComputedStyle(t).fill;
    })).jsonValue();
    assert.equal(tile, th === 'night' ? 'rgb(246, 244, 238)' : 'rgb(29, 27, 22)', 'boot mark tile');
    await p.waitForSelector('.hdr');
    assert.ok(await p.isVisible(`.hdr ${show}`) && !(await p.isVisible(`.hdr ${hide}`)), `header shows ${show} on ${th}`);
    assert.ok(await loaded(p, `.hdr ${show}`));
    await ctx2.close();
  });
}

test('Geek mode bar carries the mark', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${IDS['Nifty Monthly Strangle']}/geek`);
  await p.waitForSelector('.gk-top .gk-mark');
  assert.ok(await loaded(p, '.gk-top .gk-mark:not(.mark-paper)'));
  assert.ok(!(await p.isVisible('.gk-top .mark-paper')), 'paper mark only on Night');
  await ctx.close();
});

test('loaders are the prompt with a blinking cursor', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('.hdr');
  const s = await p.evaluate(() => {
    const el = document.createElement('span'); el.className = 'spinner'; document.body.append(el);
    const before = getComputedStyle(el, '::before'), after = getComputedStyle(el, '::after');
    return { mask: before.maskImage || before.webkitMaskImage, blink: after.animationName, w: el.getBoundingClientRect().width };
  });
  assert.match(s.mask, /svg/);
  assert.equal(s.blink, 'blink');
  assert.ok(s.w > 15, 'prompt and cursor side by side');
  await ctx.close();
});
