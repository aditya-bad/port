import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { importSummary } from '../../static/linck/js/data/lensimport.js';

const f = (o) => ({ name: 'tb.csv', rows: 3, added: 3, skipped: 0, first_day: '2024-04-02', last_day: '2025-03-28', problems: [], ...o });

test('a good upload says how many trades are new, file by file', () => {
  const n = importSummary([f(), f({ name: 'mf.csv', rows: 2, added: 1, skipped: 1 })]);
  assert.equal(n.tone, 'good');
  assert.equal(n.title, 'Uploaded: 4 new trades imported');
  assert.match(n.lines[0], /^tb\.csv: 3 trades, 3 new · 2 Apr '24 to 28 Mar '25$/);
  assert.match(n.lines[1], /^mf\.csv: 2 trades, 1 new, 1 already here/);
});

test('the same file again: nothing new, said so', () => {
  const n = importSummary([f({ added: 0, skipped: 3 })]);
  assert.equal(n.tone, 'good');
  assert.equal(n.title, 'Uploaded: nothing new, all 3 trades were already here');
});

test('rows it couldn’t read, or a file with no trades, make it a warning', () => {
  const n = importSummary([f({ problems: ['line 4: bad trade_date'] }), f({ name: 'x.csv', rows: 0, added: 0, first_day: null, problems: ['no tradebook rows found'] })]);
  assert.equal(n.tone, 'warn');
  assert.match(n.title, /^Uploaded: 3 new trades imported; some of it couldn’t be read$/);
  assert.match(n.lines[0], /1 row not read: line 4: bad trade_date/);
  assert.equal(n.lines[1], 'x.csv: no trades found (no tradebook rows found)');
});

test('not a tradebook at all: no trades found, and what it needs', () => {
  const n = importSummary([f({ name: 'notes.txt', rows: 0, added: 0, first_day: null, problems: ['no tradebook rows found'] })]);
  assert.equal(n.tone, 'bad');
  assert.equal(n.title, 'Uploaded, but no trades found in it');
  assert.match(n.lines.at(-1), /Console › Reports › Tradebook/);
});

test('a failed upload says it failed, which files, and why', () => {
  const n = importSummary(null, new Error('Request Entity Too Large'), ['big.csv']);
  assert.equal(n.tone, 'bad');
  assert.equal(n.title, 'Upload failed: nothing was imported');
  assert.match(n.lines[0], /^big\.csv: Request Entity Too Large\./);
});
