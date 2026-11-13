// Tools › Compare with underlying (pages/tools-track.js, app/v2/track.py):
// an underlying from the shortcuts or a search, ETFs added and removed,
// one scale in % or points, how far each strays, and the numbers. This
// server has no Kite, so /v2/track and the search answer from
// track.fixture.json (made by app/v2/track.compare from made-up prices).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BASE, browser, context, until } from './helper.mjs';

const FX = JSON.parse(readFileSync(new URL('./track.fixture.json', import.meta.url), 'utf8'));
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('Compare with underlying: pick, plot on one scale, the gap, the numbers; remembered', async () => {
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  const asked = [];
  await ctx.route('**/v2/track?*', r => {
    const u = new URL(r.request().url());
    asked.push(Object.fromEntries(u.searchParams));
    const which = u.searchParams.get('interval') === '5minute' ? FX.intraday : FX.day;
    const want = u.searchParams.get('with').split(',').map(Number);
    return r.fulfill({ json: { ...which, others: which.others.filter(o => want.includes(o.token)) } });
  });
  await ctx.route('**/instruments/search?*', r => r.fulfill({ json: { query: 'x', results: FX.search } }));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/tools/track`);
  await p.waitForSelector('[data-tool="track"]');
  assert.equal(await p.locator('nav.tabs a.on').innerText(), 'Compare with underlying');
  assert.match(await p.locator('[data-track="out"]').innerText(), /Pick at least one ETF or stock/);
  assert.equal(await p.locator('[data-pick="base"] .chip.on').innerText(), 'NIFTY 50', 'NIFTY by default');
  // add NIFTYBEES and SETFNIF50 from a search: derivatives aren't offered
  await p.click('[data-act="tsearch"][data-m="with"]');
  await p.fill('[data-tq]', 'nifty');
  await p.waitForSelector('.trk-r');
  assert.deepEqual(await p.locator('.trk-r b').allInnerTexts(), ['NIFTYBEES', 'SETFNIF50', 'NIFTY MIDCAP 100'], 'NSE and BSE only');
  await p.click('.trk-r[data-s="NIFTYBEES"]');
  await p.waitForSelector('[data-track="figures"]');
  await p.fill('[data-tq]', 'setf');   // the search stays open to add more
  await p.click('.trk-r[data-s="SETFNIF50"]');
  await until(async () => (await p.locator('[data-track="figures"] .tr[data-token]').count()) === 2);
  const last = asked[asked.length - 1];
  assert.equal(last.base, 'NIFTY');
  assert.equal(last.with, '2707457,5582849');
  assert.equal(last.interval, 'day');
  // the numbers
  const bees = p.locator('[data-token="2707457"]');
  assert.match(await bees.innerText(), /NIFTYBEES[\s\S]*\+21\.79%[\s\S]*\+20\.34%[\s\S]*\+1\.45 pts[\s\S]*1\.30%[\s\S]*a year/);
  assert.match(await p.locator('[data-token="5582849"] [data-l="Gap at the end"]').innerText(), /−2\.42 pts/);
  assert.match(await p.locator('[data-token="5582849"] [data-l="Widest gap"]').innerText(), /−3\.08 pts/);
  assert.match(await p.locator('[data-track="out"]').innerText(), /1 of the underlying’s left out/);
  // who won: tracked closest (lowest tracking error), gained most
  const closest = await p.locator('[data-win="closest"]').innerText();
  assert.match(closest, /Tracked closest[\s\S]*NIFTYBEES[\s\S]*Tracking error 1\.30% a year; next SETFNIF50 at 4\.01% a year[\s\S]*Ended \+1\.45 pts against NIFTY 50/);
  assert.match(await p.locator('[data-win="gained"]').innerText(), /Gained most[\s\S]*NIFTYBEES[\s\S]*\+21\.79% over the period; next SETFNIF50 \+17\.92%[\s\S]*\+1\.45 pts more than NIFTY 50 \(\+20\.34%\)/);
  assert.equal(await bees.locator('[data-tag="closest"]').count(), 1);
  assert.equal(await bees.locator('[data-tag="gained"]').count(), 1);
  assert.equal(await p.locator('[data-token="5582849"] [data-tag]').count(), 0);
  // both charts: the underlying and two ETFs; two gap lines and the zero line
  assert.equal(await p.locator('[data-chart="levels"] svg path').count(), 3);
  assert.equal(await p.locator('[data-chart="gap"] svg path').count(), 2);
  // hover: the day's values
  const box = await p.locator('[data-chart="levels"] svg').boundingBox();
  await p.mouse.move(box.x + box.width - 2, box.y + box.height / 2);
  await until(async () => /NIFTYBEES/.test(await p.locator('.tip, .tooltip, [role="tooltip"]').first().innerText().catch(() => '')));
  // in points: the axis says so, nothing asked of the server again
  const n = asked.length;
  await p.click('[data-pick="unit"] [data-v="pts"]');
  assert.match(await p.locator('[data-chart="levels"] .trk-axis').innerText(), /NIFTY 50 points; each one scaled to start at 24,000/);
  assert.equal(asked.length, n);
  // one day, 5-minute candles
  await p.click('[data-pick="period"] [data-v="intraday"]');
  await until(async () => asked[asked.length - 1].interval === '5minute');
  await until(async () => /a candle/.test(await bees.locator('[data-l="Tracking error"]').innerText()));
  // remove one; a reload keeps the rest
  await p.click('[data-act="tdrop"][data-t="5582849"]');
  await until(async () => asked[asked.length - 1].with === '2707457');
  await until(async () => /How closely it tracked[\s\S]*NIFTYBEES/.test(await p.locator('[data-win="closest"]').innerText()));
  assert.equal(await p.locator('[data-tag]').count(), 0, 'one compared: no contest, no tags');
  await p.reload();
  await p.waitForSelector('[data-track="figures"]');
  assert.deepEqual(await p.locator('[data-pick="with"] .trk-w').evaluateAll(xs => xs.map(x => x.dataset.w)), ['2707457']);
  assert.equal(await p.locator('[data-pick="period"] .chip.on').innerText(), 'One day, 5-min');
  // another underlying from the shortcuts
  await p.click('[data-pick="base"] [data-v="BANKNIFTY"]');
  await until(async () => asked[asked.length - 1].base === 'BANKNIFTY');
  // a phone (the pointer off the charts: no hover card left open)
  await p.mouse.move(1, 1);
  await p.setViewportSize({ width: 390, height: 844 });
  await p.waitForTimeout(200);
  assert.equal(await p.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Compare with underlying: without Kite it says so', async () => {
  const ctx = await context(b, { width: 1200, height: 800 });
  await ctx.addInitScript(() => {   // picked before: NIFTY against NIFTYBEES, a month
    if (location.protocol !== 'http:') return;   // about:blank has no storage
    const all = JSON.parse(localStorage.getItem('linck-ui') || '{}');
    all.track = { base: { key: 'NIFTY', label: 'NIFTY 50' }, with: [{ token: 2707457, label: 'NIFTYBEES' }], period: '1M' };
    localStorage.setItem('linck-ui', JSON.stringify(all));
  });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/tools/track`);
  await p.waitForSelector('[data-track="error"]');
  assert.match(await p.locator('[data-track="error"]').innerText(), /Couldn’t compare[\s\S]*Kite isn’t connected/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
