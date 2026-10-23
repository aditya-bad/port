// Strategy health alerts: a deployment drifting from how it normally
// trades shows on the Desk with the other things that need you, on its
// lane, and goes away when dismissed until the next trade changes it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => {
  // dismissals are kept on the server per user now: leave none behind for the other specs
  const ctx = await context(b);
  const keys = Object.keys(await (await ctx.request.get(`${BASE}/v2/alerts/dismissed`)).json());
  if (keys.length) await ctx.request.post(`${BASE}/v2/alerts/dismissed`, { data: { restore: keys } });
  await ctx.close();
  await b.close();
});

test('a drifting deployment needs you on the Desk, until dismissed', async () => {
  const ID = IDS['BankNifty DTT Plain'];
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  // what the server would find (the rules themselves: tests/unit/test_strategy_health.py)
  const finding = { rule: 'win_rate', key: 'win_rate:2026-10-20T08:30:00+00:00', title: 'Winning less often',
    body: 'Won 20% of the last 10 trades against 60% before.', since: '2026-10-06', data: { recent: 20, usual: 60 }, deployment_id: ID, name: 'BankNifty DTT Plain' };
  let findings = { [ID]: [finding] };
  await ctx.route('**/v2/strategy-health*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ checked_at: new Date().toISOString(), deployments: findings }) }));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  const item = p.locator('.attn-row', { hasText: 'BankNifty DTT Plain: winning less often' });
  await item.waitFor();
  assert.match(await item.innerText(), /Won 20% of the last 10 trades against 60% before\./);
  // its lane says so too
  const lane = p.locator('.lane', { has: p.locator(`[data-today="${ID}"]`) });
  assert.match(await lane.locator('.lane-name').innerText(), /winning less often/);
  // Open goes to its performance
  await item.locator('[data-act="alert"]').click();
  await p.waitForURL(new RegExp(`#/d/${ID}/performance`));
  await p.goto(`${BASE}/linck/#/desk`);
  await item.waitFor();
  // dismissed: gone, and stays gone after a reload
  await item.locator('[data-act="dismiss"]').click();
  await p.waitForFunction(() => ![...document.querySelectorAll('.attn-row')].some(r => r.innerText.includes('winning less often')));
  await p.reload();
  await p.waitForSelector('.attn-head');
  await p.waitForTimeout(800);
  assert.equal(await p.locator('.attn-row', { hasText: 'winning less often' }).count(), 0);
  // a new trade closes and it's still drifting: a new key, back again
  findings = { [ID]: [{ ...finding, key: 'win_rate:2026-10-21T08:30:00+00:00' }] };
  await p.reload();
  await p.locator('.attn-row', { hasText: 'winning less often' }).waitFor();
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the server answers for every deployment', async () => {
  const ctx = await context(b);
  const r = await ctx.request.get(`${BASE}/v2/strategy-health?fresh=true`);
  assert.equal(r.status(), 200);
  const body = await r.json();
  assert.ok(body.checked_at && typeof body.deployments === 'object');
  for (const fs of Object.values(body.deployments)) for (const f of fs) assert.ok(f.title && f.body && f.key && f.deployment_id);
  await ctx.close();
});

test('a VIX spike needs you on the Desk: what holds sold premium, dismissed for the day', async () => {
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  const ID = IDS['Nifty Strangle A'];
  let spike = { spiking: true, vix: 16.4, prev_close: 14.5, change: 1.9, change_pct: 13.1, at: '2026-10-21T11:00:00+05:30',
    exposed: [{ id: ID, name: 'Nifty Strangle A', legs: 4, premium: 52000 }, { id: IDS['BankNifty DTT Plain'], name: 'BankNifty DTT Plain', legs: 2, premium: 30000 }] };
  await ctx.route('**/v2/vix/spike*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(spike) }));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  const item = p.locator('.attn-row', { hasText: 'VIX up 13% today: 14.50 → 16.40' });
  await item.waitFor();
  assert.match(await item.innerText(), /2 deployments hold sold premium that loses as VIX rises: Nifty Strangle A, BankNifty DTT Plain\./);
  await item.locator('[data-act="alert"]').click();
  await p.waitForURL(new RegExp(`#/d/${ID}/geek/volatility`));
  await p.goto(`${BASE}/linck/#/desk`);
  await item.waitFor();
  await item.locator('[data-act="dismiss"]').click();
  await p.waitForFunction(() => ![...document.querySelectorAll('.attn-row')].some(r => /VIX up/.test(r.innerText)));
  await p.reload();
  await p.waitForSelector('.attn-head');
  await p.waitForTimeout(800);
  assert.equal(await p.locator('.attn-row', { hasText: 'VIX up' }).count(), 0, 'dismissed for the day');
  // the next day's spike is new
  spike = { ...spike, at: '2026-10-22T10:30:00+05:30', exposed: [] };
  await p.reload();
  const next = p.locator('.attn-row', { hasText: 'VIX up' });
  await next.waitFor();
  assert.match(await next.innerText(), /Nothing open is selling premium/);
  await next.locator('[data-act="alert"]').click();   // "Got it"
  await p.waitForFunction(() => ![...document.querySelectorAll('.attn-row')].some(r => /VIX up/.test(r.innerText)));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('prices stopped while "connected": the Desk says so, not "live"', async () => {
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  await ctx.route('**/health', async r => {
    const h = await (await r.fetch()).json();
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...h, kite_connected: true, needs_login: false, ticks_stalled_s: 600 }) });
  });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('.kite-banner[data-stalled]');
  assert.match(await p.innerText('.kite-banner'), /Kite has stopped sending prices\.[\s\S]*no prices for 10 min/);
  assert.match(await p.textContent('.kite-chip'), /Prices stopped · reconnecting/, 'the long label (a narrow header shows "Reconnecting")');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the Desk shows what open legs gain or lose if VIX rises a point', async () => {
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  const real = await (await ctx.request.get(`${BASE}/v2/vix/exposure`)).json();
  assert.ok(Array.isArray(real.deployments) && typeof real.total === 'number', 'the server answers');
  await ctx.route('**/v2/vix/exposure', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(
    { total: -8200, legs: 6, missing: 1, deployments: [{ id: 'x', name: 'Nifty Strangle A', legs: 4, per_point: -9000 }, { id: 'y', name: 'ST Inverse', legs: 1, per_point: 800 }] }) }));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  const sub = p.locator('[data-vix-sub]');
  await sub.waitFor();
  assert.match(await sub.innerText(), /If VIX \+1\s+−₹8,200/);
  assert.match(await sub.getAttribute('data-tip'), /Nifty Strangle A {2}−₹9,000[\s\S]*1 leg left out/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
