// Stall repairs in the UI (ui/repairs.js, the "Repaired" tag): the
// deployment page notes each repair with its trades; the trade, the
// straddle and the replay's episode, trades and legs it wrote are tagged.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

const ID = IDS['BankNifty DTT Plain'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('the deployment page notes the repair, and what it wrote is tagged everywhere', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const { repairs } = await api(ctx, `/v2/deployments/${ID}/repairs`);
  assert.equal(repairs.length, 1);
  await p.goto(`${BASE}/linck/#/d/${ID}/now`);
  await p.waitForSelector('[data-repairs] .fix-note');
  const note = p.locator('[data-repairs] .fix-note');
  assert.match(await note.innerText(), /12:16–15:30/);
  assert.match(await note.innerText(), /2 trades replayed from 1-minute candles/);
  await note.locator('summary').click();
  assert.match(await note.innerText(), /13:05 up first/);
  assert.equal(await note.locator('.fix-trades > div').count(), 2);

  // the trades tab: exactly the repaired trade
  await p.goto(`${BASE}/linck/#/d/${ID}/trades`);
  await p.waitForSelector('.trade-grid.click');
  assert.equal(await p.locator('.trade-grid.click .fix-tag').count(), 1);

  // Geek: the straddle, and the replay of it
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/straddles`);
  await p.waitForSelector('#gkView .gk-tbl .tr:not(.th)');
  assert.equal(await p.locator('#gkView .gk-tbl .fix-tag').count(), 1);
  const eps = (await api(ctx, `/v2/deployments/${ID}/geek/replay`)).episodes;
  const fixed = eps.filter(e => e.repaired);
  assert.equal(fixed.length, 1, 'one episode was repaired');
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay?from=${fixed[0].n}&to=${fixed[0].n}`);
  await p.waitForSelector('[data-rp="strip"] .rp-cell');
  assert.match(await p.locator('[data-pick="from"] option:checked').innerText(), /repaired/);
  assert.equal(await p.locator('[data-ep] .fix-tag').count(), 1, 'tagged in the episode list');
  assert.ok(await p.locator('.rp-li .fix-tag').count() >= 1, 'its exit, in the event list');
  await p.keyboard.press(']');
  assert.match(await p.locator('[data-rp="banner"]').innerText(), /repaired/i);
  assert.match(await p.locator('[data-rp="banner"] .rp-chip').innerText(), /Exit/i, 'the repaired exit');
  assert.ok(await p.locator('[data-rp="pos"] .fix-tag').count() >= 1);
  // a deployment with no repair has no note
  await p.goto(`${BASE}/linck/#/d/${IDS['Nifty Strangle A']}/now`);
  await p.waitForSelector('.tab-body');
  await p.waitForTimeout(800);
  assert.equal(await p.locator('[data-repairs] .fix-note').count(), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
