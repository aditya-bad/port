import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { line, yOf } from '../../static/linck/js/ui/charts.js';

const fills = svg => [...svg.matchAll(/<path d="[^"]*Z" fill="([^"]+)"(?: clip-path="url\(#(\w+)\)" data-fill="(\w+)")?/g)]
  .map(m => ({ fill: m[1], clip: m[2], side: m[3] }));
const clipRect = (svg, id) => {
  const m = svg.match(new RegExp(`<clipPath id="${id}"><rect x="0" y="([-\\d.]+)" width="1000" height="([\\d.]+)"/>`));
  return m && { y: +m[1], h: +m[2] };
};

test('equity below zero is filled red, above zero green, split at the zero line', () => {
  const values = [0, 500, -1600, -900, 1300];
  const svg = String(line({ series: [{ values, fill: 'var(--gs)', fillNeg: 'var(--rs)', fillTo: 0 }], h: 200, lo: -1600, hi: 1300, zero: true }));
  const f = fills(svg);
  assert.deepEqual(f.map(x => [x.fill, x.side]), [['var(--gs)', 'up'], ['var(--rs)', 'down']]);
  const zero = yOf(0, 200, -1600, 1300);
  const up = clipRect(svg, f[0].clip), dn = clipRect(svg, f[1].clip);
  assert.ok(Math.abs(up.y + up.h - zero) < 0.2, 'green stops at zero');
  assert.ok(Math.abs(dn.y - zero) < 0.2 && dn.y + dn.h >= 200, 'red starts at zero and runs to the bottom');
  assert.notEqual(f[0].clip, f[1].clip);
});

test('two charts on one page get their own clip paths', () => {
  const s = () => String(line({ series: [{ values: [-1, 1], fill: 'a', fillNeg: 'b', fillTo: 0 }] }));
  const a = fills(s()), b = fills(s());
  assert.notEqual(a[0].clip, b[0].clip);
});

test('without fillNeg the fill stays one colour', () => {
  const f = fills(String(line({ series: [{ values: [-5, 5], fill: 'var(--rs)', fillTo: 0 }] })));
  assert.deepEqual(f.map(x => x.fill), ['var(--rs)']);
  assert.equal(f[0].clip, undefined);
});
