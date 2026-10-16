// Static market facts. Edit here, nowhere else.

// Index strip, in order. token = Kite instrument_token of the index.
export const INDICES = [
  { n: 'NIFTY', token: 256265, symbol: 'NIFTY 50' },
  { n: 'BANKNIFTY', token: 260105, symbol: 'NIFTY BANK' },
  { n: 'SENSEX', token: 265, symbol: 'SENSEX' },
  { n: 'BANKEX', token: 274441, symbol: 'BANKEX' },
];
export const indexByName = n => INDICES.find(i => i.n === n);

export const LAB_TAG = 'Strategy Lab';   // built-in tag: experiments, hidden by default
export const YOUNG_DAYS = 14;             // under this, listed but never ranked
export const QUIET_DAYS = 10;             // an active deployment with no trade this long is "quiet"
export const STREAK_ALERT = 3;            // losing trades in a row that raise an alert
export const DESK_LANES = 12;             // lanes shown before "Show all"
