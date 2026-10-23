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

// A holiday with Kite down: open legs have no price since the open, so
// they haven't moved. The Desk must not show their stored unrealized P&L
// as a spike "today" (seen on a holiday: flat lanes jumping at "now").
const laneOf = (p, id) => p.evaluate(id => {
  const lane = document.querySelector(`[data-today="${id}"]`).closest('.lane');
  const d = lane.querySelector('.lane-plot path[fill=none]').getAttribute('d');
  const ys = [...d.matchAll(/[ML][\d.]+ ([\d.]+)/g)].map(m => +m[1]);
  return { today: document.querySelector(`[data-today="${id}"]`).textContent.trim(), ys };
}, id);

// a figure as shown (₹1,23,400 / −₹750) as a number
const num = t => Number(t.replace(/[₹,\s]/g, '').replace('−', '-'));

test('no ticks since the open: lanes stay flat, a positional lane shows its open cycle', async () => {
  const ctx = await context(b);
  await streamTicks(ctx, [], false);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector(`[data-today="${ID}"]`);
  await p.waitForTimeout(500);
  const l = await laneOf(p, ID);
  assert.equal(new Set(l.ys).size, 1, `a flat lane, not a spike (${l.ys.join(',')})`);
  // positional: what its open cycle's closed legs made plus what its open legs hold now
  const { cycles } = await api(ctx, '/v2/book');
  assert.ok(cycles[ID], 'the server has the open cycle');
  const held = num(await p.innerText(`[data-grp="${ID}"]`));
  assert.ok(Math.abs(num(l.today) - (Number(cycles[ID].pnl) + held)) <= 1, `${l.today} = cycle realized ${cycles[ID].pnl} + open ${held}`);
  await ctx.close();
});

test('once ticks arrive, the lane moves with them', async () => {
  const ctx = await context(b);
  const legs = await api(ctx, `/deployments/${ID}/positions?status=open`);
  // move one leg only: the same move on every leg of a balanced strangle nets to ₹0
  await streamTicks(ctx, legs.slice(0, 1));
  const p = await ctx.newPage();
  // a tick only counts towards today once the session is open (09:15 IST):
  // run inside it whatever time the suite runs at
  const ist = new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);
  await p.clock.install({ time: new Date(`${ist}T11:00:00+05:30`) });
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector(`[data-today="${ID}"]`);
  await p.waitForFunction(id => document.querySelector(`[data-today="${id}"]`).textContent.trim() !== '₹0', ID, { timeout: 8000 });
  assert.ok(new Set((await laneOf(p, ID)).ys).size > 1, 'the lane shows the move');
  await ctx.close();
});

// A holiday / Kite down: no live price, so the legs show their last
// recorded price, muted, with when it was recorded (it was a bare "—").
test('legs with no live price show their last recorded price, marked as not live', async () => {
  const ctx = await context(b);
  await streamTicks(ctx, [], false);
  const p = await ctx.newPage();
  const id = IDS['Bankex Strangle'];
  await p.goto(`${BASE}/linck/#/d/${id}/now`);
  await p.waitForSelector('.legs-tbl.leg [data-ltp]');
  const cells = await p.$$eval('.legs-tbl.leg [data-ltp]', els => els.map(e => ({ t: e.textContent.trim(), stale: e.classList.contains('stale'), tip: e.dataset.tip || '' })));
  assert.equal(cells.length, 2);
  for (const c of cells) {
    assert.match(c.t, /^\d+\.\d{2}$/, 'a price, not "—"');
    assert.ok(c.stale, 'muted');
    assert.match(c.tip, /Last recorded price, .* 15:25\. Not live\./);
  }
  assert.match(await p.innerText('[data-live]'), /Not live · last prices .* 15:25/);
  const pnl = await p.$$eval('.legs-tbl.leg [data-pnl]', els => els.map(e => e.textContent.trim()));
  assert.ok(pnl.every(t => t !== '—'), 'P&L at that price');
  await ctx.close();
});

