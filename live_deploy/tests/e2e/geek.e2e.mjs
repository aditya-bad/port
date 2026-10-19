// Geek mode (pages/geek/): the engine card on the Now tab, entering with
// the button or G, the full-screen shell without the app's header, every
// view loading on desktop and phone, and Esc back to the normal page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

const ID = IDS['Nifty Monthly Strangle'];
const VIEWS = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'map', 'cycles', 'adjustments', 'premium', 'entries', 'settings'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const chromeHidden = p => p.evaluate(() => getComputedStyle(document.querySelector('#top')).display === 'none'
  && document.documentElement.hasAttribute('data-geek'));

test('the Now tab shows the engine card and Geek mode opens full screen', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/now`);
  await p.waitForSelector('[data-engine] .er');
  assert.equal(await p.locator('[data-engine] .er').count(), 5, 'five rules, in the strategy\'s order');
  assert.match(await p.locator('[data-engine] .en').innerText(), /EOD check/);
  assert.equal(await chromeHidden(p), false);
  await p.click('.dep-head [data-act="geek"]');
  await p.waitForURL(/\/geek$/);
  await p.waitForSelector('.gk .gk-next');
  assert.equal(await chromeHidden(p), true, 'header and index strip step aside');
  assert.match(await p.locator('.gk-top').innerText(), /Nifty Monthly Strangle/);
  await p.keyboard.press('Escape');
  await p.waitForURL(/\/now$/);
  await p.waitForSelector('.dep-head');
  assert.equal(await chromeHidden(p), false, 'the normal page is back');
  await p.keyboard.press('g');
  await p.waitForURL(/\/geek$/);
  await p.keyboard.press('c');
  await p.waitForURL(/\/geek\/cycles$/);
  await p.waitForSelector('.gk-tbl .tr:not(.th)');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('other strategies have no Geek mode', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${IDS['BankNifty DTT Plain']}/now`);
  await p.waitForSelector('.dep-head');
  assert.equal(await p.locator('[data-act="geek"]').count(), 0);
  await p.keyboard.press('g');
  await p.waitForTimeout(500);
  assert.doesNotMatch(p.url(), /geek/);
  await p.goto(`${BASE}/linck/#/d/${IDS['BankNifty DTT Plain']}/geek`);
  await p.waitForSelector('.gk .empty');
  assert.match(await p.locator('.gk .empty').innerText(), /No Geek mode for this strategy/);
  await ctx.close();
});

for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
  test(`every Geek view loads with no errors or sideways scroll (${label})`, async () => {
    const ctx = await context(b, { width, height: 860 });
    const p = await ctx.newPage();
    for (const v of VIEWS) {
      await p.goto(`${BASE}/linck/#/d/${ID}/geek/${v}`);
      await p.waitForSelector(`.gk-rail a.on[href$="/geek/${v}"]`);
      await p.waitForFunction(() => {
        const el = document.querySelector('#gkView');
        return el && el.children.length && !el.querySelector('.skel');
      }, null, { timeout: 10000 });
      const text = await p.locator('#gkView').innerText();
      assert.doesNotMatch(text, /undefined|NaN|\[object/, `${v}: no broken values`);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(sw <= width, `${v}: page ${sw}px wide at ${width}px`);
    }
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}

test('the views show this strategy\'s own analytics', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/engine`);
  await p.waitForSelector('[data-rule="eod_gap_check"]');
  assert.deepEqual(await p.$$eval('[data-rule]', els => els.map(e => e.dataset.rule)),
    ['contract_expiry_backstop', 'checkpoint_target', 'convergence_stop', 'eod_gap_check', 'continuous_50pct_trigger']);
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/decisions`);
  await p.waitForSelector('.oc.blocked');
  await p.click('[data-act="outcome"][data-k="no_action"]');
  const outcomes = await p.$$eval('#gkView .gk-tbl .tr:not(.th) .oc', els => [...new Set(els.map(e => e.textContent))]);
  assert.deepEqual(outcomes, ['No action'], 'the filter keeps only checks that did nothing');
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycles`);
  await p.waitForSelector('#gkView .gk-tbl .tr:not(.th)');
  const cycles = await p.locator('#gkView .gk-tbl .tr:not(.th)').count();
  assert.equal(cycles, 10, 'nine closed cycles and the open one');
  assert.match(await p.locator('#gkView').innerText(), /Convergence stop/);
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/adjustments`);
  await p.waitForSelector('#gkView .gk-tbl .tr:not(.th)');
  assert.match(await p.locator('.gk-tiles').innerText(), /of 9/, 'every roll is scored against holding');
  await ctx.close();
});
