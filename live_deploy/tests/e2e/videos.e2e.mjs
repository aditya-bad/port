// Videos on the replay page (pages/geek/videos.js, app/video/): make one of
// the replay shown, watch it being made, find it again after leaving the
// page, play it with its chapters, and delete it. The server here speaks
// with a stand-in voice at two frames a second (tests/e2e/serve.py).
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context, api, until } from './helper.mjs';

const ID = IDS['BankNifty DTT Plain'];
let b;
test.before(async () => { b = await browser(); });
test.after(async () => {
  const ctx = await context(b);
  for (const v of await api(ctx, `/v2/deployments/${ID}/videos`)) await ctx.request.delete(`${BASE}/v2/videos/${v.id}`);
  await ctx.close();
  await b.close();
});

test('make a video of the replay, watch it with its chapters, delete it', async () => {
  const ctx = await context(b);
  let p = await ctx.newPage();
  const d = await api(ctx, `/v2/deployments/${ID}/geek/replay`);
  const [a] = d.selected, label = d.episodes.find(e => e.n === a).label;
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await p.waitForSelector('[data-act="video"]', { timeout: 20000 });
  assert.equal(await p.locator('[data-videos]').count(), 0, 'no panel until asked, with no videos yet');
  await p.click('[data-act="video"]');
  await p.waitForSelector('[data-videos]');
  assert.match(await p.locator('[data-videos]').innerText(), new RegExp(`Make a video of\\s+${label}`));
  assert.equal(await p.locator('[data-vid-voice] option').count(), 4);
  await p.selectOption('[data-vid-voice]', 'bf_emma');
  await p.click('[data-act="vMake"]');
  await p.waitForSelector('.vid-row');
  const row = p.locator('.vid-row').first();
  assert.match(await row.innerText(), new RegExp(label));
  // being made: a status and a bar, and the button can't start a second one
  await until(async () => /Waiting its turn|Recording the voice|Drawing the frames|Ready/.test(await row.innerText()));
  // it keeps going without the page: close it, and find it again in a new one
  await p.close();
  p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${ID}/geek/replay`);
  await p.waitForSelector('[data-act="video"]', { timeout: 60000 });
  await p.waitForSelector('[data-videos] .vid-row', { timeout: 60000 });
  await until(async () => (await p.locator('[data-act="vWatch"]').count()) === 1, 180000);
  const v = (await api(ctx, `/v2/deployments/${ID}/videos`))[0];
  assert.equal(v.status, 'done'); assert.equal(v.voice, 'bf_emma');
  assert.ok(v.seconds > 20 && v.chapters.length >= 5, JSON.stringify(v).slice(0, 300));
  await p.click('[data-act="vWatch"]');
  await p.waitForSelector('[data-vid-player] video');
  assert.equal(await p.locator('[data-vid-player] video').getAttribute('src'), `/v2/videos/${v.id}/file`);
  assert.equal(await p.locator('[data-act="vSeek"]').count(), v.chapters.length);
  assert.match(await p.locator('.vid-chaps').innerText(), /How it plays[\s\S]*The market/);
  assert.equal(await p.locator('[data-vid-dl]').getAttribute('href'), `/v2/videos/${v.id}/file?download=1`);
  const f = await ctx.request.get(`${BASE}/v2/videos/${v.id}/file`);
  assert.equal(f.headers()['content-type'], 'video/mp4');
  // delete it
  await p.click('[data-act="vDelete"]');
  await p.waitForSelector('.dialog');
  assert.match(await p.innerText('.dialog'), /Delete this video\?/);
  await p.click('.dialog [data-ok]');
  await until(async () => (await p.locator('.vid-row').count()) === 0);
  assert.deepEqual(await api(ctx, `/v2/deployments/${ID}/videos`), []);
  assert.deepEqual(ctx.errors || [], []);
  await ctx.close();
});
