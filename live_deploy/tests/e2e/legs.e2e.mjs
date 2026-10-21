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

test('no ticks since the open: lanes holding legs stay flat at ₹0', async () => {
  const ctx = await context(b);
  await streamTicks(ctx, [], false);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector(`[data-today="${ID}"]`);
  await p.waitForTimeout(500);
  const l = await laneOf(p, ID);
  assert.equal(l.today, '₹0', 'nothing moved today');
  assert.equal(new Set(l.ys).size, 1, `a flat lane, not a spike (${l.ys.join(',')})`);
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

// A holiday, a weekend, before the open, or Kite down with nothing
// traded today: today's P&L would read ₹0, which says nothing. The Desk
// shows the open positions' P&L instead, and says so.
test('nothing traded today: the Desk shows the open positions’ P&L, not ₹0', async () => {
  const ctx = await context(b);
  await streamTicks(ctx, [], false);
  // today's session as on such a day: no fills, nothing booked since the open
  const deps = await api(ctx, '/deployments');
  await ctx.route('**/v2/session', async r => {
    const s = await (await r.fetch()).json();
    for (const d of deps) {
      const l = s.lanes[d.id] || (s.lanes[d.id] = { series: [] });
      l.events = [];
      l.realized_open = { gross: Number(d.realized_pnl), net: Number(d.realized_pnl_net ?? d.realized_pnl), net_slip: Number(d.realized_pnl_net_slip ?? d.realized_pnl_net ?? d.realized_pnl) };
      l.equity_open = null;
    }
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(s) });
  });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('#heroNum[data-quiet="1"]');
  const label = await p.innerText('[data-hero-label]');
  assert.match(label, /nothing traded today · open positions’ P&L, \d+ legs?/);
  const hero = (await p.getAttribute('#heroNum .odo', 'aria-label'));
  const subT = (await p.innerText('#subT')).trim();
  assert.equal(subT, '₹0', 'today is still there, as a figure under it');
  assert.notEqual(hero, '₹0', 'the open legs’ P&L, not a misleading ₹0');
  assert.equal(await p.locator('#subU').count(), 0);
  // the lanes too: each shows its open P&L under an "Open" column
  assert.equal((await p.innerText('.lane-axis b.right')).trim(), 'Open');
  // each holding lane shows the same open P&L as its group in Open positions
  const pairs = await p.$$eval('[data-grp]', gs => gs.map(g => [g.textContent.trim(), document.querySelector(`[data-today="${g.dataset.grp}"]`)?.textContent.trim()]));
  assert.ok(pairs.length, 'some deployment holds legs');
  for (const [grp, lane] of pairs) if (lane != null) assert.equal(lane, grp, 'a holding lane shows what it holds');
  assert.ok(pairs.some(([g]) => g !== '₹0'));
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
