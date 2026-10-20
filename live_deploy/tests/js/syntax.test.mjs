// Every Linck module parses as an ES module. A duplicate declaration
// (`const x` twice in one scope) made the legs table's module fail to
// load, and with it the Desk and every deployment's Now tab.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const ROOT = new URL('../../static/linck/', import.meta.url).pathname;
const files = dir => readdirSync(dir).flatMap(f => {
  const p = join(dir, f);
  return statSync(p).isDirectory() ? files(p) : p.endsWith('.js') ? [p] : [];
});

test('every Linck module parses', () => {
  const all = [...files(join(ROOT, 'js')), ...files(join(ROOT, 'login'))];
  assert.ok(all.length > 30);
  const bad = all.flatMap(f => {
    const r = spawnSync(process.execPath, ['--input-type=module', '--check'], { input: readFileSync(f) });
    return r.status === 0 ? [] : [`${f.slice(ROOT.length)}: ${String(r.stderr).split('\n').find(l => /Error/.test(l))}`];
  });
  assert.deepEqual(bad, []);
});
