// The Alerts panel (ui/alertspanel.js) from the header's Alerts button:
// every alert, not just the Desk's first five, each with its action and
// Dismiss; Dismiss all; dismissed ones can be brought back. Dismissals are
// kept on the server per user (app/v2/alerts.py), so they hold on every
// device, and the other open devices update at once.
//
// Signed in as its own user, so its dismissals can't touch the other
// specs; the health monitor's own alerts are global, so dismissing those
// is intercepted here (ops.e2e covers the real thing).
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, until } from './helper.mjs';

const USER = 'alerts-e2e', PASS = 'alerts-e2e-pass-1';
let b;

async function device(opts = {}) {
  const ctx = await context(b, { signedIn: false, blockSW: true, ...opts });
  const r = await ctx.request.post(`${BASE}/auth/login`, { data: { username: USER, password: PASS } });
  if (!r.ok()) throw new Error(`sign-in failed: ${r.status()}`);
  return ctx;
}
const rows = p => p.locator('.attn-row');
const panelItems = p => p.locator('.apanel .ap-item:not(.off)');

test.before(async () => {
  b = await browser();
  const admin = await context(b);
  await admin.request.post(`${BASE}/auth/users`, { data: { username: USER, password: PASS } });   // already there on a rerun: fine
  await admin.close();
});
test.after(async () => {
  const ctx = await device();
  const keys = Object.keys(await (await ctx.request.get(`${BASE}/v2/alerts/dismissed`)).json());
  if (keys.length) await ctx.request.post(`${BASE}/v2/alerts/dismissed`, { data: { restore: keys } });
  await ctx.close();
  await b.close();
});

test('the Alerts button lists every alert, and the Desk links to them past its first five', async () => {
  const ctx = await device();
  // five more deployments that aren't running: seven deployment alerts in all
  await ctx.route('**/health', async r => {
    const h = await (await r.fetch()).json();
    const extra = [1, 2, 3, 4, 5].map(i => ({ id: `fake-${i}`, name: `Fake ${i}`, reason: `test ${i}`, open_positions: 0 }));
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...h, not_running: [...(h.not_running || []), ...extra] }) });
  });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await until(async () => (await rows(p).count()) === 5);
  const desk = Number((await p.locator('.attn-n').innerText()).trim());
  assert.ok(desk >= 7, `at least seven on the Desk (${desk})`);
  const all = p.locator('[data-act="allAlerts"]');
  assert.match(await all.innerText(), new RegExp(`See all ${desk}`));
  await all.click();
  await p.waitForSelector('.apanel');
  const n = Number((await p.locator('.alerts-btn i').innerText()).trim());
  assert.equal(await panelItems(p).count(), n, 'the panel lists as many as the header counts');
  assert.ok(n >= desk, 'the Desk’s alerts plus the health monitor’s');
  assert.match(await p.locator('.apanel').innerText(), /Fake 5 isn't running/, 'the last one too');
  // Escape closes it; the header button opens it on any page
  await p.keyboard.press('Escape');
  await until(async () => (await p.locator('.apanel').count()) === 0);
  await p.goto(`${BASE}/linck/#/leaderboard`);
  await p.click('.alerts-btn');
  await p.waitForSelector('.apanel .ap-item');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a dismissal on one device holds on the other, at once and after a reload; Bring back undoes it on both', async () => {
  const A = await device(), B = await device();
  const pa = await A.newPage(), pb = await B.newPage();
  await pa.goto(`${BASE}/linck/#/desk`);
  await pb.goto(`${BASE}/linck/#/desk`);
  await rows(pa).first().waitFor();
  await rows(pb).first().waitFor();
  await pa.click('.alerts-btn');
  const item = pa.locator('.apanel .ap-item[data-key]').first();
  await item.waitFor();
  const title = (await item.locator('b').innerText()).trim();
  const onDesk = p => p.locator('.attn-row', { hasText: title });
  assert.equal(await onDesk(pb).count(), 1, `B shows “${title}”`);
  await item.locator('[data-ap="dismiss"]').click();
  await pa.waitForSelector('text=Dismissed on all your devices.');
  assert.equal(await until(async () => (await onDesk(pb).count()) === 0), true, 'gone on B without a reload');
  await pb.reload();
  await pb.waitForSelector('.attn-head');
  await pb.waitForTimeout(800);
  assert.equal(await onDesk(pb).count(), 0, 'and after one');
  // B brings it back from its panel's dismissed list: back on A too
  await pb.click('.alerts-btn');
  await pb.click('.apanel [data-ap="toggle-off"]');
  await pb.locator('.apanel .ap-item.off', { hasText: title }).locator('[data-ap="restore"]').click();
  await pa.keyboard.press('Escape');
  assert.equal(await until(async () => (await onDesk(pa).count()) === 1), true, 'back on A');
  assert.deepEqual([...A.errors, ...B.errors], []);
  await A.close(); await B.close();
});

test('Dismiss all clears every alert, the health monitor’s too', async () => {
  const ctx = await device();
  let closed = 0;
  await ctx.route('**/v2/ops/issues/*/dismiss', r => { closed += 1; return r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }); });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await rows(p).first().waitFor();
  await p.click('.alerts-btn');
  await p.waitForSelector('.apanel .ap-item');
  const monitor = await p.locator('.apanel .ap-item[data-issue]').count();
  await p.click('.apanel [data-ap="all"]');
  await until(async () => (await p.locator('.apanel .ap-item[data-key]').count()) === 0);
  assert.equal(await p.locator('.apanel .ap-item[data-key]').count(), 0, 'every deployment alert dismissed');
  await until(async () => closed === monitor);   // sent after the deployment alerts are gone
  assert.equal(closed, monitor, 'each monitor alert dismissed');
  await p.keyboard.press('Escape');
  await until(async () => (await rows(p).count()) === 0);
  assert.match(await p.locator('.attn-head').innerText(), /all clear/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone the panel fills the screen and fits', async () => {
  const ctx = await device({ width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.click('.alerts-btn');
  await p.waitForSelector('.apanel');
  const w = await p.locator('.apanel').evaluate(el => el.getBoundingClientRect().width);
  assert.ok(w >= 389, `full width (${w}px)`);
  assert.ok(await p.evaluate(() => document.documentElement.scrollWidth - innerWidth) <= 1, 'no sideways scroll');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
