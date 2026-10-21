// Geek mode (pages/geek/): the engine card on the Now tab, entering with
// the button or G, the full-screen shell without the app's header, every
// view loading on desktop and phone, and Esc back to the normal page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

const ID = IDS['Nifty Monthly Strangle'];
const VIEWS = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'map', 'cycles', 'adjustments', 'premium', 'entries', 'conditions', 'volatility', 'settings'];
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
  const DTT = IDS['Sensex Credit Spread'];
  const SHARED = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'conditions', 'volatility', 'settings'];
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
      }, null, { timeout: 20000 });
      const text = await p.locator('#gkView').innerText();
      assert.doesNotMatch(text, /undefined|NaN|\[object/, `${label} ${v}: no broken values`);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(sw <= width, `${label} ${v}: page ${sw}px wide at ${width}px`);
      if (v === 'overview') assert.match(text, /Win rate/);
      if (v === 'engine') assert.match(text, /Paused|Stopped|Not running|Flat|Holding/);
      if (v === 'decisions') {
        if (await p.locator('#gkView [data-act="scope"][data-k="all"]').count()) {   // running: today first
          await p.click('#gkView [data-act="scope"][data-k="all"]');
          await p.waitForSelector('#gkView [data-act="scope"][data-k="all"].on');
        }
        assert.ok(await p.locator('#gkView .oc.acted').count() > 0, 'its trades, as decisions');
      }
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
      }, null, { timeout: 20000 });
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

