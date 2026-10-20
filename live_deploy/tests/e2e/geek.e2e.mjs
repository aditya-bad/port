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

test('every strategy has Geek mode: the shared views, without the strangle\'s own', async () => {
  const DTT = IDS['BankNifty DTT Plain'];
  const SHARED = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'settings'];
  for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
    const ctx = await context(b, { width, height: 860 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${DTT}/now`);
    await p.waitForSelector('.dep-head');
    assert.equal(await p.locator('.dep-head [data-act="geek"]').count(), 1, `${label}: the button is there`);
    assert.equal(await p.locator('[data-engine]').count(), 0, `${label}: the engine card stays a strangle thing`);
    await p.keyboard.press('g');
    await p.waitForURL(/\/geek$/);
    await p.waitForSelector('.gk-rail a.on');
    const menu = await p.$$eval('.gk-rail a', as => as.map(a => a.getAttribute('href').split('/geek/')[1]));
    assert.deepEqual(menu, SHARED, `${label}: the menu`);
    if (width > 800) assert.doesNotMatch(await p.locator('.gk-top').innerText(), /Cycle/, 'no cycle figure');
    await p.keyboard.press('c');
    await p.waitForTimeout(300);
    assert.match(p.url(), /\/geek$/, 'C (Cycles) does nothing here');
    for (const v of SHARED) {
      await p.goto(`${BASE}/linck/#/d/${DTT}/geek/${v}`);
      await p.waitForSelector(`.gk-rail a.on[href$="/geek/${v}"]`);
      await p.waitForFunction(() => {
        const el = document.querySelector('#gkView');
        return el && el.children.length && !el.querySelector('.skel');
      }, null, { timeout: 10000 });
      const text = await p.locator('#gkView').innerText();
      assert.doesNotMatch(text, /undefined|NaN|\[object/, `${label} ${v}: no broken values`);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(sw <= width, `${label} ${v}: page ${sw}px wide at ${width}px`);
      if (v === 'overview') assert.match(text, /Win rate/);
      if (v === 'engine') assert.match(text, /Paused|Stopped|Not running|Flat|Holding/);
      if (v === 'decisions') assert.ok(await p.locator('#gkView .oc.acted').count() > 0, 'its trades, as decisions');
      if (v === 'settings') { assert.match(text, /Lots/); assert.match(text, /Rules that traded/); }
    }
    await p.goto(`${BASE}/linck/#/d/${DTT}/geek/cycles`);
    await p.waitForSelector('.gk-rail a.on');
    assert.match(p.url(), /\/geek\/cycles$/);
    assert.equal(await p.locator('.gk-rail a.on').getAttribute('href').then(h => h.split('/geek/')[1]), 'overview', 'an unknown view falls back to Overview');
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  }
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
  await p.waitForSelector('#gkView .gk-tbl .tr[data-c]');
  const cycles = await p.locator('#gkView .gk-tbl .tr[data-c]').count();   // the cycles table, not the day-by-day one
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
  // its own journey: this cycle only, not a link to itself
  await p.waitForSelector('.gk-h h2:has-text("Cycle 9")');
  await p.waitForSelector('#gkView .jr-one');
  assert.equal(await p.locator('#gkView .jr-one').count(), 1, 'one journey line');
  assert.equal(await p.locator('#gkView .jr').count(), 0, 'no other cycles');
  assert.match(await p.locator('#gkView .jr-one').innerHTML(), /#9/);
  assert.match(await p.locator('#gkView').innerText(), /Journey\s+this cycle only/);
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
  await p.click('[data-act=jump][data-to=cySess]');
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
  assert.equal(await p.locator('.jr-one[data-kind="booked"] path[stroke-dasharray="6 4"]').count(), 1, 'its journey, booked steps');
  assert.match(text, /no prices stored for this cycle/, 'no dip or peak claimed');
  assert.ok(await p.locator('.gk-chart path[stroke-dasharray="6 4"]').count() >= 1, 'the booked steps are drawn');
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycle?c=3`);
  await p.waitForSelector('.gk-h h2:has-text("Cycle 3")');
  assert.match(await p.locator('#gkView').innerText(), /15-minute candle close/);
  assert.doesNotMatch(await p.locator('#gkView').innerText(), /Appears after/, 'drawn from the candles');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

// Cycles keeps the full journeys chart and adds a calmer Spotlight, the
// running cycle against past ones, and a day-by-day table.
test('cycles: spotlight, is-this-normal band and the day-by-day table', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycles`);
  await p.waitForSelector('#spot .sp');
  assert.equal(await p.locator('.gk-chart .jr[data-kind]').count(), 10, 'the original journeys chart is still there');
  // spotlight: 9 past cycles faint, a typical line, the running one
  assert.equal(await p.locator('#spot .sp').count(), 9);
  assert.equal(await p.locator('#spot .sp-typ').count(), 1);
  assert.match(await p.locator('#spot .sp-run').innerHTML(), /#10 now/);
  // filters
  for (const [k, n] of [['last5', 5], ['checkpoint', 7], ['stopped', 2], ['all', 9]]) {
    await p.click(`[data-spot="${k}"]`);
    await p.waitForFunction(([k, n]) => document.querySelector(`[data-spot="${k}"]`)?.classList.contains('on')
      && document.querySelectorAll('#spot .sp').length === n, [k, n]);
  }
  // hover lights a line and names it; clicking opens it
  const line = p.locator('#spot .sp[data-c="5"]');
  await line.scrollIntoViewIfNeeded();
  const pt = await line.locator('.hit').evaluate(e => { const q = e.getPointAtLength(e.getTotalLength() * 0.85), m = e.getScreenCTM(); return { x: q.x * m.a + m.e, y: q.y * m.d + m.f }; });
  await p.mouse.move(pt.x, pt.y);
  await p.waitForFunction(() => document.querySelector('#spot .sp[data-c="5"]').classList.contains('lit'));
  assert.match(await p.locator('#spot .sp-info').innerText(), /^#5 · convergence stop · −₹3,852 after/);
  assert.equal(await line.locator('.v').evaluate(e => getComputedStyle(e).opacity), '1');
  // the band: past cycles' range with the running cycle over it
  const nb = p.locator('.gk-panel', { hasText: 'Is this cycle normal?' });
  assert.equal(await nb.locator('.nb-mid').count(), 1);
  assert.equal(await nb.locator('.nb-range').count(), 1);
  assert.match(await nb.locator('.nb-run').innerHTML(), /#10 now/);
  // the table: a row per day and one for now, highlighted
  const dt = p.locator('.gk-panel', { hasText: 'Day by day' });
  const rows = await dt.locator('.tr:not(.th)').allInnerTexts();
  assert.match(rows[0], /^Day 1/);
  assert.ok(rows.some(r => /^Now · day \d/.test(r)), 'a row for the running cycle now');
  assert.equal(await dt.locator('.tr.att').count(), 1);
  assert.match(rows.join('\n'), /below every other cycle|bottom quarter|middle half|top quarter|above every other cycle/);
  const text = await p.locator('#gkView').innerText();
  assert.doesNotMatch(text, /undefined|NaN|\[object/);
  await p.mouse.click(pt.x, pt.y);
  await p.waitForURL(/cycle\?c=5$/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

// A to Z on one page: the engine while it runs, the Cycles page's charts
// with this cycle set against the others, the Adjustments charts for its
// rolls, and a bar of links to every section.
test('a cycle’s own page has everything, set against the other cycles', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  // the running cycle: the engine's rules, strikes and legs come first
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycle?c=10`);
  await p.waitForSelector('#cy-live [data-rule]', { timeout: 15000 });
  assert.deepEqual(await p.$$eval('.cy-jump button', bs => bs.map(x => x.textContent)),
    ['Live now', 'P&L', 'Against other cycles', 'Price chart, legs, orders', 'Balance & strikes', 'Rolls', 'Timeline & money', 'Every leg']);
  assert.equal(await p.locator('#cy-live [data-rule]').count(), 5);
  assert.match(await p.locator('#cy-live').innerText(), /Where the strikes sit[\s\S]*Legs & price feed/);
  // a closed cycle: no live section; everything else
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/cycle?c=5`);
  await p.waitForSelector('.gk-h h2:has-text("Cycle 5")');
  await p.waitForSelector('#cy-vs .sp');
  assert.equal(await p.locator('#cy-live').count(), 0);
  assert.equal(await p.locator('.cy-jump button').first().innerText(), 'P&L');
  const vs = p.locator('#cy-vs');
  assert.equal(await vs.locator('.sp').count(), 8, 'the other closed cycles, faint');
  assert.match(await vs.locator('.sp-run').innerHTML(), /#5</, 'this one in blue, not marked "now"');
  assert.match(await vs.locator('.nb-run').innerHTML(), /#5 ended −₹3,852/);
  const day = await vs.locator('.gk-panel', { hasText: 'Day by day' }).locator('.tr.att').innerText();
  assert.match(day, /^End · day 9\.1/);
  assert.equal(await vs.locator('.rg.me').count(), 1, 'its bar outlined among the rest');
  assert.match(await vs.locator('.gk-panel', { hasText: 'This cycle vs the others' }).innerText(), /Result, gross[\s\S]*9th best of 9/);
  // its rolls, charted as on Adjustments
  const rolls = await p.locator('#cy-rolls').innerText();
  for (const want of [/2 in this cycle/, /What rolling earned/, /Balance when it rolled vs what it paid/, /Balance before → after each roll/, /By side and direction/, /By hour of day/]) assert.match(rolls, want);
  // the bar jumps to a section
  await p.click('.cy-jump [data-to="cy-rolls"]');
  await p.waitForFunction(() => { const r = document.querySelector('#cy-rolls').getBoundingClientRect(); return r.top >= 0 && r.top < 200; });
  // another cycle's bar opens that cycle
  await vs.locator('.rg[data-c="4"] rect').first().click({ force: true });
  await p.waitForURL(/cycle\?c=4$/);
  await p.waitForSelector('.gk-h h2:has-text("Cycle 4")');
  assert.doesNotMatch(await p.locator('#gkView').innerText(), /undefined|NaN|\[object/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
