// The repair marks on every page (ui/repairs.js, from /v2/book's repairs):
// a day flagged by the end-of-day check and not repaired yet is marked
// "Flagged", a repaired one "Repaired", and each opens that day's case on
// the Repair page — from inside a clickable row too. Seeded by
// tests/e2e/serve.py: seed_day_checks (Nifty Strangle A flagged yesterday),
// seed_repair (a repair made by the script on BankNifty DTT Plain),
// seed_repair_cases (Bankex Strangle: repaired yesterday, its cycle still
// open) and seed_old_repair (BankNifty DTT Advanced: three weeks back).
// A repair marks the deployment only while its cycle is the current one;
// the cycle keeps its mark wherever it's listed. A flagged day stays
// marked until it's repaired or dismissed from its case.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

const FLAGGED = IDS['Nifty Strangle A'], SCRIPTED = IDS['BankNifty DTT Plain'];
let b, marks;
test.before(async () => {
  b = await browser();
  const ctx = await context(b);
  marks = (await api(ctx, '/v2/book')).repairs;
  await ctx.close();
});
test.after(async () => { await b.close(); });
const flagOf = id => marks.find(m => m.deployment_id === id && m.state === 'flagged');
const repairOf = id => marks.find(m => m.deployment_id === id && m.state === 'repaired');

test('the book carries each deployment’s flagged and repaired days', () => {
  assert.ok(flagOf(FLAGGED), 'Nifty Strangle A: flagged yesterday, not repaired');
  assert.match(flagOf(FLAGGED).headline, /would have made/);
  assert.ok(repairOf(SCRIPTED), 'BankNifty DTT Plain: repaired by the script');
});

