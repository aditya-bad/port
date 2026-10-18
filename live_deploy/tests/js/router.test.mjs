import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parse, href, ROUTES } from '../../static/linck/js/core/router.js';
import { requestTokenFrom } from '../../static/linck/js/actions.js';
import { ui, emit, listen } from '../../static/linck/js/core/store.js';

test('routes and params', () => {
  assert.equal(parse('').route.path, 'desk');
  assert.equal(parse('#/deployments').route.path, 'deployments');
  const d = parse('#/d/abc-123/trades?cycle=4');
  assert.equal(d.route.path, 'd/:id/:tab?');
  assert.deepEqual(d.params, { id: 'abc-123', tab: 'trades', cycle: '4' });
  assert.deepEqual(parse('#/d/abc').params, { id: 'abc' }, 'tab is optional');
  assert.equal(parse('#/tools').route.path, 'tools/:tab?');
  assert.equal(parse('#/nowhere').route.path, 'desk', 'unknown routes land on the Desk');
  assert.equal(parse('#/d/a%20b').params.id, 'a b');
  assert.equal(href('reports'), '#/reports');
  assert.ok(ROUTES.every(r => typeof r.load === 'function'));
});

test('a pasted redirect URL or a bare request_token both work', () => {
  assert.equal(requestTokenFrom('abc123'), 'abc123');
  assert.equal(requestTokenFrom('  abc123  '), 'abc123');
  assert.equal(requestTokenFrom('https://x.example/kite/callback?status=success&request_token=Xy%2Fz9&action=login'), 'Xy/z9');
  assert.equal(requestTokenFrom('https://x/?request_token=tok#frag'), 'tok');
  assert.equal(requestTokenFrom(''), '');
  assert.equal(requestTokenFrom(null), '');
});

test('per-page UI state persists and the bus delivers', () => {
  const s = ui('test-page', { q: '', sort: 'pnl' });
  assert.deepEqual(s.get(), { q: '', sort: 'pnl' });
  s.set({ q: 'nifty' });
  assert.deepEqual(JSON.parse(localStorage.getItem('linck-ui'))['test-page'], { q: 'nifty', sort: 'pnl' });
  const got = [];
  const off = listen('data', d => got.push(d));
  emit('data', 1); off(); emit('data', 2);
  assert.deepEqual(got, [1]);
});
