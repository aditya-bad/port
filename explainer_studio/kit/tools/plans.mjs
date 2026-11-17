// Each scene's plan from a built page (step start times, voice lengths),
// for subtitles and chapters without recording a frame.
//   node plans.mjs <project>/out/<slug>.html   → [{ title, total, steps }] on stdout
import os from 'node:os';
import path from 'node:path';
const EV = process.env.EV_HOME || path.join(os.homedir(), '.cache/explainer-video');
const { chromium } = await import(process.env.PW || path.join(EV, 'node/node_modules/playwright/index.mjs'));
const html = path.resolve(process.argv[2]);
const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
await p.goto(`file://${html}?record=1&seek=1&scene=1`);
const out = await p.evaluate(() => SCENES.map((s, i) => ({ title: s.title, ...window.__plan(i + 1) })));
await b.close();
process.stdout.write(JSON.stringify(out));
