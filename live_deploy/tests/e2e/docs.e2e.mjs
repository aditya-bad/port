// The architecture docs in docs/*.html open with no script errors, both
// directly and the way the claude.ai artifact viewer shows them: inside a
// sandboxed iframe from srcdoc, where history and storage calls throw.
// The narrated animation (linck-visual) is also driven through every
// scene, and its narration checked: every clip decodes, and each step's
// voice ends before the next step starts.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { browser } from './helper.mjs';

const DOCS = new URL('../../docs/', import.meta.url);
const files = readdirSync(DOCS).filter(f => f.endsWith('.html'));
const VISUAL = new URL('linck-visual.html', DOCS).href;

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

async function page() {
  const ctx = await b.newContext({ viewport: { width: 1600, height: 900 } });
  const p = await ctx.newPage();
  p.errors = [];
  p.on('pageerror', e => p.errors.push(e.message));
  return [ctx, p];
}

for (const f of files) {
  test(`${f} opens with no script errors`, async () => {
    const [ctx, p] = await page();
    await p.goto(new URL(f, DOCS).href);
    await p.waitForTimeout(300);
    assert.deepEqual(p.errors, []);
    await ctx.close();
  });

  test(`${f} works inside a sandboxed srcdoc iframe (artifact viewer)`, async () => {
    const [ctx, p] = await page();
    const html = readFileSync(new URL(f, DOCS), 'utf8');
    await p.setContent('<iframe id="f" sandbox="allow-scripts" style="width:1600px;height:900px;border:0"></iframe>');
    await p.$eval('#f', (el, h) => { el.srcdoc = h; }, html);
    const frame = await (await p.waitForSelector('#f')).contentFrame();
    await frame.waitForLoadState('load');
    if (f === 'linck-visual.html') {
      // moving between scenes is what used to call history.replaceState
      await frame.waitForSelector('#layer .ttl');
      await frame.click('#go-muted');
      await frame.click('#next');
      await frame.click('#next');
      await frame.click('#prev');
      assert.match(await frame.textContent('#layer .idx'), /^2 \/ 16$/);
    }
    await p.waitForTimeout(300);
    assert.deepEqual(p.errors, []);
    await ctx.close();
  });
}

test('linck-visual: every scene draws from start to end, with captions, without errors', async () => {
  const [ctx, p] = await page();
  await p.goto(`${VISUAL}?record=1&seek=1&scene=1`);
  const n = await p.evaluate(() => SCENES.length);
  assert.equal(n, 16);
  for (let s = 1; s <= n; s++) {
    const plan = await p.evaluate(s => window.__plan(s), s);
    assert.ok(plan.steps.length >= 2, `scene ${s} is narrated in steps`);
    for (const st of plan.steps) {
      await p.evaluate(([s, t]) => window.__seek(s, t), [s, st.T0 + 0.05]);
      assert.notEqual((await p.textContent('#cap')).trim(), '', `scene ${s} has a caption at ${st.T0}s`);
    }
    await p.evaluate(([s, t]) => window.__seek(s, t), [s, plan.total]);
    assert.match(await p.textContent('#layer .idx'), new RegExp(`^${s} / 16$`));
  }
  assert.deepEqual(p.errors, []);
  await ctx.close();
});

test('linck-visual: each step waits for its narration, and the animation never runs ahead', async () => {
  const [ctx, p] = await page();
  await p.goto(`${VISUAL}?record=1&seek=1&scene=1`);
  const bad = await p.evaluate(() => {
    const out = [];
    SCENES.forEach((sc, i) => {
      const pl = plan(i), st = pl.steps;
      if (st[0].at !== 0) out.push(`scene ${i + 1}: first step must start at 0`);
      st.forEach((x, j) => {
        if (!x.src || !x.caps.length) out.push(`scene ${i + 1} step ${j}: no clip or caption`);
        const next = j + 1 < st.length ? st[j + 1].T0 : pl.total;
        if (next - x.T0 < x.dur + 0.5) out.push(`scene ${i + 1} step ${j}: voice runs into the next step`);
        if (j + 1 < st.length && st[j + 1].at <= x.at) out.push(`scene ${i + 1} step ${j}: steps out of order`);
        // the animation reaches the next step's moment only when that step begins
        const [tEnd] = clock(pl, next - 0.001);
        if (tEnd > (j + 1 < st.length ? st[j + 1].at : sc.dur) + 1e-6) out.push(`scene ${i + 1} step ${j}: runs ahead`);
        if (x.rate > 0.6 + 1e-9) out.push(`scene ${i + 1} step ${j}: faster than 0.6×`);
      });
    });
    return out;
  });
  assert.deepEqual(bad, []);
  await ctx.close();
});