// The Cycles tab listed closed cycles only; the one running now was missing.
test('the Cycles tab shows the running cycle first, its P&L moving with the ticks', async () => {
  const ctx = await context(b);
  const legs = await api(ctx, `/deployments/${ID}/positions?status=open`);
  await streamTicks(ctx, legs.slice(0, 1));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/cycles`);
  await p.waitForSelector('.cyc-grid.running');
  const first = p.locator('.tr.cyc-grid:not(.th)').first();
  assert.match(await first.getAttribute('class'), /running/, 'pinned first');
  assert.match(await first.innerText(), /RUNNING[\s\S]*→ now[\s\S]*so far[\s\S]*still open/);
  assert.match(await first.innerText(), new RegExp(`${legs.length} open legs`));
  // a new price for one leg moves the running P&L (in place, on the next tick)
  const a = await p.textContent('[data-run-pnl]');
  await p.evaluate(async tok => {
    const live = await import('/linck/js/data/live.js'), store = await import('/linck/js/core/store.js');
    const cur = live.prices.get(tok);
    live.prices.set(tok, { ...(cur || {}), price: (cur ? cur.price : 100) + 40, at: Date.now() });
    store.emit('ticks');
  }, legs[0].instrument_token);
  await p.waitForFunction(v => document.querySelector('[data-run-pnl]').textContent !== v, a, { timeout: 8000 });
  assert.match(await p.textContent('.stats'), /closed · 1 more running/);
  await first.click();
  await p.waitForSelector('.ss-plot, .sheet', { timeout: 15000 });
  await ctx.close();
});

// Every page follows cycle P&L, not the day's: an intraday deployment's
// cycle is the day, a positional one's runs from its entry to its exit.
test('the Desk follows cycles: the hero adds them up, each lane shows its own', async () => {
  const ctx = await context(b);
  await streamTicks(ctx, [], false);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector(`[data-today="${ID}"]`);
  await p.waitForTimeout(500);
  assert.match(await p.innerText('[data-hero-label]'), /cycle P&L across \d+ deployments?/);
  assert.doesNotMatch(await p.innerText('[data-hero-label]'), /nothing traded today/);
  assert.equal((await p.innerText('.lane-axis b.right')).trim(), 'Cycle');
  // every lane, Strategy Lab ones too: the hero counts them all
  const lab = p.locator('[data-act="lab"]', { hasText: 'Show' });
  if (await lab.count()) await lab.click();
  const all = p.locator('[data-act="all"]', { hasText: 'Show all' });
  if (await all.count()) await all.click();
  await p.waitForTimeout(300);
  const lanes = await p.$$eval('[data-today]', els => els.map(e => ({ t: e.textContent.trim(), note: e.closest('.lane-today')?.querySelector('[data-closed-today] b')?.textContent.trim() || null })));
  const hero = num(await p.getAttribute('#heroNum .odo', 'aria-label'));
  const sum = lanes.reduce((a, l) => a + (l.t === '—' ? 0 : num(l.t)) + (l.note ? num(l.note) : 0), 0);
  assert.ok(Math.abs(hero - sum) <= lanes.length, `hero ${hero} = the lanes' cycles ${sum}`);
  // the intraday lane is the day
  const dtt = IDS['BankNifty DTT Plain'];
  const tip = await p.getAttribute(`.lane-today:has([data-today="${dtt}"])`, 'data-tip');
  assert.match(tip, /An intraday cycle is the day/);
  assert.match(await p.getAttribute(`.lane-today:has([data-today="${ID}"])`, 'data-tip'), /Open cycle since[\s\S]*one entry, its adjustments and its exit/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

// The day a positional cycle exits and a new one enters: both show that
// day, the new one as the figure and the closed one under it. From the
// next day, only the new one.
test('the switch day: a positional cycle closed today shows under the new one', async () => {
  const ctx = await context(b);
  await streamTicks(ctx, [], false);
  const now = new Date();
  const closed = { at: now };
  await ctx.route('**/v2/book', async r => {
    const bk = await (await r.fetch()).json();
    const t0 = bk.trades.find(t => t.deployment_id === ID) || bk.trades[0];
    const at = closed.at.toISOString();
    bk.trades.push({ ...t0, deployment_id: ID, opened_at: new Date(closed.at - 9 * 864e5).toISOString(), closed_at: at, pnl: 6000, pnl_net: 5600, pnl_net_slip: 5400 });
    bk.cycles[ID] = { ...(bk.cycles[ID] || {}), start: at, pnl: 0, pnl_net: 0, pnl_net_slip: 0 };
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bk) });
  });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  const cell = p.locator(`.lane-today:has([data-today="${ID}"])`);
  await cell.locator('[data-closed-today]').waitFor();
  assert.match(await cell.locator('[data-closed-today]').innerText(), /\+ closed today\s*\+₹6,000/);
  const held = num(await p.innerText(`[data-grp="${ID}"]`));
  assert.ok(Math.abs(num(await cell.locator(`[data-today="${ID}"]`).innerText()) - held) <= 1, 'the figure is the new cycle: its open legs');
  assert.match(await cell.getAttribute('data-tip'), /Closed today[\s\S]*\+₹6,000[\s\S]*Open cycle since/);
  // the Deployments list says the same
  await p.goto(`${BASE}/linck/#/deployments`);
  await p.locator(`[data-closed-today]`).first().waitFor();
  // the next day: the cycle closed yesterday no longer shows
  closed.at = new Date(now - 864e5);
  await p.goto(`${BASE}/linck/#/desk`);
  await p.reload();
  await p.waitForSelector(`[data-today="${ID}"]`);
  await p.waitForTimeout(500);
  assert.equal(await cell.locator('[data-closed-today]').count(), 0, 'only the new cycle from the next day');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
