// Account → Option price history: the daily backfill's schedule, its
// progress while it runs (also in the header), and its last result.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, api, until } from './helper.mjs';

// A weekday `n` weekdays back: its contracts aren't stored yet (the run
// at start-up only does today), so a backfill of it has work to show.
function weekdayBack(n) {
  const d = new Date();
  while (n > 0) { d.setDate(d.getDate() - 1); if (d.getDay() % 6) n--; }
  return d.toISOString().slice(0, 10);
}

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('run now: progress in the header and on Account, then the result', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  await until(async () => (await api(ctx, '/candles/backfill')).running === null, 30000);   // a run at start-up may be going
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/account?s=history`);
  await p.waitForSelector('#history [data-act=runHist]');
  assert.match(await p.innerText('#history'), /Next run[\s\S]*17:00/);
  await ctx.request.post(`${BASE}/candles/backfill`, { data: { day: weekdayBack(1) } });   // what Run now does, for a day with work
  await p.waitForSelector('#history .bf-run', { timeout: 15000 });   // noticed without a reload
  assert.match(await p.innerText('#history .bf-run'), /Saving .* option history/);
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('.bf-strip', { timeout: 10000 });
  await p.goto(`${BASE}/linck/#/account?s=history`);
  await p.waitForSelector('#history [data-act=runHist]:not([disabled])', { timeout: 30000 });   // finished
  const txt = await p.innerText('#history');
  assert.match(txt, /Last run[\s\S]*contracts? ·/);
  assert.match(txt, /Stored/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the run-now button starts it', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  await until(async () => (await api(ctx, '/candles/backfill')).running === null, 30000);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/account?s=history`);
  await p.waitForSelector('#history [data-act=runHist]:not([disabled])');
  await p.click('#history [data-act=runHist]');
  await p.waitForSelector('#history .bf-run, #history [data-act=runHist]:not([disabled])', { timeout: 10000 });
  assert.ok(await until(async () => (await api(ctx, '/candles/backfill')).last !== null, 30000));
  await ctx.close();
});

for (const width of [390, 1366, 1440, 1600]) {
  test(`while it runs, a strip under the header shows progress without crowding it (${width}px)`, async () => {
    const ctx = await context(b, { width, height: 844 });
    await until(async () => (await api(ctx, '/candles/backfill')).running === null, 30000);
    await ctx.request.post(`${BASE}/candles/backfill`, { data: { day: weekdayBack(2 + [390, 1366, 1440, 1600].indexOf(width)) } });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/desk`);
    await p.waitForSelector('.bf-strip', { timeout: 10000 });
    const strip = await p.innerText('.bf-strip');
    assert.match(strip, /Saving .*option history\s+\d+ of \d+/);
    if (width > 600) assert.doesNotMatch(strip, /today/, 'a run for an earlier day names that day, not "today"');
    const sw = await p.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(sw <= width + 1, `no sideways scroll (${sw})`);
    await ctx.close();
  });
}