test('linck-visual: every narration clip decodes, at the length the plan expects', async () => {
  const [ctx, p] = await page();
  await p.goto(`${VISUAL}?record=1&seek=1&scene=1`);
  const bad = await p.evaluate(async () => {
    const out = [];
    for (const [i, steps] of NARR.entries()) for (const [j, st] of steps.entries()) {
      const d = await new Promise(res => { const a = new Audio(); a.onloadedmetadata = () => res(a.duration); a.onerror = () => res(-1); a.src = st.src; });
      if (Math.abs(d - st.dur) > 0.2) out.push(`scene ${i + 1} step ${j}: ${d} s, expected ${st.dur}`);
    }
    return out;
  });
  assert.deepEqual(bad, []);
  await ctx.close();
});

test('linck-visual: the start screen starts with sound or captions only', async () => {
  const [ctx, p] = await page();
  await p.goto(VISUAL);
  assert.ok(await p.isVisible('#start'), 'a start screen first: browsers need a click before sound');
  const prog = await p.getAttribute('.prog', 'width');
  await p.waitForTimeout(300);
  assert.equal(await p.getAttribute('.prog', 'width'), prog, 'nothing plays behind it');
  await p.click('#go');
  assert.ok(!(await p.isVisible('#start')));
  assert.equal(await p.textContent('#snd'), '🔊');
  await p.waitForFunction(() => !aud.paused && aud.currentTime > 0, null, { timeout: 5000 });
  assert.match(await p.textContent('#cap'), /\w/, 'captions show while it speaks');
  await p.click('#pp');
  assert.ok(await p.evaluate(() => aud.paused), 'pausing pauses the voice');
  await p.click('#snd');
  assert.equal(await p.textContent('#snd'), '🔇');
  assert.deepEqual(p.errors, []);
  await ctx.close();
});

test('linck-visual: with reduced motion, each scene shows its finished picture and all its words', async () => {
  const ctx = await b.newContext({ viewport: { width: 1600, height: 900 }, reducedMotion: 'reduce' });
  const p = await ctx.newPage();
  const errors = []; p.on('pageerror', e => errors.push(e.message));
  await p.goto(`${VISUAL}#6`);
  assert.ok(!(await p.isVisible('#start')));
  assert.match(await p.textContent('#cap'), /forked from that parent/);
  assert.deepEqual(errors, []);
  await ctx.close();
});

test('linck-visual: keys and dots move between scenes; space pauses', async () => {
  const [ctx, p] = await page();
  await p.goto(`${VISUAL}#3`);
  const idx = () => p.textContent('#layer .idx');
  assert.equal(await idx(), '3 / 16', 'opens at the scene in the address');
  await p.keyboard.press('ArrowRight');
  assert.ok(!(await p.isVisible('#start')), 'an arrow key starts it, captions only');
  assert.equal(await idx(), '4 / 16');
  await p.keyboard.press('ArrowLeft');
  await p.keyboard.press('ArrowLeft');
  assert.equal(await idx(), '2 / 16');
  await p.click('.dots button:nth-child(16)');
  assert.equal(await idx(), '16 / 16');
  await p.keyboard.press('ArrowRight');
  assert.equal(await idx(), '1 / 16', 'wraps around');
  await p.keyboard.press(' ');
  assert.equal(await p.textContent('#pp'), '▶');
  await p.waitForTimeout(100);   // let the paused frame draw
  const w1 = await p.getAttribute('.prog', 'width');
  await p.waitForTimeout(400);
  assert.equal(await p.getAttribute('.prog', 'width'), w1, 'paused: progress holds');
  assert.deepEqual(p.errors, []);
  await ctx.close();
});
