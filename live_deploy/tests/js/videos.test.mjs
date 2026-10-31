import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { eta, mmss, working } from '../../static/linck/js/data/videos.js';

test('a length as minutes and seconds', () => {
  assert.equal(mmss(0), '0:00');
  assert.equal(mmss(65.9), '1:05');
  assert.equal(mmss(556.6), '9:16');
  assert.equal(mmss(null), '—');
});

test('the time left comes from the pace so far, once there is one', () => {
  const t0 = Date.parse('2026-10-31T10:00:00Z');
  const v = (progress, status = 'drawing') => ({ status, progress, started_at: new Date(t0).toISOString() });
  assert.equal(eta(v(0.02), t0 + 60e3), null, 'too early to tell');
  assert.equal(eta(v(0.25), t0 + 5 * 60e3), 'about 15 min left');
  assert.equal(eta(v(0.9), t0 + 4 * 60e3), 'under a minute left');
  assert.equal(eta(v(1, 'done'), t0 + 60e3), null);
  assert.equal(eta({ status: 'queued', progress: 0, started_at: null }), null);
  assert.ok(working({ status: 'voicing' }) && !working({ status: 'failed' }));
});
