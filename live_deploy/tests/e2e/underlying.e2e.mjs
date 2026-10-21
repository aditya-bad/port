// The positions page against the index (ui/underlying.js): spot, the
// future that prices the legs and the basis, each leg's distance from
// both and its delta, and what a move of the future would do — and the
// same future in the Replay (pages/geek/replay.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

const ID = IDS['Nifty Monthly Strangle'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('the Now tab shows the legs against the spot and their future', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const u = await api(ctx, `/v2/deployments/${ID}/underlying`);
  assert.ok(u.available && u.future && u.basis && u.net, 'the fixture strangle has a known future');
  await p.goto(`${BASE}/linck/#/d/${ID}/now`);
  await p.waitForSelector('[data-underlying] .ul-tiles');
  const t = await p.locator('[data-underlying]').innerText();
  assert.match(t, new RegExp(u.future.symbol));
  assert.match(t, /Basis/); assert.match(t, /pts/); assert.match(t, /days/);
  assert.match(t, /for every point the future rises|flat/);
  assert.equal(await p.locator('[data-underlying] .ul-r:not(.th)').count(), u.legs.length);
  assert.equal(await p.locator('[data-underlying] .ul-b').count(), 8, 'eight moves of the future');
  // a deployment on an index with no known future says why, without breaking the page.
  // Whichever still holds legs when this runs: other specs close some (flow closes BankNifty DTT Plain's)
  let other = null;
  for (const id of Object.values(IDS)) {
    const x = await api(ctx, `/v2/deployments/${id}/underlying`).catch(() => null);
    if (x && x.available && !x.future && x.legs.length) { other = id; break; }
  }
  assert.ok(other, 'some fixture with legs on an index with no known future');
  await p.goto(`${BASE}/linck/#/d/${other}/now`);
  await p.waitForSelector('[data-underlying] .ul-tiles');
  assert.match(await p.locator('[data-underlying]').innerText(), /No futures contract is known/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the replay draws the future, its basis, and measures strikes from it', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  assert.ok(d.future && d.frames.some(f => f.fut != null));
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await p.waitForSelector('[data-rp="strip"] .rp-cell');
  assert.match(await p.locator('[data-rp="strip"]').innerText(), /Future · .*\n[\s\S]*basis/);
  await p.keyboard.press(']');
  assert.match(await p.locator('[data-rp="stage"]').innerHTML(), /from fut/);
  assert.match(await p.locator('[data-rp="pos"]').innerText(), /Future then/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
