// P&L basis: Gross → After charges → After charges + slippage. One
// switch changes every figure at once. Stored under the same key as the
// classic UI's toggle, so both experiences agree.

export const BASES = [
  { k: 'gross', l: 'Gross', stamp: 'GROSS', tip: 'Before charges' },
  { k: 'charges', l: 'After charges', stamp: 'NET', tip: 'After brokerage, STT, exchange fees, GST, stamp' },
  { k: 'all', l: '+ Slippage', long: 'After charges + slippage', stamp: 'NET+SLIP', tip: 'After charges and estimated slippage' },
];
const KEY = 'pnlMode';
const listeners = new Set();

export function basis() {
  try {
    const m = localStorage.getItem(KEY);
    if (BASES.some(b => b.k === m)) return m;
    return localStorage.getItem('includeCharges') === 'true' ? 'charges' : 'gross';
  } catch (e) { return 'gross'; }
}
export function setBasis(k) {
  try { localStorage.setItem(KEY, k); } catch (e) {}
  listeners.forEach(fn => fn(k));
}
export function cycleBasis() {
  const i = BASES.findIndex(b => b.k === basis());
  setBasis(BASES[(i + 1) % BASES.length].k);
}
export const current = () => BASES.find(b => b.k === basis());
export function onBasis(fn) { listeners.add(fn); return () => listeners.delete(fn); }

// v1 rows carry `x`, `x_net`, `x_net_slip`.
export function pick(row, base) {
  if (!row) return null;
  const m = basis();
  if (m === 'all' && row[`${base}_net_slip`] != null) return Number(row[`${base}_net_slip`]);
  if (m !== 'gross' && row[`${base}_net`] != null) return Number(row[`${base}_net`]);
  return row[base] != null ? Number(row[base]) : null;
}
// v2 book rows carry pnl / pnl_net / pnl_net_slip; session baselines gross / net / net_slip.
export const pnlOf = t => pick(t, 'pnl');
export const baseOf = eq => (eq ? Number(eq[{ gross: 'gross', charges: 'net', all: 'net_slip' }[basis()]]) : 0);
