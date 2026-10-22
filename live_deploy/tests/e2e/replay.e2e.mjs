// Geek mode's Replay (pages/geek/replay.js), the way Opstra and Sensibull
// replay a position: the payoff at expiry and today against the index,
// with the index and its future marked; every position under it, new ones
// joining and closed ones greyed with what they booked; steps of 5m…1D as
// fine as the stored prices go; play pausing at each trade; the timeline
// to drag; a chain of cycles; a phone.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

const ID = IDS['Nifty Monthly Strangle'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const step = p => p.evaluate(() => +document.querySelector('[data-range]').value);
const ready = p => p.waitForSelector('[data-rp="strip"] .rp-cell', { timeout: 20000 });
const rows = p => p.locator('[data-rp="pos"] .tr:not(.th):not(.rp-total)').count();
const ms = t => new Date(t).getTime();

test('the payoff of what’s held, the positions under it, step by step to the next trade', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  assert.equal(d.interval, '5minute');
  const adj = d.events.find(e => e.frame > 0);
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  const chart = p.locator('[data-rp="stage"] svg.gk-chart');
  assert.equal(await chart.count(), 1);
  assert.equal(await chart.locator('[data-curve="expiry"]').count(), 1, 'the payoff at expiry');
  assert.equal(await chart.locator('[data-curve="now"]').count(), 1, 'and today');
  assert.match(await chart.innerHTML(), /NIFTY 50 [\d,.]+/);
  assert.match(await chart.innerHTML(), /FUT [\d,.]+/);
  assert.match(await p.locator('[data-rp="strip"]').innerText(), /Future · NIFTY26NOVFUT[\s\S]*basis/);
  assert.match(await p.locator('[data-rp="strip"]').innerText(), /Max loss at expiry\s*Unlimited/, 'a short strangle');
  assert.match(await p.locator('[data-rp="banner"]').innerText(), /New cycle 10[\s\S]*first closed cycle 9/);
  // the P&L on the chart is the P&L of the step
  const pnl = (await p.locator('[data-rp="strip"] .rp-cell', { hasText: 'P&L' }).locator('b').innerText()).trim();
  assert.ok((await chart.innerHTML()).includes(pnl.replace('+', '+')), `the chart shows ${pnl} at the index`);
  assert.equal(await rows(p), d.legs.filter(l => ms(l.t0) < ms(d.frames[1].t)).length, 'only what was open at the first step');

  // steps: as fine as the 5-minute prices, and bigger
  assert.deepEqual(await p.locator('[data-seg="step"] button').allInnerTexts(), ['5m', '15m', '30m', '1h', '1D']);
  await p.click('[data-act="fwd"]');
  assert.equal(await step(p), 1);
  await p.click('[data-act="step"][data-m="30"]');
  await p.click('[data-act="fwd"]');
  const i30 = await step(p);
  assert.ok(ms(d.frames[i30].t) - ms(d.frames[1].t) >= 30 * 60000, 'thirty minutes on');
  await p.keyboard.press('ArrowLeft');
  assert.equal(await step(p), 1, 'and back');

  // play, a day at a time: it pauses on the adjustment, which brings in a new leg and greys the old one
  await p.click('[data-act="to"][data-f="0"]');
  await p.click('[data-act="step"][data-m="0"]');
  await p.click('[data-act="speed"][data-s="3"]');
  await p.click('[data-act="play"]');
  await p.waitForFunction(f => +document.querySelector('[data-range]').value === f, adj.frame, { timeout: 30000 });
  await p.waitForTimeout(300);
  assert.match(await p.locator('.rp-play').innerText(), /Play/, 'paused');
  assert.ok(await p.locator('.rp-banner.here').count(), 'on the trade itself');
  assert.match(await p.locator('[data-rp="banner"]').innerText(), new RegExp(adj.label));
  assert.ok(await p.locator('[data-rp="pos"] .tr.rp-fresh').count() >= 1, 'the new leg, highlighted');
  const gone = p.locator('[data-rp="pos"] .tr.rp-closed');
  assert.ok(await gone.count() >= 1, 'the closed leg, greyed');
  assert.match(await gone.first().innerText(), /Closed 15:13 · Adjustment/);
  assert.match(await gone.first().innerText(), /[+−]₹[\d,]+/, 'with what it booked');
  assert.match(await p.locator('[data-rp="pos"] .rp-total').innerText(), /open [+−]?₹[\d,]+ · booked [+−]?₹[\d,]+/);

  // trades by key, and the list
  await p.keyboard.press('[');
  assert.equal(await step(p), 0);
  await p.keyboard.press(']');
  assert.equal(await step(p), adj.frame);
  await p.locator('.rp-li').first().click();
  assert.equal(await step(p), 0);
  // the timeline stays with the controls as the page scrolls: press anywhere on it to go there
  const box = await p.locator('[data-track]').boundingBox();
  await p.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  const mid = await step(p);
  assert.ok(Math.abs(mid - (d.frames.length - 1) / 2) <= d.frames.length * 0.03, `the middle of the replay (${mid})`);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a chain of cycles: From and To replay them as one, each new cycle in the trades', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  const n = await p.locator('[data-pick="from"] option').count();
  await p.selectOption('[data-pick="from"]', String(n - 2));
  await p.waitForURL(new RegExp(`replay\\?from=${n - 2}&to=${n}$`));
  await ready(p);
  assert.match(await p.locator('.rp-pick').innerText(), /a chain of 3/);
  assert.equal(await p.locator('.rp-li .rp-chip', { hasText: 'New cycle' }).count(), 3);
  await p.locator('[data-ep]').nth(1).click();
  await p.waitForURL(/replay\?from=(\d+)&to=\1$/);
  await p.waitForFunction(() => /one episode/.test(document.querySelector('.rp-pick')?.innerText || ''), null, { timeout: 20000 });
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('it says what it can’t draw: an index level that can’t be these contracts’', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  // the fixture's BankNifty history sits near 25,000 while its strikes are 55,900
  await p.goto(`${BASE}/linck/#/d/${IDS['BankNifty DTT Plain']}/geek/replay`);
  await ready(p);
  const why = await p.locator('[data-rp="stage"]').innerText();
  assert.match(why, /NIFTY BANK at [\d,.]+ can’t be the index of/);
  assert.match(why, /PE 55900/); assert.match(why, /CE 55900/);
  assert.equal(await p.locator('[data-rp="stage"] svg').count(), 0);
  assert.ok(await rows(p) >= 1, 'the positions are still there');
  // the same for Sensex (history near 25,000, strikes past 80,000). A leg with no known
  // expiry (the payoff at expiry, but no line for today) is in tests/js/replay.test.mjs
  await p.goto(`${BASE}/linck/#/d/${IDS['Sensex Credit Spread']}/geek/replay`);
  await ready(p);
  assert.match(await p.locator('[data-rp="stage"]').innerText(), /SENSEX at [\d,.]+ can’t be the index of/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('every kind of deployment has it, and it fits a phone', async () => {
  for (const name of ['Nifty Monthly Strangle', 'BankNifty DTT Plain', 'Pivot Supertrend Options', 'BankNifty DTT Adjusted']) {
    const ctx = await context(b, { width: 390, height: 844 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${IDS[name]}/geek/replay`);
    await p.waitForFunction(() => { const el = document.querySelector('#gkView'); return el && el.children.length && !el.querySelector('.skel'); }, null, { timeout: 20000 });
    assert.match(await p.locator('#gkView').innerText(), /Replay/, name);
    if (await p.locator('[data-rp="banner"]').count()) {
      assert.match(await p.locator('[data-rp="banner"]').innerText(), /Entry|New cycle|Before the first trade/, name);
    }
    const s = p.locator('[data-rp="stage"] svg');
    if (await s.count()) {
      assert.match(await s.getAttribute('viewBox'), /^0 0 420 380$/, `${name}: drawn for the phone, not shrunk`);
      const w = await s.evaluate(x => x.getBoundingClientRect().width);
      const cw = await p.locator('[data-rp="stage"]').evaluate(x => x.clientWidth);
      assert.ok(w <= cw + 1, `${name}: the chart fits`);
    }
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${name}: nothing sideways`);
    assert.deepEqual(ctx.errors, [], name);
    await ctx.close();
  }
});
