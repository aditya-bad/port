// The kinds of alert a deployment raises, and which you can turn off.
// The same list as app/alert_types.py (tests/test_alert_types.py keeps
// them the same). Each deployment keeps the kinds it doesn't want in
// raw.alerts_off; one turned off isn't raised anywhere: not on the Desk,
// not as a toast, not pushed.

export const GROUPS = [
  ['Trading', [
    ['trades', 'Entries and exits', 'A toast and a push each time it enters or exits'],
    ['paused', 'Paused with open legs', 'On the Desk while it’s paused holding positions'],
    ['streak', 'Losing streak', 'On the Desk after several losing trades in a row'],
  ]],
  ['How it’s doing', [
    ['win_rate', 'Winning less often', 'Its recent win rate well under its usual one'],
    ['loss_size', 'Losing more per loss', 'Its recent losses well bigger than usual'],
    ['worst_loss', 'Worst trade yet', 'A trade lost more than any before it'],
    ['slippage', 'Paying more in slippage', 'Slippage per trade doubled'],
    ['drawdown', 'Deepest drawdown yet', 'Further below its best than it has ever been'],
    ['fewer_trades', 'Trading less than usual', 'Far fewer trades than usual in the last 10 market days'],
  ]],
  ['Market and orders', [
    ['vix', 'VIX spike', 'VIX up 10% or more in a day while it holds sold premium'],
    ['shadow', 'Kite would have refused', 'Shadow mode: an order the real Kite would have rejected'],
  ]],
  ['Operations', [
    ['stale_legs', 'Legs not traded today', 'Open legs with no trade today, after 09:45'],
    ['strategy_warning', 'Strategy warnings', 'Non-critical problems the strategy reports about itself'],
  ]],
];

export const KEYS = GROUPS.flatMap(([, ts]) => ts.map(t => t[0]));

// Missing these can lose money unwatched, so they always alert.
export const ALWAYS_ON = [
  ['Isn’t running', 'Marked active, but nothing runs its strategy'],
  ['Errored', 'Its strategy stopped with an error'],
  ['Not acting on prices', 'Running, prices flow, but it hasn’t acted on one in 3 minutes'],
  ['Strategy errors', 'Its strategy raised errors in the last 5 minutes'],
  ['Critical problems it reports', 'A failed fill, legs it lost track of, an entry it couldn’t make'],
  ['Day’s-end rules', 'An intraday one holding legs after its cut-off, a leg past expiry'],
];

export const offOf = v => (v && v.raw && v.raw.alerts_off) || [];
export const isOn = (v, kind) => !offOf(v).includes(kind);

// alerts_off with `kind` switched to `on`, in the catalogue's order.
export function switched(v, kind, on) {
  const off = new Set(offOf(v));
  if (on) off.delete(kind); else off.add(kind);
  return KEYS.filter(k => off.has(k));
}
