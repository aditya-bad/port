// Shadow mode (app/shadow.py): what Kite would have done with each paper
// fill. The server seeds BankNifty DTT Plain's latest fills: today's
// first entry leg Kite would refuse (lot size), one fills ₹ worse, the
// rest at the paper price (tests/e2e/serve.py seed_shadow).
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

const ID = IDS['BankNifty DTT Plain'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => {
  // dismissals are kept per user: leave none behind
  const ctx = await context(b);
  const keys = Object.keys(await (await ctx.request.get(`${BASE}/v2/alerts/dismissed`)).json()).filter(k => k.startsWith('shadow:'));
  if (keys.length) await ctx.request.post(`${BASE}/v2/alerts/dismissed`, { data: { restore: keys } });
  await ctx.close();
  await b.close();
});

for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
  test(`the Now tab says what Kite would have done with each fill (${label})`, async () => {
    const ctx = await context(b, { width, height: 900 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${ID}/now`);
    const panel = p.locator('[data-shadow-panel]');
    await panel.locator('[data-shadow]').first().waitFor();
    const head = await panel.locator('.sect').innerText();
    // the seeded twelve, plus any fill another spec made since (checked for real)
    const total = Number(head.match(/(\d+) fills checked/)[1]);
    assert.ok(total >= 12, head);
    assert.match(head, /Would Kite take these\?[\s\S]*fills checked · \d+ would fill · 1 worse · 1 rejected/);
    const rej = panel.locator('[data-shadow="reject"]');
    assert.equal(await rej.count(), 1);
    assert.match(await rej.innerText(), /Would be rejected: lot size/);
    assert.match(await rej.getAttribute('data-tip'), /Lot size: Kite takes whole lots only[\s\S]*Freeze limit: Within the freeze limit of 1,050 \(config\)/);
    assert.match(await panel.locator('[data-shadow="worse"]').innerText(), /Would fill ₹\d+ worse/);
    assert.equal(await panel.locator('.sh-row').count(), 8, 'the latest eight');
    // ready for live? not yet: a refusal in its last ten sessions, too short a record
    const card = panel.locator('.ready-card[data-ready="not_yet"]');
    assert.match(await card.innerText(), /Not ready for live yet/);
    assert.match(await card.locator('[data-rule="record"]').innerText(), /At least 20 sessions in shadow\s+\d+ of 20/);
    assert.match(await card.locator('li.no[data-rule="rejects"]').innerText(), /Nothing Kite would refuse in the last 10 sessions\s+1 refused/);
    assert.match(await card.locator('.ready-stats').innerText(), /Kite would accept[\s\S]*%[\s\S]*Worse than paper/);
    await panel.locator('[data-act="shadowAll"]').click();
    assert.equal(await p.locator('[data-shadow-panel] .sh-row').count(), total, 'then all of them');
    await p.locator('[data-shadow-panel] [data-act="shadowAll"]').click();
    assert.equal(await p.locator('[data-shadow-panel] .sh-row').count(), 8, 'and back');
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}

test('each order on a day’s page carries its verdict', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/cycles`);
  await p.locator('.day-grid.click').first().click();   // the latest closed day
  await p.waitForSelector('.ss-orders', { timeout: 15000 });
  await p.locator('.ss-orders [data-shadow]').first().waitFor();
  const chips = await p.$$eval('.ss-orders [data-shadow]', els => els.map(e => e.dataset.shadow));
  assert.ok(chips.length >= 2, `its entry and exit (${chips.join(',')})`);
  assert.ok(chips.every(c => ['fill', 'worse', 'reject', 'unknown', 'partial', 'no_fill'].includes(c)), chips.join(','));
  const ok = p.locator('.ss-orders [data-shadow="fill"], .ss-orders [data-shadow="worse"]').first();
  assert.match(await ok.getAttribute('data-tip'), /limit ₹[\d,.]+[\s\S]*Order book: Fills on the book/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('an order Kite would refuse is an alert, once for the day', async () => {
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.click('.alerts-btn');
  const item = p.locator('.apanel .ap-item', { hasText: 'BankNifty DTT Plain: Kite would have refused an order today' });
  await item.waitFor();
  assert.match(await item.innerText(), /Lot size: Kite takes whole lots only[\s\S]*The paper trade went through; live, it wouldn’t have\./);
  await item.locator('[data-ap="run"]').click();
  await p.waitForURL(new RegExp(`#/d/${ID}/now`));
  await p.click('.alerts-btn');
  await item.waitFor();
  await item.locator('[data-ap="dismiss"]').click();
  await p.waitForFunction(() => ![...document.querySelectorAll('.apanel .ap-item[data-key]')].some(r => r.innerText.includes('would have refused')));
  await p.reload();
  await p.waitForSelector('.alerts-btn');
  await p.waitForTimeout(800);
  await p.click('.alerts-btn');
  await p.waitForSelector('.apanel');
  assert.equal(await p.locator('.apanel .ap-item[data-key]', { hasText: 'would have refused' }).count(), 0, 'dismissed for the day');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Geek mode: Live-ready, with the card, the days and every fill', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/shadow`);
  await p.waitForSelector('#gkView .ready-card');
  const chart = p.locator('#gkView svg[aria-label="Worse than paper, day by day"]');
  assert.ok(await chart.locator('.hit[data-tip]').count() >= 2, 'a tip on each day');
  assert.match(await chart.locator('.hit[data-tip]').last().getAttribute('data-tip'), /Fills checked {2}\d+[\s\S]*Kite would refuse {2}1/);
  await p.locator('#gkView [data-act="v"][data-k="reject"]').click();
  assert.equal(await p.locator('#gkView .gk-tbl .tr:not(.th)').count(), 1, 'the one Kite would refuse');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the Leaderboard and Compare say which deployments are ready for live', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/leaderboard`);
  const chip = p.locator(`[data-ready-of="${ID}"]`);
  await chip.waitFor();
  assert.equal((await chip.innerText()).trim(), 'Not yet');
  assert.match(await chip.getAttribute('data-tip'), /✗ Nothing Kite would refuse in the last 10 sessions {2}1 refused/);
  assert.equal((await p.locator(`[data-ready-of="${IDS['Nifty Strangle B']}"]`).innerText()).trim(), 'No record');
  await p.goto(`${BASE}/linck/#/compare?ids=${ID},${IDS['Nifty Strangle B']}`);
  const row = p.locator('.mrow', { hasText: 'Ready for live' }).first();
  await row.waitFor();
  assert.match(await row.innerText(), /Not yet · \d to clear[\s\S]*No record/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the server answers for any deployment', async () => {
  const ctx = await context(b);
  const r = await ctx.request.get(`${BASE}/v2/deployments/${IDS['Nifty Strangle A']}/shadow`);
  assert.equal(r.status(), 200);
  const body = await r.json();
  assert.ok(Array.isArray(body.checks) && typeof body.counts === 'object' && body.enabled === true);
  await ctx.close();
});
