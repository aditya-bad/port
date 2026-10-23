// The architecture docs in docs/*.html open with no script errors, both
// directly and the way the claude.ai artifact viewer shows them: inside a
// sandboxed iframe from srcdoc, where history and storage calls throw.
// The animated one (linck-visual) is also driven through every scene.
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

test('linck-visual: every scene draws from start to end without errors', async () => {
  const [ctx, p] = await page();
  await p.goto(`${VISUAL}?record=1&scene=1&t=0`);
  const durs = await p.evaluate(() => SCENES.map(s => s.dur));
  assert.equal(durs.length, 16);
  for (let n = 1; n <= durs.length; n++) {
    for (const t of [0, durs[n - 1] / 2, durs[n - 1]]) {
      await p.goto(`${VISUAL}?record=1&scene=${n}&t=${t}`);
      await p.waitForFunction(() => window.__sceneDone === true);
      assert.match(await p.textContent('#layer .idx'), new RegExp(`^${n} / 16$`));
    }
  }
  assert.deepEqual(p.errors, []);
  await ctx.close();
});

test('linck-visual: keys and dots move between scenes; space pauses', async () => {
  const [ctx, p] = await page();
  await p.goto(`${VISUAL}#3`);
  const idx = () => p.textContent('#layer .idx');
  assert.equal(await idx(), '3 / 16', 'opens at the scene in the address');
  await p.keyboard.press('ArrowRight');
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
