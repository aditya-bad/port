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

test('ready for live: short form for tables, with every rule in the tip', async () => {
  const { readyShort, readyCard } = await import('../../static/linck/js/ui/shadow.js');
  assert.deepEqual(readyShort(null), { text: 'No record', cls: 'unk', tip: 'No fills checked in shadow mode yet.' });
  const rules = [{ key: 'record', ok: false, label: 'At least 20 sessions in shadow', value: '12 of 20' }, { key: 'repairs', ok: true, label: 'No open Repair case', value: 'none' }];
  const c = { state: 'not_yet', ready: false, rules, accepted_pct: 98.5, filled_pct: 90, shortfall: 1200, shortfall_pct: 0.31, worst: { shortfall: 150 },
    refused: { n: 1, pnl: 5000, pnl_net: 4800, pnl_net_slip: 4700 }, unfilled: { n: 0 }, total: { pnl: 9000, pnl_net: 8000, pnl_net_slip: 7500 }, splits: 0 };
  const r = readyShort(c);
  assert.equal(r.text, 'Not yet');
  assert.equal(r.tip, '✗ At least 20 sessions in shadow  12 of 20\n✓ No open Repair case  none');
  const out = String(readyCard(c));
  assert.match(out, /Not ready for live yet/);
  assert.match(out, /1 of 2 still to clear/);
  assert.match(out, /Made on trades Kite would refuse[\s\S]*\+₹5,000[\s\S]*1 trade · of \+₹9,000 in all/);
  assert.doesNotMatch(out, /wouldn’t fill/, 'nothing unfilled: not shown');
  assert.equal(readyShort({ ...c, ready: true, state: 'ready' }).text, 'Ready');
});

test('Live-ready: rupees worse than paper and refusals, per day', async () => {
  const { byDay } = await import('../../static/linck/js/pages/geek/shadow.js');
  const d = byDay([
    { executed_at: '2026-10-22T04:00:00Z', shortfall: 10, verdict: 'worse' },
    { executed_at: '2026-10-22T09:00:00Z', shortfall: null, verdict: 'reject' },
    { executed_at: '2026-10-21T04:00:00Z', shortfall: 0, verdict: 'fill' },
  ]);
  assert.deepEqual(d, [{ day: '2026-10-21', short: 0, rejects: 0, n: 1 }, { day: '2026-10-22', short: 10, rejects: 1, n: 2 }]);
});

test('an estimate from stored data says so on its chip', () => {
  assert.equal(verdictLabel({ ...base, verdict: 'worse', shortfall: 45, estimated: true }), 'Would fill ₹45 worse (est.)');
  assert.equal(verdictLabel({ ...base, verdict: 'unknown', estimated: true }), 'Couldn’t check (est.)');
});
