// The narration of a project, read from its scenes.js without drawing
// anything: [{ title, dur, steps: [{ at, text, say }] }] on stdout.
//   node steps.mjs <project>/scenes.js
// Drawing helpers are stubs here; beats() and diagram.js are the real ones,
// so steps timed from their words (and flowScene's steps) come out the same
// as in the page. A scene file's own top-level functions and constants work
// as written.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
const here = path.dirname(new URL(import.meta.url).pathname);
const src = readFileSync(process.argv[2], 'utf8');
const out = [];
const base = { scene: (meta) => out.push(meta), console, Math, JSON, Object, Array, String, Number };
const ctx = vm.createContext(new Proxy(base, {
  has: () => true,                                        // a helper the file uses but doesn't define is a no-op
  get: (o, k) => (k in o ? o[k] : typeof k === 'string' && /^[A-Za-z_$]/.test(k) ? () => ({}) : undefined),
}));
for (const f of ['beats.js', 'diagram.js']) vm.runInContext(readFileSync(path.join(here, '../engine', f), 'utf8'), ctx);   // flowScene() calls scene()
vm.runInContext(src, ctx);
const bad = [];
const scenes = out.map((m, i) => {
  const steps = (m.steps || []).map(([at, text, o = {}], j) => {
    if (typeof at !== 'number' || typeof text !== 'string' || !text.trim()) bad.push(`scene ${i + 1} step ${j + 1}: needs [at, 'text']`);
    return { at, text, say: o.say || null };
  });
  steps.forEach((s, j) => { if (j && s.at <= steps[j - 1].at) bad.push(`scene ${i + 1} step ${j + 1}: at must increase`); });
  if (steps.length && steps[0].at !== 0) bad.push(`scene ${i + 1}: the first step starts at 0`);
  if (steps.length && steps[steps.length - 1].at >= m.dur) bad.push(`scene ${i + 1}: the last step must start before dur`);
  return { title: m.title, dur: m.dur, steps };
});
if (bad.length) { console.error(bad.join('\n')); process.exit(1); }
process.stdout.write(JSON.stringify(scenes, null, 1));
