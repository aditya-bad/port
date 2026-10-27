// LinckLens (pages/lens/*.js, app/lens.py): the logo picks the product,
// LinckTrade or LinckLens; Lens reads the Kite account. This server has no
// Kite session, so the real answer is "Kite isn't connected"; the account
// itself is lens.fixture.json, a made-up account read through the real
// app/lens.py (tests/e2e/lens_fixture.py; a unit test keeps it current).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BASE, browser, context, until } from './helper.mjs';

const FX = JSON.parse(readFileSync(new URL('./lens.fixture.json', import.meta.url), 'utf8'));
const LOADING = { day: FX.history.day, done: 4, total: FX.history.total, error: null, state: 'loading' };

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

// A signed-in page whose Lens reads answer from the fixture. history:
// what /v2/lens/history answers, call by call (the last one repeats).
async function lensPage({ width = 1440, height = 900, history = [FX.history] } = {}) {
  const ctx = await context(b, { width, height, blockSW: true });   // the page's requests come to the route, not the service worker
  const n = { holdings: 0, history: 0 };
  await ctx.route('**/v2/lens/holdings*', r => { n.holdings += 1; return r.fulfill({ json: FX.holdings }); });
  await ctx.route('**/v2/lens/history*', r => { const a = history[Math.min(n.history, history.length - 1)]; n.history += 1; return r.fulfill({ json: a }); });
  const p = await ctx.newPage();
  return { ctx, p, n };
}
const noSideways = p => p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
const symbols = p => p.locator('[data-lens="equity"] .tr[data-symbol]').evaluateAll(rs => rs.map(r => r.dataset.symbol));

