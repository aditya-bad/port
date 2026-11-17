// A title card as a picture, in the explainer's own fonts and colours.
//   node card.mjs <out.png> <title> [subtitle]   (SCALE, SCHEME as render.mjs)
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const EV = process.env.EV_HOME || path.join(os.homedir(), '.cache/explainer-video');
const { chromium } = await import(process.env.PW || path.join(EV, 'node/node_modules/playwright/index.mjs'));
const [out, title, sub = ''] = process.argv.slice(2);
const here = path.dirname(new URL(import.meta.url).pathname);
const css = ['fonts.css', 'tokens.css'].map(f => fs.readFileSync(path.join(here, '../engine', f), 'utf8')).join('\n');
const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const html = `<!doctype html><meta charset="utf-8"><style>${css}
html,body{margin:0;height:100%;background:var(--paper);color:var(--ink)}
body{display:grid;place-content:center;gap:28px;padding:0 140px;font-family:var(--f-body)}
h1{font-family:var(--f-display);font-size:86px;line-height:1.05;margin:0;text-wrap:balance;letter-spacing:-.01em}
p{font-size:34px;margin:0;color:var(--mute);text-wrap:balance}
i{display:block;width:120px;height:10px;background:var(--y);border-radius:5px}</style>
<i></i><h1>${esc(title)}</h1>${sub ? `<p>${esc(sub)}</p>` : ''}`;
const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const p = await b.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: +(process.env.SCALE || 1),
  colorScheme: process.env.SCHEME === 'dark' ? 'dark' : 'light' });
await p.setContent(html);
await p.evaluate(() => document.fonts.ready);
await p.screenshot({ path: out });
await b.close();
