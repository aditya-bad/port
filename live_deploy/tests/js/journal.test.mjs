import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseTags, tagsText, addTag, problem, byDay, dayNotes } from '../../static/linck/js/data/journal.js';

test('tags: lower case, single spaces, no duplicates, as the server keeps them', () => {
  assert.deepEqual(parseTags('Event Day, news,  event   day ,, '), ['event day', 'news']);
  assert.deepEqual(parseTags(''), []);
  assert.deepEqual(parseTags(null), []);
  assert.equal(tagsText(['a', 'b c']), 'a, b c');
  assert.equal(addTag('news, gap open', 'News'), 'news, gap open', 'adding one already there changes nothing');
  assert.equal(addTag('', 'kite down'), 'kite down');
});

test('a note that the server would refuse says why before it is sent', () => {
  assert.equal(problem('   ', []), 'Write something in the note.');
  assert.match(problem('x'.repeat(4001), []), /4,000/);
  assert.match(problem('ok', Array.from({ length: 11 }, (_, i) => `t${i}`)), /At most 10 tags/);
  assert.match(problem('ok', ['x'.repeat(31)]), /at most 30 characters/);
  assert.equal(problem('fine', ['ok']), '');
});

test('notes grouped by day, and a day’s notes as tooltip lines', () => {
  const E = [{ day: '2026-10-20', body: 'a' }, { day: '2026-10-20', body: 'b' }, { day: '2026-10-19', body: 'c'.repeat(80) }];
  assert.deepEqual(byDay(E).map(([d, n]) => [d, n.length]), [['2026-10-20', 2], ['2026-10-19', 1]]);
  assert.deepEqual(dayNotes(E, '2026-10-20'), ['Note  a', 'Note  b']);
  assert.equal(dayNotes(E, '2026-10-19')[0].length, 'Note  '.length + 60, 'long notes are cut');
  assert.deepEqual(dayNotes(E, '2026-10-20', 1), ['Note  a', 'and 1 more note']);
  assert.deepEqual(dayNotes(E, '2026-10-18'), []);
});
