// Repairing a flagged day (pages/repair-case.js; app/repair/dayrepair.py):
// the plan with its proof — the index minute by minute with both sides'
// trades, each leg, each step and what triggered it, the end state both
// ways — pushed only with a confirmation, a minute that reads two ways
// chosen, an applied repair undone. The repair worker is real: this server
// has no Kite session, so the jobs it's given fail, and the page says why.
// Seeded by tests/e2e/serve.py: seed_repair_cases.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api, dep, until } from './helper.mjs';

let b;
let day;
test.before(async () => {
  b = await browser();
  const ctx = await context(b);
  day = (await api(ctx, '/v2/day-checks')).day;
  await ctx.close();
});
test.after(async () => { await b.close(); });

test('a planned repair shows its proof, and why each step was taken', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-row');
  await p.locator('.dc-row', { hasText: 'Nifty Strangle A' }).locator('a', { hasText: 'Review and repair' }).click();
  await p.waitForSelector('.rc-state.t-ready');
  assert.match(await p.locator('.rc-found').innerText(), /Traded differently/i);
  assert.match(await p.locator('.rc-state').innerText(), /Ready[\s\S]*draft/);
  const chart = p.locator('[data-chart="index"]');
  assert.equal(await chart.locator('.rc-mk.app').count(), 2, 'the app’s two trades');
  assert.equal(await chart.locator('.rc-mk.rep').count(), 2, 'the strategy’s two');
  assert.match(await chart.locator('.rc-from').innerText(), /Repaired from 13:31/);
  const box = await chart.boundingBox();
  await p.mouse.move(box.x + box.width * 0.5, box.y + 100);
  assert.match(await chart.locator('[data-read]').innerText(), /\d\d:\d\d · NIFTY 50 O [\d,.]+ H [\d,.]+ L [\d,.]+ C [\d,.]+ · VIX/);
  assert.equal(await p.locator('[data-chart="legs"] + .rc-legend > span').count(), 3);
  assert.match(await p.locator('.dc-end').innerText(), /Now[\s\S]*NIFTY26NOV22400PE[\s\S]*After the repair[\s\S]*NIFTY26NOV22800PE/i);
  assert.match(await p.locator('.rc-nums').innerText(), /Change\s*\+₹4,956/i);
  const steps = await p.locator('.rc-steps').innerText();
  assert.match(steps, /13:12[\s\S]*PE at 2\.1× the CE[\s\S]*no action — ratio 0\.53/);
  assert.match(steps, /13:31\s*Bought 75 NIFTY26NOV22400PE[^\n]*\ncontinuous_50pct_trigger — [^\n]*ratio 0\.48 · threshold 0\.5[\s\S]*13:31\s*Sold 75 NIFTY26NOV22800PE/,
    'the buy-back, with what triggered it, before the new sell');
  // a trade on the chart leads to its step
  await chart.locator('.rc-mk.rep').first().click();
  await p.waitForSelector('.rc-steps > .hl');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('push asks first, pauses the deployment, and reports how it went', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair/${day}/${IDS['Nifty Strangle A']}`);
  await p.waitForSelector('[data-act="push"]');
  await p.click('[data-act="push"]');
  const dlg = p.locator('.scrim');
  await dlg.waitFor();
  const text = await dlg.innerText();
  assert.match(text, /From 13:31 to the close, the app’s 0 trades are replaced by the strategy’s 2/);
  assert.match(text, /paused while it’s applied/);
  await dlg.locator('[data-ok]').click();
  // the real worker has no Kite session here: the push fails, and says why
  await until(async () => /didn’t go through[\s\S]*No Kite session/.test(await p.locator('.rc-state').innerText()), 60000);
  await until(async () => (await dep(ctx, 'Nifty Strangle A')).status === 'active', 15000);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a minute that reads two ways: both paths side by side, and the choice kept as a draft', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair/${day}/${IDS['Nifty Monthly Strangle']}`);
  await p.waitForSelector('.rc-choice');
  assert.match(await p.locator('.rc-state').innerText(), /Your choice: 13:05/);
  const choice = p.locator('.rc-choice');
  assert.match(await choice.locator('.rc-candles').innerText(), /NIFTY 50[\s\S]*H 25,430\.00[\s\S]*L 24,600\.00/);
  const paths = choice.locator('.rc-path');
  assert.equal(await paths.count(), 2);
  assert.match(await paths.nth(0).innerText(), /Up first[\s\S]*Bought 75 NIFTY26NOV27000CE @ 31\.00[\s\S]*−₹980/);
  assert.match(await paths.nth(1).innerText(), /Down first[\s\S]*usual reading[\s\S]*No trade this minute/);
  assert.equal(await p.locator('[data-chart="index"] rect[fill="var(--y)"]').count(), 1, 'the minute marked on the chart');
  await paths.nth(0).locator('[data-act="choose"]').click();
  await until(async () => (await p.locator('.rc-choices').innerText().catch(() => '')).includes('13:05 up first'));
  const c = await api(ctx, `/v2/repair-cases/${day}/${IDS['Nifty Monthly Strangle']}`);
  assert.deepEqual(c.draft.choices, { '13:05': 'up_first' });
  // planning from there needs Kite: here it says so
  await until(async () => /Couldn’t plan it[\s\S]*No Kite session/.test(await p.locator('.rc-state').innerText()), 60000);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('an applied repair is shown and can be undone', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair/${day}/${IDS['Bankex Strangle']}`);
  await p.waitForSelector('.rc-state.t-ok');
  assert.match(await p.locator('.rc-state').innerText(), /Repaired[\s\S]*Applied \d\d:\d\d from 14:02: 1 trade in place of the app’s 1/);
  assert.match(await p.locator('.rc-replaced').innerText(), /14:40 Bought 30 BANKEX26NOV66300CE/);
  await p.click('[data-act="undo"]');
  await p.locator('.scrim [data-ok]').click();
  await until(async () => (await api(ctx, `/v2/repair-cases/${day}/${IDS['Bankex Strangle']}`)).applied === null);
  await p.waitForSelector('.rc-state.t-ok', { state: 'detached' });
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone the case reads top to bottom and fits', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair/${day}/${IDS['Nifty Strangle A']}`);
  await p.waitForSelector('.rc-found');
  await p.waitForSelector('[data-chart="index"]');
  const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(over <= 1, `no sideways scroll (${over}px)`);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
