// The open-legs table: which instrument (with its index), entry price
// on every screen size, and prices that move with the tick stream.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api } from './helper.mjs';

const ID = IDS['Nifty Strangle A'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

// Serve /sse/ticks ourselves: each reconnect delivers one batch of new prices.
async function streamTicks(ctx, legs, live = true) {
  let k = 0;
  await ctx.route('**/sse/ticks', r => {
    if (!live) return r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': idle\n\n' });
    k++;
    const ticks = legs.map(p => ({ instrument_token: p.instrument_token, last_price: +(Number(p.avg_entry_price) + k).toFixed(2) }));
    return r.fulfill({ status: 200, contentType: 'text/event-stream', body: `retry: 300\ndata: ${JSON.stringify(ticks)}\n\n` });
  });
}

for (const [width, label] of [[390, 'phone'], [1440, 'desktop']]) {
  test(`holdings show the index, the entry price and live prices (${label})`, async () => {
    const ctx = await context(b, { width, height: 860 });
    const legs = await api(ctx, `/deployments/${ID}/positions?status=open`);
    await streamTicks(ctx, legs);
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${ID}/now`);
    await p.waitForSelector('.legs-tbl.leg');
    const row = p.locator('.legs-tbl.leg').first();
    const text = await row.innerText();
    assert.match(text, /NIFTY/, 'the index is named');
    assert.match(text, /\d{5} (CE|PE)/, 'strike and type');
    const entry = row.locator('span.right').nth(1);
    assert.ok(await entry.isVisible(), 'entry is shown');
    assert.equal(await entry.innerText(), Number(legs.find(l => text.includes(l.symbol.match(/(\d+)(CE|PE)$/)[1])).avg_entry_price).toFixed(2));
    await p.waitForFunction(() => document.querySelector('[data-live]')?.classList.contains('on'), null, { timeout: 5000 });
    // the LTP shown is the streamed price (entry + n), not the server's snapshot
    const [ltpTxt, entryTxt] = await p.evaluate(() => { const r = document.querySelector('.legs-tbl.leg'); return [r.querySelector('[data-ltp]').textContent, r.querySelectorAll('span.right')[1].textContent]; });
    const diff = parseFloat(ltpTxt.replace(/,/g, '')) - parseFloat(entryTxt.replace(/,/g, ''));
    assert.ok(diff >= 1 && Math.abs(diff - Math.round(diff)) < 0.011, `LTP ${ltpTxt} comes from the tick stream (entry ${entryTxt})`);
    const { sw, cw } = await p.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    assert.ok(sw <= cw + 1, 'no sideways scroll');
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}

test('without ticks it says the prices are not live', async () => {
  const ctx = await context(b, { width: 390, height: 860 });
  await streamTicks(ctx, [], false);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/now`);
  await p.waitForSelector('[data-live]');
  assert.match(await p.innerText('[data-live]'), /Not streaming|No prices yet/);
  assert.ok(!(await p.locator('[data-live].on').count()));
  await ctx.close();
});

// Desk's open positions: each deployment is its own band, never mistaken for a leg.
for (const [width, label] of [[390, 'phone'], [1440, 'desktop']]) {
  test(`desk groups open legs under a band per deployment (${label})`, async () => {
    const ctx = await context(b, { width, height: 900 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/desk`);
    await p.waitForSelector('.leg-set .leg-group');
    const sets = await p.$$eval('.leg-set', ss => ss.map(s => {
      const g = s.querySelector('.leg-group'), name = g.querySelector('.leg-group-name > b');
      const leg = s.querySelector('.leg-set-rows .legs-tbl.leg b');
      const gs = getComputedStyle(g), rows = s.querySelector('.leg-set-rows');
      return {
        name: name.textContent, meta: g.querySelector('.leg-group-meta').textContent,
        legs: rows.querySelectorAll('.legs-tbl.leg').length,
        shaded: gs.backgroundColor !== getComputedStyle(document.body).backgroundColor,
        rule: parseFloat(gs.borderTopWidth),
        fonts: [getComputedStyle(name).fontFamily, getComputedStyle(leg).fontFamily],
        indent: rows.querySelector('.legs-tbl.leg').getBoundingClientRect().left - g.getBoundingClientRect().left,
        href: g.getAttribute('href'),
      };
    }));
    assert.ok(sets.length >= 2, 'several deployments hold legs');
    for (const [i, s] of sets.entries()) {
      assert.match(s.meta, new RegExp(`· ${s.legs} legs?$`), `${s.name}: strategy and leg count`);
      assert.ok(s.legs > 0 && s.shaded, `${s.name}: a shaded band`);
      // a rule between deployments; the first sits on the column-header rule
      assert.ok(i ? s.rule >= 1 : s.rule === 0, `${s.name}: rule above (${s.rule}px)`);
      assert.notEqual(s.fonts[0], s.fonts[1], `${s.name}: the name is set apart from the instruments`);
      assert.ok(s.indent >= 6, `${s.name}: its legs are indented under it`);
      assert.match(s.href, /#\/d\//);
    }
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    await ctx.close();
  });
}
