import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

// A colour token that isn't defined draws nothing (a chart segment went
// invisible this way): every var(--x) the pages' JavaScript uses must be
// defined in the stylesheets.
const ROOT = new URL('../../static/linck/', import.meta.url).pathname;
const walk = d => readdirSync(d).flatMap(f => { const p = join(d, f); return statSync(p).isDirectory() ? walk(p) : [p]; });

test('every colour and size token the JavaScript uses is defined', () => {
  const css = walk(join(ROOT, 'css')).filter(f => f.endsWith('.css')).map(f => readFileSync(f, 'utf8')).join('\n');
  const defined = new Set([...css.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(m => m[1]));
  const missing = [];
  for (const f of walk(join(ROOT, 'js')).filter(x => x.endsWith('.js'))) {
    const src = readFileSync(f, 'utf8');
    const inline = new Set([...src.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(m => m[1]));   // set in a style="" here
    for (const m of src.matchAll(/var\((--[a-z0-9-]+)/g)) if (!defined.has(m[1]) && !inline.has(m[1])) missing.push(`${m[1]} in ${f.slice(ROOT.length)}`);
  }
  assert.deepEqual([...new Set(missing)], []);
});