test('SuperTrend options: engine ladder, signals, exits, days and one trade end to end', async () => {
  const ST = IDS['Pivot Supertrend Options'];
  const OWN = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'signals', 'exits', 'days', 'conditions', 'volatility', 'settings'];
  for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
    const ctx = await context(b, { width, height: 860 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${ST}/now`);
    await p.waitForSelector('[data-engine] .er');
    assert.equal(await p.locator('[data-engine] .er').count(), 4, `${label}: its four rules on the Now tab`);
    await p.goto(`${BASE}/linck/#/d/${ST}/geek`);
    await p.waitForSelector('.gk-rail a.on');
    assert.deepEqual(await p.$$eval('.gk-rail a', as => as.map(a => a.getAttribute('href').split('/geek/')[1])), OWN, `${label}: menu`);
    for (const v of OWN.concat(['trade'])) {
      await p.goto(`${BASE}/linck/#/d/${ST}/geek/${v}`);
      await p.waitForSelector(`.gk-rail a.on[href$="/geek/${v === 'trade' ? 'signals' : v}"]`);
      await p.waitForFunction(() => {
        const el = document.querySelector('#gkView');
        return el && el.children.length && !el.querySelector('.skel');
      }, null, { timeout: 20000 });
      const text = await p.locator('#gkView').innerText();
      assert.doesNotMatch(text, /undefined|NaN|\[object|Infinity/, `${label} ${v}: no broken values`);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(sw <= width, `${label} ${v}: page ${sw}px wide at ${width}px`);
    }
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  }
  const ctx = await context(b);
  const p = await ctx.newPage();
  const api = async v => (await ctx.request.get(`${BASE}/v2/deployments/${ST}/geek/${v}`)).json();
  // the engine: four rules, the ladder S3..R3 with the index and the line, as last saved (it's stopped)
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/engine`);
  await p.waitForSelector('[data-st] svg');
  assert.equal(await p.locator('.gk-rules [data-rule]').count(), 4);
  assert.deepEqual(await p.$$eval('.gk-rules [data-rule]', rs => rs.map(r => r.dataset.rule)), ['st_flip', 'force_exit', 'pivot_break', 'max_trades']);
  const ladder = await p.locator('[data-st]').innerText();
  for (const k of ['S3', 'S2', 'S1', 'P', 'R1', 'R2', 'R3', 'as last saved', 'Up', 'sells PEs on R breaks']) assert.ok(ladder.includes(k), k);
  assert.match(await p.locator('.gk-next').innerText(), /Stopped/);
  // signals: every trade listed, and the numbers are the API's
  const sig = await api('signals');
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/signals`);
  await p.waitForSelector('#gkView .gk-tbl a[href*="/geek/trade?p="]');
  assert.equal(await p.locator('#gkView a[href*="/geek/trade?p="]').count(), sig.trades.length);
  assert.ok(sig.trades.length >= 10 && sig.trades.every(t => t.past_level > 0 && t.from_st > 0));
  const tiles = await p.locator('.gk-tiles').first().innerText();
  assert.ok(tiles.includes(String(sig.summary.trades)) && tiles.includes(`${Math.round(sig.summary.win_rate)}%`));
  // one trade: from the list, then older and newer
  await p.locator('#gkView a[href*="/geek/trade?p="]').nth(1).click();
  await p.waitForURL(/\/geek\/trade\?p=/);
  await p.waitForFunction(() => document.querySelector('#gkView').innerText.includes('All trades') && document.querySelector('#gkView .gk-chart'));
  const second = sig.trades[1];
  assert.match(await p.locator('#gkView').innerText(), new RegExp(`${sig.trades.length - 1} of ${sig.trades.length}`));
  assert.match(await p.locator('#gkView').innerText(), new RegExp(second.symbol));
  assert.equal(await p.locator('.gk-rail a.on').getAttribute('href').then(h => h.split('/geek/')[1]), 'signals');
  await p.click('a:has-text("Newer →")');
  await p.waitForFunction(n => document.querySelector('#gkView').innerText.includes(`${n} of ${n}`), sig.trades.length);
  assert.equal(await p.locator('a:has-text("Newer →")').count(), 0, 'the newest has no newer');
  assert.equal(await p.locator('#gkView svg.gk-chart:not([data-vix-slot] svg)').count(), 2, 'the index and the premium (VIX: its own test)');
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/trade?p=nope`);
  await p.waitForSelector('#gkView .empty');
  assert.match(await p.locator('#gkView').innerText(), /There’s no such trade/);
  // exits and days add up to the same trades
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/exits`);
  await p.waitForSelector('#gkView .gk-tiles');
  const ex = await p.locator('#gkView .gk-tiles').innerText();
  for (const [k, g] of Object.entries(sig.by_exit)) assert.ok(ex.includes(g.label) && ex.includes(String(g.n)), k);
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/days`);
  await p.waitForSelector('#gkView .gk-tbl .tr:not(.th)');
  assert.equal(await p.locator('#gkView .gk-tbl .tr:not(.th)').count(), sig.days.length);
  assert.ok(await p.locator('#gkView .gk-tbl .tr.att').count() >= 1, 'a day the cap turned a signal away stands out');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('DTT straddle: engine, straddles, exits & timing, and one straddle end to end', async () => {
  const DD = IDS['BankNifty DTT Plain'];
  const OWN = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'straddles', 'timing', 'conditions', 'volatility', 'settings'];
  for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
    const ctx = await context(b, { width, height: 860 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${DD}/geek`);
    await p.waitForSelector('.gk-rail a.on');
    assert.deepEqual(await p.$$eval('.gk-rail a', as => as.map(a => a.getAttribute('href').split('/geek/')[1])), OWN, `${label}: menu`);
    for (const v of OWN.concat(['straddle'])) {
      await p.goto(`${BASE}/linck/#/d/${DD}/geek/${v}`);
      await p.waitForSelector(`.gk-rail a.on[href$="/geek/${v === 'straddle' ? 'straddles' : v}"]`);
      await p.waitForFunction(() => {
        const el = document.querySelector('#gkView');
        return el && el.children.length && !el.querySelector('.skel');
      }, null, { timeout: 20000 });
      const text = await p.locator('#gkView').innerText();
      assert.doesNotMatch(text, /undefined|NaN|\[object|Infinity/, `${label} ${v}: no broken values`);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(sw <= width, `${label} ${v}: page ${sw}px wide at ${width}px`);
    }
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  }
  const ctx = await context(b);
  const p = await ctx.newPage();
  const api = async v => (await ctx.request.get(`${BASE}/v2/deployments/${DD}/geek/${v}`)).json();
  // engine: the straddle panel, and its rules in the strategy's order while it runs or holds
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/engine`);
  await p.waitForSelector('[data-dd]');
  const rules = await p.$$eval('.gk-rules [data-rule]', rs => rs.map(r => r.dataset.rule));
  assert.ok(rules.length === 0 || rules.join() === 'entry,force_exit,stop_ce,stop_pe,profit_target', rules.join());
  // straddles: every one listed, numbers from the API
  const all = await api('straddles');
  assert.ok(all.straddles.length >= 20 && all.summary.closed >= 20);
  assert.ok(all.straddles.every(r => !r.ce || !r.pe || r.ce.symbol.slice(0, -2) === r.pe.symbol.slice(0, -2)), 'one strike per straddle');
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/straddles`);
  await p.waitForSelector('#gkView a[href*="/geek/straddle?s="]');
  assert.equal(await p.locator('#gkView a[href*="/geek/straddle?s="]').count(), all.straddles.length);
  const tiles = await p.locator('.gk-tiles').first().innerText();
  assert.ok(tiles.includes(String(all.summary.straddles)) && tiles.includes(`${Math.round(all.summary.win_rate)}%`));
  // one closed straddle: legs, combined and index charts; older and newer
  const i = all.straddles.findIndex(r => !r.open);
  await p.locator('#gkView a[href*="/geek/straddle?s="]').nth(i).click();
  await p.waitForURL(/\/geek\/straddle\?s=/);
  await p.waitForFunction(() => document.querySelector('#gkView').innerText.includes('All straddles') && document.querySelectorAll('#gkView .gk-chart').length >= 3);
  const one = all.straddles[i];
  const text = await p.locator('#gkView').innerText();
  assert.match(text, new RegExp(`${all.straddles.length - i} of ${all.straddles.length}`));
  assert.match(text, new RegExp(one.strike.toLocaleString('en-IN')));
  assert.equal(await p.locator('#gkView svg.gk-chart:not([data-vix-slot] svg)').count(), 3, 'the legs, the combined premium and the index (VIX: its own test)');
  assert.equal(await p.locator('.gk-rail a.on').getAttribute('href').then(h => h.split('/geek/')[1]), 'straddles');
  await p.click('a:has-text("← Older")');
  await p.waitForFunction(n => document.querySelector('#gkView').innerText.includes(`${n} of `), all.straddles.length - i - 1);
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/straddle?s=nope`);
  await p.waitForSelector('#gkView .empty');
  assert.match(await p.locator('#gkView').innerText(), /There’s no such straddle/);
  // exits & timing: one tile per way it ended, the endings chart
  await p.goto(`${BASE}/linck/#/d/${DD}/geek/timing`);
  await p.waitForSelector('#gkView .gk-chart');
  const ex = await p.locator('#gkView .gk-tiles').innerText();
  for (const g of Object.values(all.by_exit)) assert.ok(ex.includes(g.label) && ex.includes(String(g.n)), g.label);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

