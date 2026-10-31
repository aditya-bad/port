// Variations (pages/deployment/variations.js): add one from a deployment's
// Variations tab, see it side by side, find it from its primary (the chip,
// the list's count) and not as a deployment of its own in lists, open it,
// and Make primary. Uses Sensex Credit Spread, and puts everything back.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api, until } from './helper.mjs';

const NAME = 'Sensex Credit Spread';
let b;
test.before(async () => { b = await browser(); });
test.after(async () => {
  const ctx = await context(b);
  const fam = await api(ctx, `/v2/deployments/${IDS[NAME]}/variations`);
  if (fam.primary && fam.primary.id !== IDS[NAME]) await ctx.request.post(`${BASE}/v2/deployments/${IDS[NAME]}/make-primary`);
  for (const v of (await api(ctx, `/v2/deployments/${IDS[NAME]}/variations`)).variations || []) {
    await ctx.request.post(`${BASE}/deployments/${v.id}/stop`);
    await ctx.request.delete(`${BASE}/deployments/${v.id}`);
  }
  await ctx.close();
  await b.close();
});

test('add a variation, see it beside its primary, and make it the primary', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${IDS[NAME]}/variations`);
  await p.waitForSelector('[data-var="tab"]');
  assert.match(await p.innerText('[data-var="tab"]'), /No variations yet/);
  await p.click('[data-act="add"]');
  await p.waitForSelector('[data-var="add"]');
  assert.match(await p.locator('[data-act="create"]').getAttribute('data-reason'), /Give it a label/);
  await p.fill('[data-form="label"]', 'half size');
  await p.fill('[data-form="capital"]', '250000');
  await until(async () => (await p.locator('[data-act="create"]').getAttribute('aria-disabled')) !== 'true');
  // nothing held: no cycle to continue
  await p.click('[data-act="start"][data-v="continue"]');
  assert.match(await p.locator('[data-act="create"]').getAttribute('data-reason'), /holds nothing now: there’s no cycle to continue/);
  await p.click('[data-act="start"][data-v="fresh"]');
  await until(async () => (await p.locator('[data-act="create"]').getAttribute('aria-disabled')) !== 'true');
  await p.click('[data-act="create"]');
  await p.waitForSelector('[data-var="table"]');
  const fam = await api(ctx, `/v2/deployments/${IDS[NAME]}/variations`);
  assert.equal(fam.variations.length, 1);
  const vid = fam.variations[0].id;
  assert.equal(fam.variations[0].label, 'half size');
  assert.equal(fam.variations[0].capital, 250000);
  assert.equal(fam.variations[0].status, 'paused');
  const rows = await p.$$eval('[data-var-row]', rs => rs.map(r => r.innerText.split('\n')[0].trim()));
  assert.deepEqual(rows, ['Primary', 'half size']);
  assert.match(await p.innerText('.tabs'), /Variations · 1/);
  assert.match(await p.innerText('.dep-head'), /\+1 variation/);
  // the list shows the primary with its count, not the variation on its own
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.waitForSelector('[data-var-n]');
  const names = await p.$$eval('.cell-name b', bs => bs.map(x => x.textContent));
  assert.ok(names.includes(NAME) && !names.some(n => n.includes('half size')), names.join(', '));
  // the variation's own page says what it is; Make primary swaps them
  await p.goto(`${BASE}/linck/#/d/${vid}/now`);
  await p.waitForSelector('[data-var-of]');
  assert.match(await p.innerText('[data-var-of]'), new RegExp(`Variation of\\s+${NAME}\\s+· half size`));
  await p.click('[data-var-of] [data-act="makePrimary"]');
  await p.waitForSelector('.dialog');
  assert.match(await p.innerText('.dialog'), /Make half size the primary\?[\s\S]*becomes a variation labelled “Original”/);
  await p.click('.dialog [data-ok]');
  await until(async () => (await api(ctx, `/deployments/${vid}`)).variation_of === null);
  assert.equal((await api(ctx, `/deployments/${IDS[NAME]}`)).variation_of, vid);
  await until(async () => (await p.locator('[data-var-chip]').count()) === 1);
  assert.deepEqual(ctx.errors || [], []);
  await ctx.close();
});

test('a variation is pickable in Compare and Leaderboard, and Geek mode switches between them', async () => {
  const ctx = await context(b);
  const P = 'Nifty Strangle B';
  const cfg = (await api(ctx, `/deployments/${IDS[P]}`)).config;
  const r = await ctx.request.post(`${BASE}/v2/deployments/${IDS[P]}/variations`, { data: { label: 'tight band', config: cfg } });
  const vid = (await r.json()).id;
  try {
    const p = await ctx.newPage();
    // Leaderboard: primaries, unless asked
    await p.goto(`${BASE}/linck/#/leaderboard`);
    await p.waitForSelector('[data-act="vars"]');
    const rowsText = async () => p.innerText('#main');
    assert.ok(!(await rowsText()).includes('tight band'));
    await p.click('[data-act="vars"]');
    await until(async () => (await rowsText()).includes(`${P} · tight band`));
    // Compare: a quick pick of the family, and the variation as a tile
    await p.goto(`${BASE}/linck/#/compare`);
    await p.waitForSelector('.quick');
    assert.match(await p.innerText('#main'), new RegExp(`${P} and its variations`));
    assert.match(await p.locator(`[data-act="toggle"][data-id="${vid}"]`).innerText(), new RegExp(`variation of ${P}`));
    // Geek mode: the family's switcher keeps the view
    await p.goto(`${BASE}/linck/#/d/${IDS[P]}/geek/overview`);
    await p.waitForSelector('[data-gk-var]');
    await p.selectOption('[data-gk-var]', vid);
    await p.waitForURL(new RegExp(`#/d/${vid}/geek/overview$`));
    assert.equal(await p.locator('[data-gk-var]').inputValue(), vid);
  } finally {
    await ctx.request.post(`${BASE}/deployments/${vid}/stop`);
    await ctx.request.delete(`${BASE}/deployments/${vid}`);
    await ctx.close();
  }
});
