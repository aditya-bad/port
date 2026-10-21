// Geek mode's Replay (pages/geek/replay.js): a cycle played bar by bar —
// candles marching in with the open legs' strikes, each event flagged, the
// P&L per leg, VIX — and every position appearing in the table as it's
// opened. Play stops at each event; keys step and jump; an event in the
// list jumps there; a chain of cycles is picked From…To; it fits a phone.
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

test('a cycle replays bar by bar, positions appear as they open, play stops at the next event', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  assert.ok(d.frames.length > 10 && d.events.length >= 2, 'the fixture cycle has bars and events');
  const adj = d.events.find(e => e.frame > 0);
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  assert.equal(await p.locator('[data-rp="stage"] svg.gk-chart').count(), 1, 'one stage');
  assert.match(await p.locator('[data-rp="banner"]').innerText(), /New cycle 10/);
  assert.match(await p.locator('[data-rp="banner"]').innerText(), /first closed cycle 9/);
  const opened0 = d.legs.filter(l => l.t0 < d.frames[1].t).length;
  assert.equal(await rows(p), opened0, 'only what is open at the first bar');
  assert.match(await p.locator('[data-rp="stage"]').innerHTML(), />NEW</);

  // play: stops at the adjustment, which brings a new, highlighted position
  await p.click('[data-act="speed"][data-s="3"]');
  await p.click('[data-act="play"]');
  await p.waitForFunction(f => +document.querySelector('[data-range]').value === f, adj.frame, { timeout: 30000 });
  await p.waitForTimeout(300);
  assert.equal(await step(p), adj.frame, 'stopped at the event');
  assert.match(await p.locator('.rp-play').innerText(), /Play/);
  assert.match(await p.locator('[data-rp="banner"]').innerText(), new RegExp(adj.label));
  assert.ok(await p.locator('.rp-banner.here').count(), 'the banner is for this very bar');
  assert.ok(await rows(p) > opened0, 'the adjustment\'s new leg joined the table');
  assert.ok(await p.locator('[data-rp="pos"] .tr.rp-fresh').count() >= 1, 'and is highlighted');
  assert.match(await p.locator('[data-rp="stage"]').innerHTML(), />ADJ</);
  assert.match(await p.locator('[data-rp="pos"]').innerText(), /Closed .* Adjustment/);

  // keys and the event list
  await p.keyboard.press('ArrowRight');
  assert.equal(await step(p), adj.frame + 1);
  assert.equal(await p.locator('[data-rp="pos"] .tr.rp-fresh').count(), 0, 'highlight only on the bar it opened');
  await p.keyboard.press('[');
  assert.equal(await step(p), adj.frame);
  await p.keyboard.press('[');
  assert.equal(await step(p), 0);
  await p.locator('.rp-li').last().click();
  assert.equal(await step(p), d.events[d.events.length - 1].frame);
  // fewer bars on screen
  await p.click('[data-act="zoom"][data-z="60"]');
  assert.equal(await p.locator('[data-seg="zoom"] button.on').innerText(), '60 bars');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a chain of cycles: From and To replay them as one, each new cycle flagged', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  const n = await p.locator('[data-pick="from"] option').count();
  await p.selectOption('[data-pick="from"]', String(n - 2));
  await p.waitForURL(new RegExp(`replay\\?from=${n - 2}&to=${n}$`));
  await ready(p);
  assert.match(await p.locator('.rp-pick').innerText(), /a chain of 3/);
  assert.equal(await p.locator('.rp-li .rp-chip', { hasText: 'New cycle' }).count(), 3, 'each cycle starts with its own marker');
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
    assert.match(await p.locator('#gkView').innerText(), /Replay/, name);
    if (await p.locator('[data-rp="banner"]').count()) {
      assert.match(await p.locator('[data-rp="banner"]').innerText(), /Entry|New cycle|Before the first event/, name);
      // the newest bar is in view
      const atEnd = await p.evaluate(() => { const s = document.querySelector('.rp-stage'); return s.scrollWidth <= s.clientWidth + 1 || s.scrollLeft + s.clientWidth >= s.scrollWidth - 2; });
      assert.ok(atEnd, `${name}: the stage shows its newest bar`);
    }
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${name}: nothing sideways`);
    assert.deepEqual(ctx.errors, [], name);
    await ctx.close();
  }
});