for (const [name, rolls] of [['BankNifty DTT Adjusted', false], ['BankNifty DTT Advanced', true]]) {
  test(`${name}: engine, sessions, adjustments and one day end to end`, async () => {
    const ID = IDS[name];
    const OWN = ['overview', 'positions', 'trades', 'performance', 'engine', 'decisions', 'journal', 'sessions', 'moves', 'conditions', 'volatility', 'settings'];
    for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
      const ctx = await context(b, { width, height: 860 });
      const p = await ctx.newPage();
      await p.goto(`${BASE}/linck/#/d/${ID}/geek`);
      await p.waitForSelector('.gk-rail a.on');
      assert.deepEqual(await p.$$eval('.gk-rail a', as => as.map(a => a.getAttribute('href').split('/geek/')[1])), OWN, `${label}: menu`);
      for (const v of OWN.concat(['session'])) {
        await p.goto(`${BASE}/linck/#/d/${ID}/geek/${v}`);
        await p.waitForSelector(`.gk-rail a.on[href$="/geek/${v === 'session' ? 'sessions' : v}"]`);
        await p.waitForFunction(() => { const el = document.querySelector('#gkView'); return el && el.children.length && !el.querySelector('.skel'); }, null, { timeout: 20000 });
        const text = await p.locator('#gkView').innerText();
        assert.doesNotMatch(text, /undefined|NaN|\[object|Infinity/, `${label} ${v}: no broken values`);
        const sw = await p.evaluate(() => document.documentElement.scrollWidth);
        assert.ok(sw <= width, `${label} ${v}: page ${sw}px wide at ${width}px`);
      }
      assert.deepEqual(ctx.errors, []);
      await ctx.close();
    }
    const ctx = await context(b);
    const p = await ctx.newPage();
    const api = async v => (await ctx.request.get(`${BASE}/v2/deployments/${ID}/geek/${v}`)).json();
    const all = await api('sessions');
    assert.ok(all.sessions.length >= 15 && all.summary.adjustments > 0, 'it adjusted');
    assert.equal(all.summary.rolling, rolls);
    assert.equal(all.summary.rolls > 0, rolls, rolls ? 'the advanced one rolls' : 'the adjusted one never rolls');
    assert.equal(Math.round(all.summary.pnl), Math.round(all.summary.original_pnl + all.summary.extra_pnl), 'original + extra legs = the total');
    await p.goto(`${BASE}/linck/#/d/${ID}/geek/engine`);
    await p.waitForSelector('[data-adj]');
    await p.goto(`${BASE}/linck/#/d/${ID}/geek/sessions`);
    await p.waitForSelector('#gkView a[href*="/geek/session?d="]');
    assert.equal(await p.locator('#gkView a[href*="/geek/session?d="]').count(), all.sessions.length);
    // open the busiest day
    const i = all.sessions.reduce((a, s, j) => (s.adjustments + s.rolls > all.sessions[a].adjustments + all.sessions[a].rolls ? j : a), 0);
    const day = all.sessions[i];
    await p.locator('#gkView a[href*="/geek/session?d="]').nth(i).click();
    await p.waitForURL(/\/geek\/session\?d=/);
    await p.waitForFunction(() => document.querySelector('#gkView').innerText.includes('All sessions') && document.querySelectorAll('#gkView .gk-chart').length >= 4);
    const text = await p.locator('#gkView').innerText();
    assert.match(text, new RegExp(`${all.sessions.length - i} of ${all.sessions.length}`));
    for (const l of day.legs) assert.ok(text.includes(l.symbol), `${l.symbol} listed`);
    assert.equal(await p.locator('.gk-rail a.on').getAttribute('href').then(h => h.split('/geek/')[1]), 'sessions');
    await p.goto(`${BASE}/linck/#/d/${ID}/geek/session?d=1999-01-01`);
    await p.waitForSelector('#gkView .empty');
    assert.match(await p.locator('#gkView').innerText(), /There’s no session on that day/);
    const moves = await api('moves');
    await p.goto(`${BASE}/linck/#/d/${ID}/geek/moves`);
    await p.waitForSelector('#gkView .gk-tbl a[href*="/geek/session?d="]');
    assert.equal(await p.locator('#gkView .gk-tbl a[href*="/geek/session?d="]').count(), moves.rows.length);
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}

test('Conditions: every trade with its market, dots that show their trade, and the buckets add up', async () => {
  const ST = IDS['Pivot Supertrend Options'];
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  const body = await (await ctx.request.get(`${BASE}/v2/deployments/${ST}/geek/conditions`)).json();
  const closed = body.rows.filter(r => r.pnl != null);
  assert.ok(closed.length >= 10 && body.summary.with_path === closed.length, 'every seeded trade has a path (from its candles)');
  assert.ok(body.rows.every(r => r.has_context && r.context.gap_pct != null), 'the seeded entries carry their market');
  for (const g of body.groups) assert.equal(g.covered, closed.length, `${g.label} counts every closed trade`);
  await p.goto(`${BASE}/linck/#/d/${ST}/geek`);
  await p.waitForSelector('.gk-rail a.on');
  await p.keyboard.press('k');
  await p.waitForURL(/\/geek\/conditions$/);
  await p.waitForFunction(() => document.querySelectorAll('#gkView .gk-chart circle').length > 0);
  assert.equal(await p.locator('#gkView .gk-chart').first().locator('circle').count(), closed.length, 'one dot per closed trade');
  assert.equal(await p.locator('#gkView .cd-grid .gk-panel').count(), body.groups.length);
  const text = await p.locator('#gkView').innerText();
  for (const s of ['Time of entry', 'Day of the week', 'Days to expiry', 'Today’s gap', 'Index since the open', 'Strike against the index', 'ATR at the signal', 'Signal candle’s size']) assert.ok(text.includes(s), s);
  const atr = body.groups.find(x => x.key === 'atr');
  assert.deepEqual(atr.buckets.map(x => x.label.split(' · ')[0]), ['Low', 'Middle', 'High']);
  // a dot shows its trade
  // (dots overlap: aim at the last one drawn, which is on top)
  const dot = p.locator('#gkView .gk-chart').first().locator('circle').last();
  await dot.scrollIntoViewIfNeeded();
  const box = await dot.boundingBox();
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await p.waitForFunction(() => { const t = document.querySelector('.tip'); return t && /Worst on the way \(MAE\)/.test(t.innerText); });
  const tip = await p.locator('.tip').innerText();
  assert.match(tip, /Result\s+[+−]₹/);
  assert.match(tip, /Gap\s+[+-]\d/);
  // a bucket row shows its trades
  const row = p.locator('#gkView .cd-row').first();
  await row.hover();
  await p.waitForFunction(() => /Average trade/.test(document.querySelector('.tip')?.innerText || ''));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Geek’s top bar shows India VIX and what it implies', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${IDS['BankNifty DTT Plain']}/geek/engine`);
  await p.waitForFunction(() => /^\d+\.\d{2}/.test(document.querySelector('[data-gk="vix"]')?.textContent || ''));
  const kv = p.locator('.gk-kv', { has: p.locator('[data-gk="vix"]') });
  const tip = await kv.getAttribute('data-tip');
  assert.match(tip, /India VIX/);
  assert.match(tip, /Expected move today {2}±\d+\.\d{2}%/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Volatility: VIX now, ₹ per VIX point, trades and days against VIX, week by week', async () => {
  const ID = IDS['Nifty Strangle A'];
  const ctx = await context(b, { width: 1440, height: 900 });
  const body = await (await ctx.request.get(`${BASE}/v2/deployments/${ID}/geek/volatility`)).json();
  assert.ok(body.has_vix && body.has_index, 'the seeded VIX and index days');
  assert.equal(body.side, 'sold', 'a hedged strangle sells premium, net');
  assert.equal(body.proxy, false, 'a Nifty deployment: the real VIX');
  assert.ok(body.per_point && body.per_point.n >= 10);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek`);
  await p.waitForSelector('.gk-rail a.on');
  await p.keyboard.press('v');
  await p.waitForURL(/\/geek\/volatility$/);
  await p.waitForFunction(() => document.querySelectorAll('#gkView .gk-chart').length >= 4);
  const text = await p.locator('#gkView').innerText();
  for (const s of ['VIX now', 'Priced move today', '₹ per VIX point', 'Week by week', 'VIX at entry', 'What VIX did during the trade', 'sells premium (net)']) assert.ok(text.includes(s), s);
  assert.match(text, /What it says now/);
  assert.match(text, /With VIX between [\d.]+ and [\d.]+, as now|new ground for it/);
  if (body.filters.length) {
    assert.match(text, /If it had only traded at some VIX levels/);
    assert.equal(await p.locator('#gkView .vf-row[data-tip]').count(), body.filters.length);
  }
  // a trade's dot shows VIX at entry and exit
  const dot = p.locator('#gkView .gk-chart').nth(1).locator('circle').last();
  await dot.scrollIntoViewIfNeeded();
  const box = await dot.boundingBox();
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await p.waitForFunction(() => /VIX at entry/.test(document.querySelector('.tip')?.innerText || ''));
  // a BankNifty deployment says VIX is a stand-in
  await p.goto(`${BASE}/linck/#/d/${IDS['BankNifty DTT Plain']}/geek/volatility`);
  await p.waitForFunction(() => /stand-in/.test(document.querySelector('#gkView')?.innerText || ''));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a VIX panel on one straddle, one session, one trade and one cycle, at their marks', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  const get = async (id, v) => (await ctx.request.get(`${BASE}/v2/deployments/${id}/geek/${v}`)).json();
  const DD = IDS['BankNifty DTT Plain'], AD = IDS['BankNifty DTT Adjusted'], ST = IDS['Pivot Supertrend Options'], SG = IDS['Nifty Monthly Strangle'];
  const s = (await get(DD, 'straddles')).straddles.find(r => !r.open);
  const sess = (await get(AD, 'sessions')).sessions.find(r => !r.open);
  const tr = (await get(ST, 'signals')).trades.find(r => !r.open);
  const cys = (await get(SG, 'cycles')).rows;
  const cy = cys.find(r => r.status !== 'open') || cys[0];
  for (const [url, what] of [[`${DD}/geek/straddle?s=${s.id}`, 'straddle'], [`${AD}/geek/session?d=${sess.day}`, 'session'],
    [`${ST}/geek/trade?p=${tr.id}`, 'trade'], [`${SG}/geek/cycle?c=${cy.cycle_id}`, 'cycle']]) {
    await p.goto(`${BASE}/linck/#/d/${url}`);
    const panel = p.locator('#gkView .gk-panel', { has: p.locator('[data-vix-slot]') });
    await panel.waitFor({ timeout: 20000 });
    await p.waitForFunction(() => document.querySelector('[data-vix-move]'), null, { timeout: 15000 });
    assert.match(await panel.locator('[data-vix-move]').innerText(), /VIX was \d+\.\d{2} at entry and (rose to|fell to|held at) \d+\.\d{2}/, what);
    assert.equal(await panel.locator('svg[data-scrub]').count(), 1, `${what}: the VIX line shows each moment on hover`);
    assert.ok(await panel.locator('svg .hit[data-tip]').count() >= 1, `${what}: the entry and exit marks`);
  }
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Volatility: the implied volatility of what was sold, against VIX', async () => {
  const ST = IDS['Pivot Supertrend Options'];
  const ctx = await context(b, { width: 1440, height: 900 });
  const body = await (await ctx.request.get(`${BASE}/v2/deployments/${ST}/geek/volatility`)).json();
  const withIv = body.rows.filter(r => r.iv != null);
  assert.ok(withIv.length >= 10, `implied volatility worked out from the fills (${withIv.length})`);
  assert.ok(withIv.every(r => r.iv > 1 && r.iv < 300), 'in a sane range');
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ST}/geek/volatility`);
  await p.waitForFunction(() => /Premium against VIX, and the result/.test(document.querySelector('#gkView')?.innerText || ''));
  if (body.groups.some(g => g.key === 'rich')) assert.match(await p.locator('#gkView').innerText(), /richest against VIX/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
