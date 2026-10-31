// Geek mode's Replay (pages/geek/replay.js), the way Opstra and Sensibull
// replay a position: the payoff at expiry and today against the index,
// with the index and its future marked; every position under it, new ones
// joining and closed ones greyed with what they booked; steps of 5m…1D as
// fine as the stored prices go; play pausing at each trade; the timeline
// to drag; a chain of cycles; a phone. Controls, payoff and positions share
// one screen, and a closed leg keeps its entry and exit.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api, until } from './helper.mjs';

const ID = IDS['Nifty Monthly Strangle'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const step = p => p.evaluate(() => +document.querySelector('[data-range]').value);
const ready = p => p.waitForSelector('[data-rp="strip"] .rp-cell', { timeout: 20000 });
const rows = p => p.locator('[data-rp="pos"] .tr:not(.th):not(.rp-total)').count();
const ms = t => new Date(t).getTime();

test('the payoff of what’s held, the positions under it, step by step to the next trade', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  assert.equal(d.interval, '5minute');
  const adj = d.events.find(e => e.frame > 0);
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  const chart = p.locator('[data-rp="stage"] svg.gk-chart');
  assert.equal(await chart.count(), 1);
  assert.equal(await chart.locator('[data-curve="expiry"]').count(), 1, 'the payoff at expiry');
  assert.equal(await chart.locator('[data-curve="now"]').count(), 1, 'and today');
  assert.match(await chart.innerHTML(), /NIFTY 50 [\d,.]+/);
  assert.match(await chart.innerHTML(), /FUT [\d,.]+/);
  assert.match(await p.locator('[data-rp="strip"]').innerText(), /Future · NIFTY\d{2}[A-Z]{3}FUT[\s\S]*basis/);
  assert.match(await p.locator('[data-rp="paystats"]').innerText(), /Max loss\s*Unlimited/, 'a short strangle');
  assert.match(await p.locator('[data-rp="banner"]').innerText(), /New cycle 10[\s\S]*first closed cycle 9/);
  // the P&L on the chart is the P&L of the step
  const pnl = (await p.locator('[data-rp="strip"] .rp-cell', { hasText: 'P&L' }).locator('b').innerText()).trim();
  assert.ok((await chart.innerHTML()).includes(pnl.replace('+', '+')), `the chart shows ${pnl} at the index`);
  assert.equal(await rows(p), d.legs.filter(l => ms(l.t0) < ms(d.frames[1].t)).length, 'only what was open at the first step');

  // steps: as fine as the 5-minute prices, and bigger
  assert.deepEqual(await p.locator('[data-seg="step"] button').allInnerTexts(), ['5m', '15m', '30m', '1h', '1D']);
  await p.click('[data-act="fwd"]');
  assert.equal(await step(p), 1);
  await p.click('[data-act="step"][data-m="30"]');
  await p.click('[data-act="fwd"]');
  const i30 = await step(p);
  assert.ok(ms(d.frames[i30].t) - ms(d.frames[1].t) >= 30 * 60000, 'thirty minutes on');
  await p.keyboard.press('ArrowLeft');
  assert.equal(await step(p), 1, 'and back');

  // play, a day at a time: it pauses on the adjustment, which brings in a new leg and greys the old one
  await p.click('[data-act="to"][data-f="0"]');
  await p.click('[data-act="step"][data-m="0"]');
  await p.click('[data-act="speed"][data-s="3"]');
  await p.click('[data-act="play"]');
  await p.waitForFunction(f => +document.querySelector('[data-range]').value === f, adj.frame, { timeout: 30000 });
  await p.waitForTimeout(300);
  assert.match(await p.locator('.rp-ctl .rp-play').innerText(), /Play/, 'paused');
  assert.ok(await p.locator('.rp-banner.here').count(), 'on the trade itself');
  assert.match(await p.locator('[data-rp="banner"]').innerText(), new RegExp(adj.label));
  assert.ok(await p.locator('[data-rp="pos"] .tr.rp-fresh').count() >= 1, 'the new leg, highlighted');
  const gone = p.locator('[data-rp="pos"] .tr.rp-closed');
  assert.ok(await gone.count() >= 1, 'the closed leg, greyed');
  assert.match(await gone.first().innerText(), /Closed · Adjustment/);
  assert.match(await gone.first().locator('[data-cell="exit"]').innerText(), /[\d,.]+\s*15:13/, 'its exit: price and time');
  assert.match(await gone.first().locator('[data-cell="entry"]').innerText(), /[\d,.]+\s*(\d+ \w+ )?\d\d:\d\d/, 'and its entry (with its day when it was another)');
  assert.match(await gone.first().innerText(), /[+−]₹[\d,]+/, 'with what it booked');
  assert.match(await p.locator('[data-rp="pos"] .rp-total').innerText(), /open [+−]?₹[\d,]+ · booked [+−]?₹[\d,]+/);

  // trades by key, and the list
  await p.keyboard.press('[');
  assert.equal(await step(p), 0);
  await p.keyboard.press(']');
  assert.equal(await step(p), adj.frame);
  await p.locator('.rp-li').first().click();
  assert.equal(await step(p), 0);
  // the timeline stays with the controls as the page scrolls: press anywhere on it to go there
  const box = await p.locator('[data-track]').boundingBox();
  await p.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  const mid = await step(p);
  assert.ok(Math.abs(mid - (d.frames.length - 1) / 2) <= d.frames.length * 0.03, `the middle of the replay (${mid})`);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('a chain of cycles: From and To replay them as one, each new cycle in the trades', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  const n = await p.locator('[data-pick="from"] option').count();
  await p.selectOption('[data-pick="from"]', String(n - 2));
  await p.waitForURL(new RegExp(`replay\\?from=${n - 2}&to=${n}$`));
  await ready(p);
  assert.match(await p.locator('.rp-pick').innerText(), /a chain of 3/);
  assert.equal(await p.locator('.rp-li .rp-chip', { hasText: 'New cycle' }).count(), 3);
  await p.locator('[data-ep]').nth(1).click();
  await p.waitForURL(/replay\?from=(\d+)&to=\1$/);
  await p.waitForFunction(() => /one episode/.test(document.querySelector('.rp-pick')?.innerText || ''), null, { timeout: 20000 });
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('it says what it can’t draw: an index level that can’t be these contracts’', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  // the fixture's BankNifty history sits near 25,000 while its strikes are 55,900
  // an earlier straddle, not the latest: flow.e2e may have closed the latest one just now
  const bn = IDS['BankNifty DTT Plain'], n = (await api(ctx, `/v2/deployments/${bn}/geek/replay`)).episodes.length;
  await p.goto(`${BASE}/linck/#/d/${bn}/geek/replay?from=${n - 1}&to=${n - 1}`);
  await ready(p);
  const why = await p.locator('[data-rp="stage"]').innerText();
  assert.match(why, /NIFTY BANK at [\d,.]+ can’t be the index of/);
  assert.match(why, /PE \d{5}/); assert.match(why, /CE \d{5}/);
  assert.equal(await p.locator('[data-rp="stage"] svg').count(), 0);
  assert.ok(await rows(p) >= 1, 'the positions are still there');
  // the same for Sensex (history near 25,000, strikes past 80,000). A leg with no known
  // expiry (the payoff at expiry, but no line for today) is in tests/js/replay.test.mjs
  await p.goto(`${BASE}/linck/#/d/${IDS['Sensex Credit Spread']}/geek/replay`);
  await ready(p);
  assert.match(await p.locator('[data-rp="stage"]').innerText(), /SENSEX at [\d,.]+ can’t be the index of/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('every kind of deployment has it, and it fits a phone', async () => {
  for (const name of ['Nifty Monthly Strangle', 'BankNifty DTT Plain', 'Pivot Supertrend Options', 'BankNifty DTT Adjusted']) {
    const ctx = await context(b, { width: 390, height: 844 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${IDS[name]}/geek/replay`);
    await p.waitForFunction(() => { const el = document.querySelector('#gkView'); return el && el.children.length && !el.querySelector('.skel'); }, null, { timeout: 20000 });
    assert.match(await p.locator('#gkView').innerText(), /Replay/, name);
    if (await p.locator('[data-rp="banner"]').count()) {
      assert.match(await p.locator('[data-rp="banner"]').innerText(), /Entry|New cycle|Before the first trade/, name);
    }
    const s = p.locator('[data-rp="stage"] svg');
    if (await s.count()) {
      assert.match(await s.getAttribute('viewBox'), /^0 0 420 280$/, `${name}: drawn for the phone`);
      const w = await s.evaluate(x => x.getBoundingClientRect().width);
      const cw = await p.locator('[data-rp="stage"]').evaluate(x => x.clientWidth);
      assert.ok(w <= cw + 1, `${name}: the chart fits`);
    }
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${name}: nothing sideways`);
    assert.deepEqual(ctx.errors, [], name);
    await ctx.close();
  }
});

const fmt = v => v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inView = async (p, sel, h) => { const r = await p.locator(sel).first().boundingBox(); return r && r.y >= 0 && r.y + Math.min(r.height, 40) <= h; };

test('one screen: the controls, the payoff and the positions together', async () => {
  const ctx = await context(b, { width: 1440, height: 900 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  for (const sel of ['.rp-ctl', '[data-rp="strip"]', '[data-rp="stage"] svg', '[data-rp="pos"] .tr:not(.th)']) {
    assert.ok(await inView(p, sel, 900), `${sel} is on the first screen`);
  }
  const svgBox = await p.locator('[data-rp="stage"] svg').boundingBox();
  assert.ok(Math.abs(svgBox.height - 295) <= 2, `the chart's height follows the window (${svgBox.height})`);
  // a shorter window: scroll to the positions and the controls stay put, with the chart between
  await p.setViewportSize({ width: 1366, height: 768 });
  await p.waitForTimeout(400);
  await p.locator('[data-rp="pos"] .tr:not(.th)').nth(1).scrollIntoViewIfNeeded();
  assert.ok(await inView(p, '.rp-ctl', 768), 'the controls stay on screen');
  const s = await p.locator('[data-rp="stage"] svg').boundingBox();
  assert.ok(s.y + s.height > 150 && s.y < 768, 'and the payoff is still in sight');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('every position, open or closed, shows its entry, its exit and what it made', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  assert.deepEqual(await p.locator('[data-rp="pos"] .tr.th > span').allInnerTexts(), ['Instrument', 'Qty', 'Entry', 'Exit', 'Now', 'IV', 'P&L', '%', 'Status']);
  await p.click('[data-act="to"][data-f="' + (d.frames.length - 1) + '"]');
  await p.waitForTimeout(300);
  const shut = d.legs.filter(l => l.t1);
  assert.ok(shut.length, 'the fixture has closed legs');
  const closedRows = p.locator('[data-rp="pos"] .tr.rp-closed');
  assert.equal(await closedRows.count(), shut.length, 'every closed leg is still listed');
  const text = (await closedRows.allInnerTexts()).join('\n');
  for (const l of shut) {
    assert.ok(text.includes(fmt(l.entry)), `entry ${fmt(l.entry)} of ${l.symbol}`);
    assert.ok(text.includes(fmt(l.exit)), `exit ${fmt(l.exit)} of ${l.symbol}`);
  }
  assert.match(text, /[+−]\d+\.\d%/, 'and the % it made');
  const open = p.locator('[data-rp="pos"] .tr:not(.th):not(.rp-total):not(.rp-closed)');
  if (await open.count()) assert.match(await open.first().locator('[data-cell="exit"]').innerText(), /open/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('on a phone, play and step stay in reach at the bottom', async () => {
  const ctx = await context(b, { width: 390, height: 844 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  await p.locator('[data-rp="pos"]').scrollIntoViewIfNeeded();
  const bar = p.locator('.rp-mini');
  const box = await bar.boundingBox();
  assert.ok(box && box.y + box.height >= 840 && box.y > 700, `pinned to the bottom (${box && box.y})`);
  assert.match(await bar.innerText(), /Play[\s\S]*\d\d:\d\d[\s\S]*₹/);
  await bar.locator('[data-act="fwd"]').click();
  assert.equal(await step(p), 1);
  await bar.locator('[data-act="play"]').click();
  assert.match(await bar.locator('.rp-play').innerText(), /Pause/);
  await bar.locator('[data-act="play"]').click();
  assert.match(await bar.locator('.rp-play').innerText(), /Play/);
  assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'nothing sideways');
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the mini bar stays out of the way on a desktop', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await ready(p);
  assert.equal(await p.locator('.rp-mini').isVisible(), false);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

// The timeline was drawn 1000 wide and scaled to fit 48px high, so on a
// wide window the drawing sat centred with blank space either side while a
// click was measured across the whole box: away from the middle, the step
// you got wasn't the one under the pointer.
test('the timeline: a click or a drag lands on the step under the pointer, at any width', async () => {
  for (const width of [1600, 1100, 390]) {
    const ctx = await context(b, { width, height: 900 });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
    await ready(p);
    const n = await p.evaluate(() => +document.querySelector('[data-range]').max + 1);
    const track = await p.locator('[data-rp="track"] svg').boundingBox();
    const head = async () => { const c = await p.locator('[data-rp="track"] svg circle').boundingBox(); return c.x + c.width / 2; };
    const slack = track.width / n + 2;   // one step's width, and rounding
    for (const f of [0.03, 0.5, 0.97]) {
      const x = track.x + f * track.width;
      await p.mouse.click(x, track.y + track.height / 2);
      await p.waitForTimeout(50);
      const hx = await head();
      assert.ok(Math.abs(hx - x) <= slack, `at ${width}px, clicked ${Math.round(x)}, the playhead went to ${Math.round(hx)}`);
    }
    // the ends of the drawing are the ends of the box
    const line = await p.locator('[data-rp="track"] svg path').first().boundingBox();
    assert.ok(line.x - track.x <= 6 && track.x + track.width - (line.x + line.width) <= 6,
      `at ${width}px the P&L line spans the timeline (${Math.round(line.x - track.x)}px and ${Math.round(track.x + track.width - line.x - line.width)}px spare)`);
    // dragging follows the pointer too
    await p.mouse.move(track.x + 0.2 * track.width, track.y + 20);
    await p.mouse.down();
    await p.mouse.move(track.x + 0.7 * track.width, track.y + 20, { steps: 5 });
    await p.mouse.up();
    assert.ok(Math.abs(await head() - (track.x + 0.7 * track.width)) <= slack, `at ${width}px a drag ends under the pointer`);
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  }
});

test('Explain: the replay told in words, the playhead going to each moment as it’s said', async () => {
  const ctx = await context(b);
  // a stand-in voice: records what's said and finishes at once
  await ctx.addInitScript(() => {
    window.__said = [];
    const synth = { speaking: false, getVoices: () => [{ lang: 'en-IN', name: 'test' }], cancel() {}, pause() {}, resume() {},
      speak(u) { window.__said.push(u.text); setTimeout(() => u.onend && u.onend(), 5); } };
    Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
    window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  });
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  const B = d.explain.beats;
  assert.ok(B.length >= 3 && B[0].kind === 'intro' && B[B.length - 1].kind === 'summary');
  assert.ok(B.some(x => x.kind === 'trade'));
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await p.waitForSelector('[data-act="explain"]');
  await p.click('[data-act="explain"]');
  await p.waitForSelector('[data-explain]');
  assert.equal(await p.innerText('[data-explain-text]'), B[0].text);
  // on to a trade: the playhead goes to its moment and it's said
  const k = B.findIndex(x => x.kind === 'trade');
  await until(async () => (await p.innerText('[data-explain-n]')) === `${k + 1} of ${B.length}`);
  assert.equal(await p.innerText('[data-explain-text]'), B[k].text);
  await until(async () => (await p.locator('[data-range]').inputValue()) === String(B[k].frame));
  await until(async () => (await p.evaluate(() => window.__said.join(' '))).includes('rupees'));
  assert.ok(!(await p.evaluate(() => window.__said.join(' '))).includes('₹'), 'amounts said as “4,388 rupees”');
  // pause holds it; Next moves on; ✕ ends it
  await p.click('[data-act="xPause"]');
  const at = await p.innerText('[data-explain-n]');
  await p.waitForTimeout(400);
  assert.equal(await p.innerText('[data-explain-n]'), at, 'paused');
  await p.click('[data-act="xNext"]');
  await until(async () => (await p.innerText('[data-explain-n]')) !== at);
  await p.click('[data-act="xStop"]');
  await until(async () => (await p.locator('[data-explain]').count()) === 0);
  // to the end on its own, with the voice off: captions only
  await p.click('[data-act="explain"]');
  await p.waitForSelector('[data-explain]');
  await p.click('[data-act="xVoice"]');
  assert.equal(await p.locator('[data-act="xVoice"]').innerText(), 'Voice off');
  await ctx.close();
});