test('lists mark the deployment, and the mark opens its case', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.waitForSelector('.dep-tbl .tr:not(.th)');
  const f = flagOf(FLAGGED);
  const flag = p.locator(`.dep-tbl a.fix-flag[href="#/repair/${f.day}/${FLAGGED}"]`);
  assert.equal(await flag.count(), 1);
  assert.match(await flag.getAttribute('data-tip'), /Flagged by the end-of-day check[\s\S]*would have made/);
  assert.equal(await p.locator(`.dep-tbl a[href^="#/repair/"][href$="/${SCRIPTED}"]`).count(), 1, 'the repaired one too');
  // the Desk's lanes are links themselves: a plain mark there
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('.lane');
  assert.ok(await p.locator('.lane-name span.fix-flag').count() >= 1);
  assert.equal(await p.locator('.lane-name a.fix-flag, .lane-name a.fix-tag').count(), 0, 'no link inside a link');
  // from the list to the case
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.locator(`.dep-tbl a.fix-flag[href="#/repair/${f.day}/${FLAGGED}"]`).click();
  await p.waitForSelector('.rc-found');
  assert.match(await p.locator('.rc-found').innerText(), /Traded differently/i);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a deployment’s pages: the header, Performance, and a repaired trade that opens its repair', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const r = repairOf(SCRIPTED);
  await p.goto(`${BASE}/linck/#/d/${SCRIPTED}/performance`);
  await p.waitForSelector('[data-repairs-line]');
  assert.ok(await p.locator('.dep-head .fix-tag, .dep-head .fix-flag').count() >= 1, 'marked in the header');
  assert.equal(await p.locator(`[data-repairs-line] a[href="#/repair/${r.day}/${SCRIPTED}"]`).count(), 1);
  // the trades tab: the row is clickable (it opens the trade's replay), its mark goes to the repair
  await p.goto(`${BASE}/linck/#/d/${SCRIPTED}/trades`);
  await p.waitForSelector('.trade-grid.click a.fix-tag');
  await p.locator('.trade-grid.click a.fix-tag').click();
  await p.waitForFunction(day => location.hash.startsWith(`#/repair/${day}/`), r.day);
  await p.waitForSelector('.rc-state.t-ok');
  // made by the script: no check of the day, and the case says so
  const c = await api(ctx, `/v2/repair-cases/${r.day}/${SCRIPTED}`);
  if (c.check === null) assert.match(await p.locator('.rc-found').innerText(), /Not checked[\s\S]*stall-repair script/i);
  assert.match(await p.locator('.rc-state').innerText(), /Repaired/);
  // the deployment page's note links to the same case
  await p.goto(`${BASE}/linck/#/d/${SCRIPTED}/now`);
  await p.waitForSelector('[data-repairs] .fix-note');
  await p.locator('[data-repairs] .fix-note summary').click();
  assert.equal(await p.locator(`[data-repairs] a[href="#/repair/${r.day}/${SCRIPTED}"]`).count(), 1);
  assert.doesNotMatch(await p.locator('[data-repairs]').innerText(), /repair script again/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the Geek header and the week in review', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const f = flagOf(FLAGGED);
  await p.goto(`${BASE}/linck/#/d/${FLAGGED}/geek`);
  await p.waitForSelector(`a.fix-flag[href="#/repair/${f.day}/${FLAGGED}"]`);
  await p.goto(`${BASE}/linck/#/review?w=${f.day}`);
  await p.waitForSelector('.rv-flag');
  const link = p.locator(`a.rv-flag[href="#/repair/${f.day}/${FLAGGED}"]`);
  assert.equal(await link.count(), 1);
  assert.match(await link.innerText(), /Flagged[\s\S]*Nifty Strangle A/i);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

const CURRENT = IDS['Bankex Strangle'], OLD = IDS['BankNifty DTT Advanced'];

test('a deployment is marked Repaired only while the repaired cycle is its current one', async () => {
  const cur = repairOf(CURRENT), old = repairOf(OLD);
  assert.ok(cur && cur.cycle.current, 'Bankex: repaired yesterday, its cycle still open');
  assert.equal(cur.cycle.to, null, 'an open positional cycle');
  assert.ok(old && !old.cycle.current && old.cycle.from === old.day, 'three weeks back: a day, long over');
  const ctx = await context(b);
  const p = await ctx.newPage();
  // (Bankex is in the Strategy Lab, which the list leaves out: its own page)
  await p.goto(`${BASE}/linck/#/d/${CURRENT}/now`);
  await p.waitForSelector('.dep-head');
  const curTag = p.locator(`.dep-head a.fix-tag[href="#/repair/${cur.day}/${CURRENT}"]`);
  await curTag.waitFor();
  assert.match(await curTag.getAttribute('data-tip'), /current cycle \(.+ – now\) was repaired/, 'the current cycle: marked');
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.waitForSelector('.dep-tbl .tr:not(.th)');
  assert.ok(await p.locator('.dep-tbl .tr', { hasText: 'BankNifty DTT Advanced' }).count(), 'listed');
  assert.equal(await p.locator(`.dep-tbl a[href^="#/repair/"][href$="/${OLD}"]`).count(), 0, 'an old cycle: the deployment isn’t marked');
  // its cycle still is: the Performance line counts it, by cycle
  await p.goto(`${BASE}/linck/#/d/${OLD}/performance`);
  await p.waitForSelector('[data-repairs-line]');
  assert.match(await p.locator('[data-repairs-line]').innerText(), /Includes a repaired cycle/);
  assert.equal(await p.locator(`[data-repairs-line] a[href="#/repair/${old.day}/${OLD}"]`).count(), 1);
  assert.equal(await p.locator('.dep-head .fix-tag').count(), 0, 'and not in its header');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the leaderboard counts the repaired cycles in the period it ranks', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/leaderboard`);
  await p.waitForSelector('.lb-tbl .tr:not(.th)');
  await p.click('[data-act="period"][data-v="all"]');
  await p.waitForTimeout(300);
  const row = p.locator('.lb-tbl .tr', { hasText: 'BankNifty DTT Advanced' });
  assert.match(await row.innerText(), /Repaired cycle/i, 'all time: its old repaired cycle');
  await p.click('[data-act="period"][data-v="month"]');
  await p.waitForTimeout(300);
  const old = repairOf(OLD).day, month = new Date().toISOString().slice(0, 7);
  if (old.slice(0, 7) !== month) assert.doesNotMatch(await row.innerText(), /Repaired cycle/i, 'this month: not repaired');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a flagged day can be dismissed from its case, and brought back', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const f = flagOf(FLAGGED);
  await p.goto(`${BASE}/linck/#/repair/${f.day}/${FLAGGED}`);
  await p.waitForSelector('.rc-found [data-act="dismiss"]');
  await p.click('.rc-found [data-act="dismiss"]');
  await p.waitForSelector('.rc-found [data-dismissed]');
  let now = (await api(ctx, '/v2/book')).repairs;
  assert.equal(now.filter(m => m.deployment_id === FLAGGED && m.day === f.day && m.state === 'flagged').length, 0, 'no longer flagged');
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.waitForSelector('.dep-tbl .tr:not(.th)');
  assert.equal(await p.locator(`.dep-tbl a.fix-flag[href="#/repair/${f.day}/${FLAGGED}"]`).count(), 0, 'nor marked in the list');
  // the day check says so, and undo brings it back
  await p.goto(`${BASE}/linck/#/repair/${f.day}/${FLAGGED}`);
  await p.waitForSelector('.rc-found [data-act="undismiss"]');
  await p.click('.rc-found [data-act="undismiss"]');
  await p.waitForSelector('.rc-found [data-act="dismiss"]');
  now = (await api(ctx, '/v2/book')).repairs;
  assert.equal(now.filter(m => m.deployment_id === FLAGGED && m.day === f.day && m.state === 'flagged').length, 1, 'flagged again');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
