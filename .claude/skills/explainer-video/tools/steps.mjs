// The narration of a project, read from its scenes.js without drawing
// anything: [{ title, dur, steps: [{ at, text, say }] }] on stdout.
//   node steps.mjs <project>/scenes.js
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const src = readFileSync(process.argv[2], 'utf8');
const out = [];
const sandbox = new Proxy({ scene: (meta) => out.push(meta), console }, {
  has: () => true,                                        // any helper the file mentions at its top level is a no-op
  get: (o, k) => (k in o ? o[k] : k === Symbol.unscopables ? undefined : () => ({})),
});
vm.runInNewContext(`with (sandbox) {\n${src}\n}`, { sandbox });
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