test('the logo switches between LinckTrade and LinckLens; without Kite it says so', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('.hdr .prod-btn');
  assert.equal((await p.textContent('.hdr .wordmark')).trim(), 'Linck');
  assert.equal((await p.textContent('.hdr .prod-name')).trim(), 'Trade');
  await p.setViewportSize({ width: 320, height: 800 });
  assert.equal(await noSideways(p), 0, 'fits a 320px phone');
  await p.setViewportSize({ width: 1440, height: 900 });
  await p.click('.hdr .prod-btn');
  const menu = p.locator('.prod-menu');
  assert.match(await menu.innerText(), /LinckTrade[\s\S]*Deployments[\s\S]*LinckLens[\s\S]*Your holdings/);
  assert.equal(await menu.locator('a.on').getAttribute('data-product'), 'trade');
  await p.keyboard.press('Escape');
  assert.equal(await p.locator('.prod-menu').count(), 0, 'Escape closes it');
  await p.click('.hdr .prod-btn');
  await p.click('.prod-menu [data-product="lens"]');
  await p.waitForSelector('.hdr.is-lens');
  assert.match(p.url(), /#\/lens$/);
  assert.equal((await p.textContent('.hdr .prod-name')).trim(), 'Lens');
  assert.deepEqual(await p.locator('.hdr .nav a').allInnerTexts(), ['Overview', 'Shares', 'Funds', 'Trades', 'Account'], 'Lens has its own pages');
  assert.equal(await p.locator('.hdr .basis-seg').count(), 0, 'no P&L basis: that is LinckTrade’s');
  // no Kite session on this server: it says so, on every page
  await p.waitForSelector('.lens .empty');
  assert.match(await p.locator('.lens .empty').innerText(), /Kite isn’t connected[\s\S]*Connect it with Reconnect at the top, then Refresh/);
  await p.click('.hdr .nav a:has-text("Funds")');
  await p.waitForSelector('.lens[data-tab="funds"] .empty');
  assert.match(p.url(), /#\/lens\/funds$/);
  assert.equal(await p.locator('.hdr .nav a.on').innerText(), 'Funds');
  // and back
  await p.click('.hdr .prod-btn');
  await p.click('.prod-menu [data-product="trade"]');
  await p.waitForSelector('.hdr:not(.is-lens)');
  assert.match(p.url(), /#\/desk$/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Overview: the whole, the shares against Nifty, what needs a look, where it is, who moved it', async () => {
  const { ctx, p, n } = await lensPage({ history: [LOADING, FX.history] });
  await p.goto(`${BASE}/linck/#/lens`);
  await p.waitForSelector('.lens-strip');
  const strip = await p.locator('.lens-strip').innerText();
  assert.match(strip, /Everything you hold\s+₹27,15,046\s+14 shares · 5 funds/);
  assert.match(strip, /Gain on what you put in\s+\+₹1,76,365\s+\+6\.95% on ₹25,38,681/);
  assert.match(strip, /Shares today\s+\+₹11,081\s+\+0\.61% · 8 up, 5 down/);
  assert.match(strip, /Going in by SIP\s+₹35,833\s+a month, 3 active SIPs/);
  // the year comes in while you watch: progress, then the line
  assert.match(await p.locator('[data-year="loading"]').innerText(), /Reading a year of prices from Kite: 4 of 15/);
  assert.equal(await p.locator('[data-attn="near_low"]').count(), 0, 'what needs the year waits for it');
  assert.ok(await p.locator('[data-attn="regular_plans"]').count(), 'what the holdings show is there at once');
  await p.waitForSelector('.lens-curve', { timeout: 10000 });
  assert.ok(n.history >= 2, 'it asked again until the year was in');
  assert.match(await p.locator('.lens-headline').innerText(), /Over 1 year, your shares as they stand today went \+3\.3%; Nifty 50 went \+11\.1%\. That’s 7\.8 points behind the market\./);
  assert.match(await p.locator('.lens-legend').innerText(), /Your shares \+3\.3%[\s\S]*Nifty 50 \+11\.1%/);
  assert.match(await p.locator('.lens-curve + .lens-note').innerText(), /today’s[\s\S]*1 share listed within the year is left out of the line: MEESHO/);
  // hover: the day, both lines and the rupee value
  const box = await p.locator('[data-scrub="lens-curve"]').boundingBox();
  await p.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  const tip = await until(() => p.evaluate(() => { const t = document.querySelector('.tip'); return t && t.style.display !== 'none' ? t.innerText.trim() : ''; }));
  assert.match(tip, /Your shares\s+[+−-][\d.]+%\s+·\s+₹[\d,]+[\s\S]*Nifty 50\s+[+−-][\d.]+%/);
  assert.doesNotMatch(tip, /NaN|undefined/);
  // a shorter window starts both lines again from 100
  await p.click('[data-act="window"][data-v="1M"]');
  assert.match(await p.locator('.lens-legend').innerText(), /Your shares −2\.7%[\s\S]*Nifty 50 −2\.2%/, 'the same as the 1M column below');
  assert.equal(await p.locator('[data-act="window"][data-v="1M"]').getAttribute('aria-pressed'), 'true');
  // periods and risk
  const periods = await p.locator('.lens-periods').innerText();
  assert.match(periods, /Your shares\s+\+1\.2%\s+−2\.7%\s+\+4\.0%\s+\+0\.1%\s+\+3\.3%/);
  assert.match(periods, /Nifty 50\s+\+0\.7%\s+−2\.2%\s+\+7\.7%\s+\+13\.1%\s+\+11\.1%/);
  assert.match(periods, /Ahead of Nifty by\s+\+0\.5 pts[\s\S]*−7\.8 pts/);
  assert.match(await p.locator('.lens-risk').innerText(), /Swings \(volatility\)\s+13\.3%[\s\S]*Beta to Nifty\s+0\.87\s+moves less than the market[\s\S]*Worst fall in the year\s+−8\.1%\s+19 May ’?'?26 → 24 Jun ’?'?26/);
  // what needs a look: act-on-it first, then good news, then know-it
  const attn = await p.locator('.lens-a').evaluateAll(as => as.map(a => [a.dataset.attn, a.className.replace('lens-a ', '')]));
  assert.deepEqual(attn.map(a => a[0]), ['regular_plans', 'deep_loss', 'near_low', 'lagging', 'near_high', 'idcw', 't1', 'pledged', 'idle_cash', 'sips_paused']);
  assert.match(await p.locator('[data-attn="regular_plans"]').innerText(), /2 funds are Regular plans, ₹1,77,309[\s\S]*AXIS ELSS TAX SAVER FUND - REGULAR PLAN/);
  assert.match(await p.locator('[data-attn="deep_loss"]').innerText(), /IDEA −?-?51%[\s\S]*TATAMOTORS −?-?48%/);
  assert.match(await p.locator('[data-attn="near_high"]').innerText(), /4 shares[\s\S]*BEL[\s\S]*SUZLON/);
  assert.doesNotMatch(await p.locator('[data-attn="near_high"]').innerText(), /LIQUIDBEES/, 'a price that never moves is near no high');
  assert.match(await p.locator('[data-attn="idle_cash"]').innerText(), /₹1,42,000 cash in the account/);
  // where it is, and how much rides on a few
  const alloc = await p.locator('.lens-two .lens-lb').allInnerTexts();
  assert.match(alloc[0], /Stocks\s*9[\s\S]*₹13,75,723[\s\S]*50\.7%/);
  assert.equal(alloc.length, 10);
  assert.match(await p.locator('.lens-conc').innerText(), /Largest holding\s+14\.4%\s+HDFCBANK[\s\S]*Top five\s+57\.8%\s+of 19 holdings[\s\S]*Spread like\s+11\.2/);
  // who moved it: up and down sides, each a link to the share
  const today = p.locator('.lens-movers').first();
  assert.match(await today.locator('.lens-mv.up').first().innerText(), /HDFCBANK[\s\S]*\+₹4,542[\s\S]*\+1\.17%/);
  assert.match(await today.locator('.lens-mv.down').first().innerText(), /RELIANCE[\s\S]*−₹466/);
  await p.locator('.lens-movers').nth(1).locator('.lens-mv.down').first().click();
  await p.waitForSelector('[data-detail="TATAMOTORS"]');
  assert.match(p.url(), /#\/lens\/shares\?open=TATAMOTORS$/, 'a mover opens its share');
  assert.equal(await noSideways(p), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Shares: every holding, found, filtered, sorted, and one opened against Nifty and your cost', async () => {
  const { ctx, p, n } = await lensPage();
  await p.goto(`${BASE}/linck/#/lens/shares`);
  await p.waitForSelector('[data-lens="equity"] .tr[data-symbol] .lens-spark');
  const strip = await p.locator('.lens-strip').innerText();
  assert.match(strip, /In shares\s+₹18,38,572\s+14 holdings/);
  assert.match(strip, /Gain on what you put in\s+−₹27,888/);
  assert.match(strip, /Over a year\s+\+3\.3%\s+Nifty 50 \+11\.1%/);
  assert.equal((await symbols(p))[0], 'HDFCBANK', 'largest first');
  const infy = p.locator('[data-symbol="INFY"]');
  assert.match(await infy.innerText(), /INFY[\s\S]*INFOSYS[\s\S]*5\.4%[\s\S]*140[\s\S]*1,390\.00[\s\S]*1,055\.11[\s\S]*₹1,47,715[\s\S]*−₹46,885[\s\S]*−24\.09%[\s\S]*\+0\.55%[\s\S]*−19\.5%/);
  assert.equal(await infy.locator('.lens-range .now').count(), 1, 'where the price sits in the year');
  assert.match(await p.locator('[data-symbol="BEL"]').innerText(), /20 T1/, 'not yet delivered, shown');
  assert.match(await p.locator('[data-symbol="GOLDBEES"]').innerText(), /GOLDBEES\s*Gold/);
  // find
  await p.fill('[data-f="q"]', 'tata');
  assert.deepEqual(await symbols(p), ['TATAMOTORS']);
  assert.equal(await p.evaluate(() => document.activeElement.dataset.f), 'q', 'typing keeps the box');
  await p.fill('[data-f="q"]', '');
  // filter by kind
  await p.click('[data-act="asset"][data-v="gold"]');
  assert.deepEqual(await symbols(p), ['GOLDBEES', 'SGBDEC28']);
  assert.match(await p.locator('[data-lens="equity"] .tot').innerText(), /2 of 14 shown[\s\S]*₹1,68,389/);
  await p.click('[data-act="asset"][data-v="all"]');
  // sort by the year, then flip it
  await p.selectOption('[data-f="sort"]', 'y1');
  assert.deepEqual((await symbols(p)).slice(0, 3), ['SGBDEC28', 'BEL', 'SUZLON']);
  assert.equal((await symbols(p)).at(-1), 'MEESHO');
  await p.click('[data-act="dir"]');
  assert.deepEqual((await symbols(p)).slice(0, 3), ['TATAMOTORS', 'IDEA', 'INFY']);
  assert.equal((await symbols(p)).at(-1), 'MEESHO', 'no 1-year figure: last whichever way');
  await p.selectOption('[data-f="sort"]', 'pnl');
  assert.equal((await symbols(p))[0], 'BEL');
  // open one: its year against Nifty from the same start and your cost
  await infy.click();
  const d = p.locator('[data-detail="INFY"]');
  await d.waitFor();
  assert.match(await d.innerText(), /INFY[\s\S]*Nifty 50, from the same start[\s\S]*your average cost ₹1,390\.00/);
  assert.match(await d.locator('.ls-cells').innerText(), /1Y\s+−19\.5%\s+Nifty \+11\.1%[\s\S]*52-week high\s+₹1,340\.32\s+−21\.3% from it[\s\S]*Beta\s+0\.59[\s\S]*Breakeven\s+\+31\.7%\s+rise needed/);
  const box = await d.locator('[data-scrub]').boundingBox();
  await p.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5);
  const tip = await until(() => p.evaluate(() => { const t = document.querySelector('.tip'); return t && t.style.display !== 'none' ? t.innerText.trim() : ''; }));
  assert.match(tip, /INFY ·[\s\S]*Price\s+₹[\d,.]+[\s\S]*Nifty 50\s+[+−-][\d.]+%[\s\S]*Against your cost\s+[+−-][\d.]+%/);
  await infy.click();
  assert.equal(await d.count(), 0, 'clicked again, it closes');
  // a listing younger than a year has no 1-year return
  assert.match(await p.locator('[data-symbol="MEESHO"] [data-l="1 year"]').innerText(), /—/);
  // Refresh reads Kite again
  const before = n.holdings;
  await p.click('[data-act="refresh"]');
  await until(async () => n.holdings > before);
  assert.ok(n.holdings > before);
  assert.equal(await noSideways(p), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('live: a price from LinckLens’s own feed reprices the share and the totals', async () => {
  const ctx = await context(b, { width: 1440, height: 900, blockSW: true });
  const live = { ...FX.holdings, live: { connected: true, priced: 14, of: 14, last_tick_at: new Date().toISOString() } };
  await ctx.route('**/v2/lens/holdings*', r => r.fulfill({ json: live }));
  await ctx.route('**/v2/lens/history*', r => r.fulfill({ json: FX.history }));
  let streams = 0;
  await ctx.route('**/sse/lens', r => {
    streams += 1;
    return r.fulfill({ status: 200, contentType: 'text/event-stream',
      body: `data: ${JSON.stringify({ at: new Date().toISOString(), p: { INFY: [1100, 1050] } })}\n\n` });
  });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/lens/shares`);
  await p.waitForSelector('[data-symbol="INFY"]');
  assert.equal(await p.locator('.lens-live').getAttribute('data-live'), 'on');
  assert.match(await p.locator('.lens-live').innerText(), /Live/);
  await until(async () => /1,100\.00/.test(await p.locator('[data-symbol="INFY"]').innerText()), 8000);
  const row = await p.locator('[data-symbol="INFY"]').innerText();
  assert.match(row, /1,100\.00[\s\S]*₹1,54,000[\s\S]*\+4\.76%[\s\S]*\+₹7,000/, 'price, value, and today against the feed’s previous close');
  assert.match(await p.locator('.lens-strip').innerText(), /In shares\s+₹18,44,857/, 'the total moved by INFY’s ₹6,285');
  assert.ok(streams >= 1);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Funds: kind, plan, option, NAV, and the SIPs feeding them', async () => {
  const { ctx, p } = await lensPage();
  await p.goto(`${BASE}/linck/#/lens/funds`);
  await p.waitForSelector('[data-lens="mf"]');
  const strip = await p.locator('.lens-strip').innerText();
  assert.match(strip, /In funds\s+₹8,76,474\s+5 funds/);
  assert.match(strip, /Gain on what you put in\s+\+₹2,04,253\s+\+30\.38% on ₹6,72,221/);
  assert.match(strip, /Going in by SIP\s+₹35,833\s+a month, 3 SIPs/);
  assert.match(strip, /Next SIP\s+31 Oct[\s\S]*₹2,500 into AXIS ELSS/);
  const rows = p.locator('[data-lens="mf"] .tr[data-isin]');
  assert.equal(await rows.count(), 5);
  const axis = p.locator('[data-isin="INF846K01131"]');
  assert.match(await axis.innerText(), /AXIS ELSS TAX SAVER FUND - REGULAR PLAN[\s\S]*Axis Mutual Fund · folio 9101\/4[\s\S]*Regular[\s\S]*Growth[\s\S]*1,450\.200[\s\S]*68\.30[\s\S]*92\.60[\s\S]*24 Oct[\s\S]*₹99,049[\s\S]*₹1,34,289[\s\S]*\+₹35,240[\s\S]*₹10,833\/mo/);
  assert.equal(await axis.locator('.tag.warn').innerText(), 'Regular', 'a Regular plan stands out');
  assert.match(await p.locator('[data-isin="INF109K01Z48"]').innerText(), /IDCW payout/);
  assert.match(await p.locator('.lens-two').innerText(), /Index\s*1[\s\S]*41\.2%[\s\S]*Direct[\s\S]*79\.8%[\s\S]*Regular[\s\S]*20\.2%/);
  const sips = await p.locator('[data-lens="sips"] .tr[data-sip]').allInnerTexts();
  assert.equal(sips.length, 4);
  assert.match(sips[0], /PARAG PARIKH[\s\S]*₹15,000[\s\S]*a month[\s\S]*on the 5th[\s\S]*5 Nov[\s\S]*38[\s\S]*active/);
  assert.match(sips[2], /₹2,500[\s\S]*a week/);
  assert.match(sips[3], /ICICI PRUDENTIAL[\s\S]*paused/);
  assert.match(await p.locator('.sect:has-text("SIPs")').innerText(), /3 active · 1 not active/);
  assert.equal(await noSideways(p), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Account: the trading account’s money, and how LinckLens reads it', async () => {
  const { ctx, p } = await lensPage();
  await p.goto(`${BASE}/linck/#/lens/account`);
  await p.waitForSelector('.lens-how');
  const strip = await p.locator('.lens-strip').innerText();
  assert.match(strip, /Free to use\s+₹1,84,251[\s\S]*Cash\s+₹1,42,000\s+opened the day at ₹1,50,000[\s\S]*From pledged holdings\s+₹61,251[\s\S]*In use\s+₹19,000/);
  assert.match(await p.locator('.lens-how').first().innerText(), /today’s shares at each past day’s close[\s\S]*Kite gives no NAV history/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Trades: what the tradebook says, against Kite and the same money in Nifty', async () => {
  const { ctx, p } = await lensPage();
  await ctx.route('**/v2/lens/trades', r => r.fulfill({ json: FX.trades }));
  const posted = [];
  await ctx.route('**/v2/lens/trades/import', r => { posted.push(r.request().postDataJSON()); return r.fulfill({ json: { files: [
    { id: 3, name: 'tradebook-2024.csv', rows: 3, added: 2, skipped: 1, first_day: '2024-04-02', last_day: '2024-09-30', problems: ['line 4: bad trade_date'] }] } }); });
  const removed = [];
  await ctx.route('**/v2/lens/trades/imports/*', r => { removed.push(r.request().url()); return r.fulfill({ json: { ok: true } }); });
  await p.goto(`${BASE}/linck/#/lens/trades`);
  await p.waitForSelector('[data-curve="ready"]');
  const strip = await p.locator('.lens-strip').first().innerText();
  assert.match(strip, /Money in now\s+₹24,51,969\s+bought less sold, 24 Feb '25 to 22 Sep '26/);
  assert.match(strip, /Return a year \(XIRR\)\s+\+8\.8%\s+shares −3\.6% · funds \+59\.5%/);
  assert.match(strip, /Booked\s+\+₹7,600[\s\S]*On paper\s+\+₹1,41,570\s+18 holdings/);
  assert.match(await p.locator('[data-curve="ready"]').innerText(), /worth ₹17,24,574[\s\S]*would be worth ₹20,14,829: you’re ₹2,90,255 behind/);
  const box = await p.locator('[data-scrub="lens-trades-curve"]').boundingBox();
  await p.mouse.move(box.x + box.width * 0.7, box.y + box.height / 2);
  const tip = await until(() => p.evaluate(() => { const t = document.querySelector('.tip'); return t && t.style.display !== 'none' ? t.innerText.trim() : ''; }));
  assert.match(tip, /Your shares\s+₹[\d,]+[\s\S]*Same money in Nifty 50\s+₹[\d,]+[\s\S]*Put in so far\s+₹[\d,]+/);
  assert.equal(await p.locator('.lens-months .ud').count(), FX.trades.analysis.months.length, 'a bar a month');
  assert.match(await p.locator('[data-fy="2026-27"]').innerText(), /2026-27[\s\S]*\+₹18,900[\s\S]*−₹2,000[\s\S]*\+₹16,900[\s\S]*3/);
  assert.equal(await p.locator('.lens-soon-i').count(), 8);
  assert.match(await p.locator('.lens-soon-i').first().innerText(), /1 day to go · long term from 28 Oct '26/);
  // a holding opens to its lots; under a year, the return over the time held
  const bel = p.locator('[data-trade="BEL"]');
  assert.match(await bel.innerText(), /\+73\.3%[\s\S]*in 120 days/);
  await bel.click();
  assert.match(await p.locator('[data-lots="BEL"]').innerText(), /29 Jun '26[\s\S]*300[\s\S]*179\.52[\s\S]*120 days[\s\S]*Short term[\s\S]*long from 30 Jun '27[\s\S]*\+₹45,540/);
  assert.match(await p.locator('[data-trade="ITC"]').innerText(), /300[\s\S]*Kite: 400/);
  assert.match(await p.locator('[data-recon="ITC"]').innerText(), /trades leave 300 · Kite holds 400[\s\S]*a bonus or split/);
  assert.match(await p.locator('[data-recon="SGBDEC28"]').innerText(), /held, with no trades/);
  assert.match(await p.locator('.lens-strip').nth(1).innerText(), /Sold at a profit\s+50%\s+2 of 4[\s\S]*Win to loss\s+1\.38/);
  // import: the files go up as text; what was new is said
  await p.setInputFiles('[data-f="files"]', { name: 'tradebook-2024.csv', mimeType: 'text/csv', buffer: Buffer.from('symbol,isin,trade_date\n') });
  await p.waitForSelector('[data-imported]');
  assert.equal(posted[0].files[0].name, 'tradebook-2024.csv');
  assert.match(posted[0].files[0].text, /^symbol,isin,trade_date/);
  assert.match(await p.locator('[data-imported]').innerText(), /tradebook-2024\.csv: 3 trades, 2 new, 1 already here[\s\S]*1 row not read: line 4: bad trade_date/);
  p.once('dialog', d => d.accept());
  await p.locator('[data-import="2"] [data-act="removeImport"]').click();
  await until(async () => removed.length > 0);
  assert.match(removed[0], /\/v2\/lens\/trades\/imports\/2$/);
  assert.equal(await noSideways(p), 0);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('Trades on the real server, without Kite: a file imported, stored once, and removed', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/lens/trades`);
  await p.waitForSelector('[data-how]');
  assert.equal(await p.locator('.lens .empty').count(), 0, 'no Kite needed to import trades');
  const csv = 'symbol\tisin\ttrade_date\texchange\tsegment\tseries\ttrade_type\tauction\tquantity\tprice\ttrade_id\torder_id\torder_execution_time\t\n'
    + 'NIFTYBEES\tINF204KB14I2\t2026-04-02\tNSE\tEQ\tEQ\tbuy\tFALSE\t4\t252.850006\tE2E400299040\t1200000002303827\t2026-04-02 9:18:00\t\n'
    + 'BANDHAN SMALL CAP FUND DIRECT PLAN-GROWTH\tINF194KB1AL4\t2026-04-01\tBSE\tMF\t\tbuy\tFALSE\t56.215\t46.249\tE2E2597645039\t2597645039\t2026-04-01 0:00:00\t\n';
  const file = { name: 'e2e-tradebook.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) };
  await p.setInputFiles('[data-f="files"]', file);
  await p.waitForSelector('[data-imported]');
  assert.match(await p.locator('[data-imported]').innerText(), /e2e-tradebook\.csv: 2 trades, 2 new/);
  await p.waitForSelector('[data-trade="NIFTYBEES"]');
  assert.match(await p.locator('.lens-strip').first().innerText(), /Money in now\s+₹3,611/);
  assert.match(await p.locator('[data-trade="BANDHAN SMALL CAP FUND DIRECT PLAN-GROWTH"]').innerText(), /Fund[\s\S]*1 Apr '26[\s\S]*56\.215/);
  assert.match(await p.locator('.lens').innerText(), /Kite isn’t connected, so the trades can’t be set against what you hold now/);
  await p.setInputFiles('[data-f="files"]', file);
  await until(async () => /0 new, 2 already here/.test(await p.locator('[data-imported]').innerText()));
  const ids = await p.locator('[data-import]').evaluateAll(xs => xs.map(x => x.dataset.import));
  for (const id of ids) {
    p.once('dialog', d => d.accept());
    await p.locator(`[data-import="${id}"] [data-act="removeImport"]`).click();
    await until(async () => (await p.locator(`[data-import="${id}"]`).count()) === 0);
  }
  await p.waitForSelector('[data-how]');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

for (const [width, label] of [[390, 'phone'], [320, 'small phone']]) {
  test(`every Lens page fits a ${label} and the bottom bar has Lens’s pages`, async () => {
    const { ctx, p } = await lensPage({ width, height: 800 });
    await ctx.route('**/v2/lens/trades', r => r.fulfill({ json: FX.trades }));
    for (const tab of ['', '/shares', '/shares?open=INFY', '/funds', '/trades', '/account']) {
      await p.goto(`${BASE}/linck/#/lens${tab}`);
      await p.waitForSelector(tab.startsWith('/shares') ? '.lens-spark' : tab === '/trades' ? '[data-curve="ready"]' : '.lens-strip');
      if (tab === '') await p.waitForSelector('.lens-curve');
      assert.equal(await noSideways(p), 0, `#/lens${tab} scrolls sideways at ${width}px`);
    }
    // stacked rows carry their labels
    await p.goto(`${BASE}/linck/#/lens/shares`);
    await p.waitForSelector('[data-symbol="INFY"]');
    assert.match(await p.locator('[data-symbol="INFY"] [data-l="P&L"]').evaluate(e => getComputedStyle(e, '::before').content), /P&L/);
    const bar = await p.locator('#bnav a, #bnav button').allInnerTexts();
    assert.deepEqual(bar.map(s => s.trim()).filter(Boolean).slice(0, 5), ['Overview', 'Shares', 'Funds', 'Trades', 'Account']);
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}
