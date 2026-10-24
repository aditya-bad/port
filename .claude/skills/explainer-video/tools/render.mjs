// Record a built page as video, frame by frame at 25 fps by seeking the
// page's own clock (nothing dropped; the voice lines up to the frame).
//   FF=<ffmpeg> node render.mjs <project>/out/<slug>.html [scenes e.g. 1,3]
// → <project>/out/vid/NN-v.mp4 (picture only) and NN.json (the scene's plan).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const EV = process.env.EV_HOME || path.join(os.homedir(), '.cache/explainer-video');
const { chromium } = await import(process.env.PW || path.join(EV, 'node/node_modules/playwright/index.mjs'));
const html = path.resolve(process.argv[2]), FF = process.env.FF, FPS = 25;
if (!FF) { console.error('render.mjs: FF is not set. Run: eval "$(bash $S/tools/setup.sh | grep =)"; export EV_HOME FF'); process.exit(2); }
const dir = path.join(path.dirname(html), 'vid');
fs.mkdirSync(dir, { recursive: true });
const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const probe = await b.newPage();
await probe.goto(`file://${html}?record=1&seek=1&scene=1`);
const n = await probe.evaluate(() => SCENES.length);
await probe.close();
const want = process.argv[3] ? process.argv[3].split(',').map(Number) : Array.from({ length: n }, (_, i) => i + 1);
async function one(s) {
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  await p.goto(`file://${html}?record=1&seek=1&scene=${s}`);
  const plan = await p.evaluate(s => window.__plan(s), s);
  const nn = String(s).padStart(2, '0');
  fs.writeFileSync(path.join(dir, `${nn}.json`), JSON.stringify({ ...plan, title: await p.evaluate(s => SCENES[s - 1].title, s) }));
  const ff = spawn(FF, ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-pix_fmt', 'yuv420p', '-r', String(FPS), path.join(dir, `${nn}-v.mp4`)]);
  const done = new Promise((res, rej) => ff.on('close', c => (c ? rej(new Error(`ffmpeg exited ${c}`)) : res())));
  const frames = Math.ceil(plan.total * FPS);
  for (let f = 0; f < frames; f++) {
    await p.evaluate(([s, t]) => window.__seek(s, t), [s, f / FPS]);
    if (!ff.stdin.write(await p.screenshot({ type: 'jpeg', quality: 90 }))) await new Promise(r => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  await done;
  await p.close();
  console.log(`scene ${s}: ${frames} frames`);
}
const q = [...want];
await Promise.all(Array.from({ length: Math.min(4, os.cpus().length) }, async () => { while (q.length) await one(q.shift()); }));
await b.close();
