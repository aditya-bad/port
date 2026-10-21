// Geek mode's view menu: the shared views every deployment has, and the
// views of a strategy's own. Pure (the views load lazily), so it's tested
// on its own.

// [key, label, group, shortcut, load, reused deployment tab?, hidden: the menu entry it sits under]
export const BASICS = [
  ['overview', 'Overview', 'Basics', '1', () => import('./overview.js')],
  ['positions', 'Positions', 'Basics', '2', () => import('../deployment/now.js'), true],
  ['trades', 'Trades & replay', 'Basics', '3', () => import('../deployment/trades.js'), true],
  ['performance', 'Performance', 'Basics', '4', () => import('../deployment/performance.js'), true],
  ['engine', 'Engine', 'Live', 'E', () => import('./engine.js')],
  ['decisions', 'Decisions', 'Live', 'D', () => import('./decisions.js')],
];
const SETTINGS = ['settings', 'Settings at work', 'Exposure', 'S', () => import('./settings.js')];
// The DTT straddles that adjust (and the one that rolls) share their views.
const ADJUSTED = [
  ['sessions', 'Sessions', 'Anatomy', 'I', () => import('./adjsessions.js')],
  ['session', 'Session', 'Anatomy', '', () => import('./adjsession.js'), false, 'sessions'],   // one day, from Sessions or Adjustments
  ['moves', 'Adjustments', 'Anatomy', 'A', () => import('./adjmoves.js')],
];
// Views of a strategy's own, between Live and Exposure.
const OWN = {
  strangle_monthly_v2: [
    ['map', 'Cycle map', 'Anatomy', 'M', () => import('../deployment/days.js'), true],
    ['cycles', 'Cycles', 'Anatomy', 'C', () => import('./cycles.js')],
    ['cycle', 'Cycle', 'Anatomy', '', () => import('./cycle.js'), false, 'cycles'],   // one cycle, from Cycles or Adjustments
    ['adjustments', 'Adjustments', 'Anatomy', 'A', () => import('./adjustments.js')],
    ['premium', 'Premium & decay', 'Anatomy', 'P', () => import('./premium.js')],
    ['entries', 'Entries', 'Anatomy', 'N', () => import('./entries.js')],
  ],
  pivot_supertrend_options: [
    ['signals', 'Signals', 'Anatomy', 'I', () => import('./signals.js')],
    ['trade', 'Trade', 'Anatomy', '', () => import('./trade.js'), false, 'signals'],   // one trade, from Signals or Exits
    ['exits', 'Exits', 'Anatomy', 'X', () => import('./exits.js')],
    ['days', 'Days', 'Anatomy', 'Y', () => import('./days.js')],
  ],
  intraday_dtt_simple: [
    ['straddles', 'Straddles', 'Anatomy', 'I', () => import('./straddles.js')],
    ['straddle', 'Straddle', 'Anatomy', '', () => import('./straddle.js'), false, 'straddles'],   // one straddle, from Straddles or Exits
    ['timing', 'Exits & timing', 'Anatomy', 'X', () => import('./timing.js')],
  ],
  intraday_dtt_adjusted: ADJUSTED,
  intraday_dtt_advanced: ADJUSTED,
};
export const viewsFor = strategy => [...BASICS, ...(OWN[strategy] || []), SETTINGS];
export const hasView = (strategy, key) => viewsFor(strategy).some(x => x[0] === key);
export const GROUPS = ['Basics', 'Live', 'Anatomy', 'Exposure'];
