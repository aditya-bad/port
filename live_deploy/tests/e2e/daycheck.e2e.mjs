// The Repair page's day check (pages/repair.js; app/repair/daycheck.py):
// yesterday's results worst first — the rule broken, the trades the replay
// made that the app didn't side by side, the end states — the ones as
// expected folded away, another day picked, and a run started by hand
// (the real worker, in its own process: with no Kite session it fails,
// and the page says why). Seeded by tests/e2e/serve.py: seed_day_checks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, api, until } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

test('yesterday’s check: what broke, side by side, worst first', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-row');
  assert.match(await p.locator('.dc-bar').innerText(), /Ran after the export, 15:52–16:03/);
  const counts = await p.locator('.dc-counts').innerText();
  assert.match(counts, /Rule broken\s*1/i);
  assert.match(counts, /Traded differently\s*1/i);
  assert.match(counts, /To review\s*1/i);
  assert.match(counts, /As expected\s*6/i);
  const rows = p.locator('.dc-list > .dc-row');
  assert.equal(await rows.count(), 3, 'the flagged ones, open');
  const rule = rows.nth(0);
  assert.match(await rule.innerText(), /BankNifty DTT Plain[\s\S]*Still holding 2 leg\(s\) after its 15:00 cut-off/);
  assert.equal(await rule.locator('.dc-trades > .missed').count(), 2);
  assert.match(await rule.locator('.dc-trades').innerText(), /15:00 Bought 35 BANKNIFTY26OCT55700CE[\s\S]*The app didn’t/);
  assert.match(await rule.locator('.dc-trades').innerText(), /₹\+1\.2 vs the candle/);
  assert.match(await rule.locator('.dc-end').innerText(), /would have ended with\s*Flat[\s\S]*The app ended with\s*Short 35 BANKNIFTY26OCT55700CE/i);
  assert.match(await rows.nth(1).innerText(), /Nifty Strangle A[\s\S]*continuous_50pct_trigger/);
  assert.match(await rows.nth(2).innerText(), /Nifty Monthly Strangle[\s\S]*Read the usual way at 13:05/);
  const fine = p.locator('.dc-fine');
  assert.match(await fine.locator('> summary').innerText(), /6 deployments as expected/);
  assert.equal(await fine.evaluate(el => el.open), false, 'folded away');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('another day, and a run started by hand reports how it went', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('[data-f="dcDay"]');
  const days = await p.locator('[data-f="dcDay"] option').evaluateAll(os => os.map(o => o.value));
  assert.equal(days.length, 2);
  await p.selectOption('[data-f="dcDay"]', days[1]);
  await until(async () => (await p.locator('.dc-fine > summary').innerText().catch(() => '')).includes('9 deployments as expected'));
  assert.equal(await p.locator('.dc-list > .dc-row').count(), 0, 'nothing flagged that day');
  // the real worker: no Kite session in this server, so it fails and says so
  await p.click('[data-act="runCheck"]');
  await until(async () => /Failed[\s\S]*No Kite session/.test(await p.locator('.dc-bar').innerText()), 60000);
  const r = await api(ctx, `/v2/day-checks?day=${days[1]}`);
  assert.equal(r.run.status, 'failed');
  assert.equal(r.results.length, 9, 'the day’s results are untouched');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone the day check stacks and fits', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-row');
  const over = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(over <= 1, `no sideways scroll (${over}px)`);
  const label = await p.locator('.dc-trades .missed [data-l]').first().evaluate(el => getComputedStyle(el, '::before').content);
  assert.match(label, /Strategy ·/, 'each stacked cell says whose it is');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

// Once a flagged day is repaired, or ignored (its numbers left as they
// are), it leaves the to-do list for Done: it used to stay listed with
// "Review and repair" after the repair was applied.
test('a flagged day you ignore moves to Done and stops being flagged; undo brings it back', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-list > .dc-row');
  const todo = p.locator('.dc-list > .dc-row');
  assert.equal(await todo.count(), 3);
  const row = todo.nth(1), dep = await row.getAttribute('data-dc');
  const day = (await api(ctx, '/v2/day-checks')).day;
  const flagged = async () => (await api(ctx, '/v2/book')).repairs.filter(m => m.deployment_id === dep && m.day === day && m.state === 'flagged').length;
  assert.equal(await flagged(), 1);
  // whatever happens below, leave the day flagged for the specs after this one
  const restore = () => ctx.request.post(`${BASE}/v2/repair-cases/${day}/${dep}/dismiss`, { data: { undo: true } });
  try {
  await row.locator('[data-act="ignoreDay"]').click();
  await until(async () => (await todo.count()) === 2);
  assert.match(await p.locator('.dc-done > summary').innerText(), /1 done: repaired or ignored/);
  assert.match(await p.locator('.dc-counts').innerText(), /Done\s*1/i);
  const done = p.locator(`.dc-done .dc-row[data-dc="${dep}"]`);
  assert.equal(await p.locator('.dc-done').evaluate(el => el.open), false, 'Done is folded away');
  await p.locator('.dc-done > summary').click();
  assert.match(await done.locator('summary').innerText(), /Ignored/);
  assert.equal(await flagged(), 0, 'no longer marked as flagged anywhere');
  // still there to open, and undo puts it back on the list
  await done.locator('summary').click();
  await done.locator('[data-act="unignoreDay"]').click();
  await until(async () => (await todo.count()) === 3);
  assert.equal(await p.locator('.dc-done').count(), 0);
  assert.equal(await flagged(), 1, 'flagged again');
  } finally { await restore(); }
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a repaired day is in Done with its repair to open, and nothing is left to do once all are', async () => {
  const ctx = await context(b, { blockSW: true });
  const p = await ctx.newPage();
  let all = false;
  // the repair worker isn't run here: the day check's answer says which are repaired
  await ctx.route('**/v2/day-checks*', async r => {
    const res = await r.fetch(), body = await res.json();
    for (const x of body.results) {
      if (x.status !== 'ok' && (all || x.name === 'BankNifty DTT Plain')) x.repaired = { id: 'r1', applied_at: new Date().toISOString() };
    }
    await r.fulfill({ response: res, json: body });
  });
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-done');
  assert.equal(await p.locator('.dc-list > .dc-row').count(), 2, 'two left to do');
  const done = p.locator('.dc-done .dc-row');
  assert.equal(await done.count(), 1);
  await p.locator('.dc-done > summary').click();
  assert.match(await done.locator('summary').innerText(), /BankNifty DTT Plain[\s\S]*Repaired/);
  await done.locator('summary').click();
  assert.equal(await done.locator('a', { hasText: 'Open the repair' }).count(), 1);
  assert.equal(await done.locator('[data-act="ignoreDay"]').count(), 0, 'nothing to ignore once repaired');
  assert.doesNotMatch(await p.locator('.dc-counts').innerText(), /Rule broken/i, 'the counts are of what’s left to do');
  all = true;
  await p.reload();
  await p.waitForSelector('.dc-clear');
  assert.match(await p.locator('.dc-clear').innerText(), /Nothing left to do for this day: all 3 flagged are repaired or ignored/);
  assert.equal(await p.locator('.dc-list > .dc-row').count(), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

// "Check again" on one deployment (after a fix): its own run, the real
// worker on that deployment only. No Kite session here, so it fails, and
// the row and the case page say why; the day's run and the other rows
// stay as they were, and a group opened by hand stays open meanwhile.
test('checking one deployment again runs it alone and says how it went on its row and its case', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/repair`);
  await p.waitForSelector('.dc-list > .dc-row');
  const before = await api(ctx, '/v2/day-checks');
  await p.locator('.dc-fine:not(.dc-done) > summary').click();          // open "as expected" by hand
  const row = p.locator('.dc-list > .dc-row').nth(1), dep = await row.getAttribute('data-dc');
  await row.locator('[data-act="recheck"]').click();
  await until(async () => (await row.locator('[data-recheck="running"]').count()) > 0
    || (await row.locator('[data-recheck="failed"]').count()) > 0);
  await until(async () => /Checking again failed[\s\S]*No Kite session/.test(await row.innerText()), 60000);
  assert.equal(await p.locator('.dc-fine:not(.dc-done)').evaluate(el => el.open), true, 'still open after the redraws');
  const after = await api(ctx, '/v2/day-checks');
  assert.equal(after.rechecks[dep].status, 'failed');
  assert.equal(after.rechecks[dep].deployment_id, dep);
  assert.deepEqual(after.run, before.run, 'the day’s own run is untouched');
  assert.deepEqual(after.results.map(x => [x.name, x.status, x.headline]), before.results.map(x => [x.name, x.status, x.headline]));
  assert.match(await p.locator('.dc-bar').innerText(), /Ran after the export, 15:52–16:03/);
  // the case page shows it too, and can start it again
  await p.goto(`${BASE}/linck/#/repair/${after.day}/${dep}`);
  await p.waitForSelector('.rc-found [data-act="recheck"]');
  await until(async () => /Checking again failed[\s\S]*No Kite session/.test(await p.locator('.rc-found').innerText()));
  assert.equal(await p.locator('.rc-found [data-act="recheck"]').innerText(), 'Check again');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
