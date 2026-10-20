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
    // the cycle page isn't in the menu: Cycles stays highlighted on it
    for (const [v, on] of [...VIEWS.map(x => [x, x]), ['cycle?c=9', 'cycles'], ['cycle', 'cycles']]) {
      await p.goto(`${BASE}/linck/#/d/${ID}/geek/${v}`);
      await p.waitForSelector(`.gk-rail a.on[href$="/geek/${on}"]`);
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
  await p.waitForSelector('svg[aria-label^="Smaller side over bigger side"]', { timeout: 8000 });
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

test('geek mode keeps the chosen theme', async () => {
  for (const theme of ['paper', 'slate', 'night']) {
    const ctx = await context(b);
    await ctx.addInitScript(t => localStorage.setItem('linck-prefs', JSON.stringify({ theme: t })), theme);
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${ID}/now`);
    await p.waitForSelector('.dep-head');
    const normal = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await p.goto(`${BASE}/linck/#/d/${ID}/geek`);
    await p.waitForSelector('.gk .gk-next');
    const geek = await p.evaluate(() => [getComputedStyle(document.querySelector('.gk')).backgroundColor, document.documentElement.dataset.th]);
    assert.deepEqual(geek, [normal, theme], `${theme}: same ground as the normal page`);
    await ctx.close();
  }
});

// One cycle end to end, reached from Cycles (a row or a line) and from
// Adjustments (a roll), with everything about it on one page.
test('a cycle opens end to end from Cycles and from Adjustments', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycles`);
  await p.waitForSelector('.gk-tbl .tr[data-c]');
  // the new charts are there, and every cycle line opens its cycle
  assert.ok(await p.locator('.gk-chart .jr').count() >= 10, 'journeys and ranges, one mark per cycle');
  // every cycle has a journey, including the ones from before prices were stored
  const kinds = await p.locator('.gk-chart .jr[data-kind]').evaluateAll(es => es.map(e => [e.dataset.c, e.dataset.kind]));
  assert.equal(kinds.length, 10, 'all ten cycles drawn');
  assert.deepEqual(Object.fromEntries(kinds.filter(([c]) => +c <= 4)), { 1: 'booked', 2: 'booked', 3: 'candles', 4: 'marks' });
  assert.equal(await p.locator('.jr[data-kind="booked"] path[stroke-dasharray="6 4"]').count(), 2, 'booked-only drawn dashed');
  assert.match(await p.locator('#gkView').innerText(), /no prices stored: steps at what each leg booked/);
  await p.locator('.gk-tbl .tr[data-c="9"]').click();
  await p.waitForURL(/\/geek\/cycle\?c=9$/);
  await p.waitForSelector('.cy-tl .cy-ev');
  const text = await p.locator('#gkView').innerText();
  for (const want of [/Cycle 9/, /Checkpoint/, /P&L through the cycle/, /Side balance/, /Call and put premium/, /Strikes over time/,
    /Everything that happened/, /How it was entered/, /Where the money went/, /Rolls in this cycle/, /Every leg/]) {
    assert.match(text, want);
  }
  assert.doesNotMatch(text, /undefined|NaN|\[object/);
  const evs = await p.locator('.cy-tl .cy-ev').allInnerTexts();
  assert.match(evs[0], /Entry/, 'oldest first: the entry');
  assert.match(evs[evs.length - 1], /Flattened cycle 9/, 'and the exit last');
  assert.ok(await p.locator('.gk-tbl .tr:not(.th)').count() >= 3, 'its legs');
  // previous / next walk the cycles
  await p.click('a.btn:has-text("Next")');
  await p.waitForURL(/cycle\?c=10$/);
  await p.waitForSelector('.gk-h h2:has-text("Cycle 10")');
  assert.match(await p.locator('.gk-h').innerText(), /Running/);
  await p.click('a.btn:has-text("Previous")');
  await p.waitForURL(/cycle\?c=9$/);
  // the price chart, legs and orders are on the page itself: no sheet to open
  await p.waitForSelector('#cySess .ss-plot', { timeout: 15000 });
  assert.equal(await p.locator('.sess').count(), 0, 'no sheet');
  const sess = await p.locator('#cySess').innerText();
  assert.match(sess, /NIFTY and combined P&L[\s\S]*Positions, final status[\s\S]*Orders and events/);
  assert.doesNotMatch(sess, /CYCLE · /, 'the page has its own header');
  // cycles count oldest first: cycle 9 of 10
  assert.match(await p.locator('#gkView .row.wrap').first().innerText(), /9 of 10/);
  // the header button jumps to it
  await p.evaluate(() => window.scrollTo(0, 0));
  await p.click('[data-act=toSess]');
  await p.waitForFunction(() => { const r = document.querySelector('#cySess').getBoundingClientRect(); return r.top < innerHeight / 2 && r.top > -40; });
  // a worst point still in profit isn't red
  const worst = p.locator('#cySess .ss-stats > div', { hasText: 'Worst' }).locator('b');
  const wv = await worst.innerText();
  assert.equal(await worst.evaluate(e => e.classList.contains('neg')), wv.trim().startsWith('−'), `worst ${wv}`);
  // from Adjustments: a roll opens its own cycle
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/adjustments`);
  await p.waitForSelector('.gk-tbl .tr[data-c]');
  assert.match(await p.locator('#gkView').innerText(), /What rolling has earned[\s\S]*Balance when it rolled vs what it paid[\s\S]*By side and direction[\s\S]*By hour of day/);
  const c = await p.locator('.gk-tbl .tr[data-c]').first().getAttribute('data-c');
  await p.locator('.gk-tbl .tr[data-c]').first().click();
  await p.waitForURL(new RegExp(`cycle\\?c=${c}$`));
  await p.waitForSelector(`.gk-h h2:has-text("Cycle ${c}")`);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('an unknown cycle says so and links back', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycle?c=999`);
  await p.waitForSelector('#gkView a[href$="/geek/cycles"]');
  assert.match(await p.locator('#gkView').innerText(), /There’s no cycle 999/);
  await ctx.close();
});

test('a cycle from before prices were stored still shows its P&L', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycle?c=1`);
  await p.waitForSelector('.gk-h h2:has-text("Cycle 1")');
  const text = await p.locator('#gkView').innerText();
  assert.match(text, /no prices were stored for this cycle: steps at what each leg booked/);
  assert.match(text, /no prices stored for this cycle/, 'no dip or peak claimed');
  assert.ok(await p.locator('.gk-chart path[stroke-dasharray="6 4"]').count() >= 1, 'the booked steps are drawn');
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycle?c=3`);
  await p.waitForSelector('.gk-h h2:has-text("Cycle 3")');
  assert.match(await p.locator('#gkView').innerText(), /15-minute candle close/);
  assert.doesNotMatch(await p.locator('#gkView').innerText(), /Appears after/, 'drawn from the candles');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
