// Geek mode's Replay (pages/geek/replay.js): a cycle played back with
// the index, VIX, the P&L and its split on one axis; play stops at each
// event, keys step and jump, an event in the list jumps there, a chain of
// cycles is picked From…To, and it fits a phone. Every deployment has it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

const ID = IDS['Nifty Monthly Strangle'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const step = p => p.evaluate(() => +document.querySelector('[data-range]').value);
const ready = p => p.waitForSelector('.rp-grid [data-rp="now"] .rp-when', { timeout: 20000 });

test('a cycle replays: charts, the event card, play stops at the next event, keys and the list jump', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  assert.ok(d.frames.length > 10 && d.events.length >= 2, 'the fixture cycle has steps and events');
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  assert.equal(await p.locator('.rp-grid svg.gk-chart').count(), 4, 'index, VIX, P&L and the split');
  assert.match(await p.locator('[data-rp="ev"]').innerText(), /New cycle 10/);
  assert.match(await p.locator('[data-rp="ev"]').innerText(), /First closed cycle 9/);
  assert.equal(await step(p), 0);

  // play: stops at the next event, which becomes the card
  const next = d.events.find(e => e.frame > 0);
  await p.click('[data-act="speed"][data-s="3"]');
  await p.click('[data-act="play"]');
  await p.waitForFunction(f => +document.querySelector('[data-range]').value === f, next.frame, { timeout: 20000 });
  await p.waitForTimeout(400);
  assert.equal(await step(p), next.frame, 'stopped at the event');
  assert.match(await p.locator('.rp-play').innerText(), /Play/);
  assert.match(await p.locator('[data-rp="ev"]').innerText(), new RegExp(next.label));
  assert.ok(await p.locator('.rp-ev-card.here').count(), 'the card is for this very step');

  // keys: a step on, and back to the previous event
  await p.keyboard.press('ArrowRight');
  assert.equal(await step(p), next.frame + 1);
  await p.keyboard.press('[');
  assert.equal(await step(p), next.frame);
  await p.keyboard.press('[');
  assert.equal(await step(p), 0);
  // the list: click the last event
  await p.locator('.rp-li').last().click();
  assert.equal(await step(p), d.events[d.events.length - 1].frame);
  // the open legs follow the playhead
  assert.ok(await p.locator('[data-rp="legs"] .tr:not(.th)').count() >= 1);
  // the playhead moved on every chart
  const heads = await p.evaluate(() => [...document.querySelectorAll('[data-rp-head]')].map(l => +l.getAttribute('x1')));
  assert.ok(heads.length === 4 && heads.every(x => x === heads[0] && x > 64));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a chain of cycles: From and To replay them as one, each new cycle marked', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  const n = await p.locator('[data-pick="from"] option').count();
  await p.selectOption('[data-pick="from"]', String(n - 2));
  await p.waitForURL(new RegExp(`replay\\?from=${n - 2}&to=${n}$`));
  await ready(p);
  assert.match(await p.locator('.rp-pick').innerText(), /a chain of 3/);
  const news = await p.locator('.rp-li .rp-chip', { hasText: 'New cycle' }).count();
  assert.equal(news, 3, 'each cycle starts with its own marker');
  // an episode row replays that one alone
  await p.locator('[data-ep]').nth(1).click();
  await p.waitForURL(/replay\?from=(\d+)&to=\1$/);
  await ready(p);
  assert.match(await p.locator('.rp-pick').innerText(), /one episode/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('every kind of deployment has it, and it fits a phone', async () => {
  for (const name of ['BankNifty DTT Plain', 'Pivot Supertrend Options', 'BankNifty DTT Adjusted']) {
    const ctx = await context(b, { width: 390, height: 844 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${IDS[name]}/geek/replay`);
    await p.waitForFunction(() => { const el = document.querySelector('#gkView'); return el && el.children.length && !el.querySelector('.skel'); }, null, { timeout: 20000 });
    const txt = await p.locator('#gkView').innerText();
    assert.match(txt, /Replay/, name);
    if (await p.locator('.rp-grid').count()) {
      assert.match(await p.locator('[data-rp="ev"]').innerText(), /Entry|New cycle|Before the first event/, name);
    }
    const sideways = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(sideways, false, `${name}: nothing sideways on a phone`);
    assert.deepEqual(ctx.errors, [], name);
    await ctx.close();
  }
});
