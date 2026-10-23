// The book's loader (static/linck/js/data/book.js load): a kind that has
// landed is what every reader sees, even while a slower kind in the same
// batch is still loading.
import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';

const dep = (id, name) => ({ id, deployment_name: name, strategy_name: 'strangle_monthly_v2', mode: 'positional', status: 'active',
  initial_capital: 100000, current_cash: 100000, config: {}, tags: [], created_at: '2026-10-01T04:00:00Z' });

test('a kind that has landed is read at once, not after the slowest one', async () => {
  let release;
  const slow = new Promise(r => { release = r; });
  globalThis.fetch = async path => {
    if (path === '/v2/shadow/scorecards') await slow;
    const body = path === '/deployments' ? [dep('a', 'A'), dep('b', 'B')] : path === '/v2/shadow/scorecards' ? {} : [];
    return { ok: true, status: 200, text: async () => JSON.stringify(body) };
  };
  const book = await import('../../static/linck/js/data/book.js');
  assert.deepEqual(book.deployments(), [], 'nothing yet (and memoized so)');
  const done = book.load(['deployments', 'cards']);
  for (let i = 0; i < 20 && !book.isLoaded('deployments'); i++) await new Promise(r => setTimeout(r, 5));
  assert.equal(book.isLoaded('deployments'), true);
  assert.equal(book.isLoaded('cards'), false, 'the slow one is still loading');
  assert.deepEqual(book.deployments().map(v => v.id), ['a', 'b'], 'not the memoized empty list');
  release();
  await done;
  assert.equal(book.isLoaded('cards'), true);
});
