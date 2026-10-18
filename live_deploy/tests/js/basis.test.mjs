import './setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import * as B from '../../static/linck/js/core/basis.js';

const row = { realized_pnl: 1000, realized_pnl_net: 900, realized_pnl_net_slip: 850 };

test('defaults to gross, honours the classic toggle', () => {
  localStorage.clear();
  assert.equal(B.basis(), 'gross');
  localStorage.setItem('includeCharges', 'true');
  assert.equal(B.basis(), 'charges', "the classic app's old switch carries over");
  localStorage.setItem('pnlMode', 'nonsense');
  assert.equal(B.basis(), 'charges');
});

test('cycles gross → charges → all and notifies', () => {
  localStorage.clear();
  const seen = [];
  const off = B.onBasis(k => seen.push(k));
  B.cycleBasis(); B.cycleBasis(); B.cycleBasis();
  off();
  B.cycleBasis();
  assert.deepEqual(seen, ['charges', 'all', 'gross']);
  assert.equal(localStorage.getItem('pnlMode'), 'charges');
});

test('pick follows the basis and falls back when a figure is missing', () => {
  B.setBasis('gross');
  assert.equal(B.pick(row, 'realized_pnl'), 1000);
  B.setBasis('charges');
  assert.equal(B.pick(row, 'realized_pnl'), 900);
  B.setBasis('all');
  assert.equal(B.pick(row, 'realized_pnl'), 850);
  assert.equal(B.pick({ realized_pnl: 1000, realized_pnl_net: 900 }, 'realized_pnl'), 900);
  assert.equal(B.pick({ realized_pnl: 1000 }, 'realized_pnl'), 1000);
  assert.equal(B.pick(null, 'x'), null);
  assert.equal(B.pick({}, 'x'), null);
});

test('v2 book rows and session baselines', () => {
  B.setBasis('all');
  assert.equal(B.pnlOf({ pnl: 10, pnl_net: 8, pnl_net_slip: 7 }), 7);
  assert.equal(B.baseOf({ gross: 100, net: 90, net_slip: 85 }), 85);
  B.setBasis('charges');
  assert.equal(B.baseOf({ gross: 100, net: 90, net_slip: 85 }), 90);
  assert.equal(B.baseOf(null), 0);
  assert.equal(B.current().stamp, 'NET');
});
