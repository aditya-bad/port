// Shadow mode's chip (static/linck/js/ui/shadow.js): what it says for
// each verdict, and the tooltip listing every rule checked.
import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { verdictLabel, verdictTip, shadowChip } from '../../static/linck/js/ui/shadow.js';

const base = { action: 'sell', qty: 150, paper_price: 100, limit_price: 99.5, est_price: 99.7, checks: [], estimated: false };

test('each verdict in words', () => {
  assert.equal(verdictLabel({ ...base, verdict: 'fill' }), 'Would fill');
  assert.equal(verdictLabel({ ...base, verdict: 'worse', shortfall: 45 }), 'Would fill ₹45 worse');
  assert.equal(verdictLabel({ ...base, verdict: 'partial', filled_qty: 75 }), 'Would fill 75 of 150');
  assert.equal(verdictLabel({ ...base, verdict: 'no_fill', followup: { reached: false } }), 'Wouldn’t fill in 60 s');
  assert.equal(verdictLabel({ ...base, verdict: 'no_fill', followup: null }), 'Resting at the limit…', 'still inside its 60 s');
  assert.equal(verdictLabel({ ...base, verdict: 'reject', checks: [{ rule: 'lot', status: 'reject', detail: 'x' }] }), 'Would be rejected: lot size');
  assert.equal(verdictLabel({ ...base, verdict: 'unknown' }), 'Couldn’t check');
  assert.equal(verdictLabel(null), '');
});

test('the tooltip: the order live would send, then every rule', () => {
  const t = verdictTip({ ...base, verdict: 'worse', checks: [{ rule: 'freeze', status: 'ok', detail: 'Within the freeze limit of 1,800.' }] });
  assert.match(t, /^Sell 150 · paper ₹100\.00 · limit ₹99\.50 · est\. ₹99\.70\nFreeze limit: Within the freeze limit of 1,800\.\nChecked as the fill happened\.$/);
  assert.match(verdictTip({ ...base, estimated: true }), /Estimated later from stored data\./);
});

test('a fill never checked shows no chip', () => {
  assert.equal(shadowChip(null), '');
  assert.match(String(shadowChip({ ...base, verdict: 'reject', checks: [] })), /class="flag sh-chip bad" data-shadow="reject"/);
});
