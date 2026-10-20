// Geek mode's view menu: the shared views every deployment has, and the
// views of a strategy's own. Pure (the views load lazily), so it's tested
// on its own.

// [key, label, group, shortcut, load, reused deployment tab?, hidden from the menu?]
export const BASICS = [
  ['overview', 'Overview', 'Basics', '1', () => import('./overview.js')],
  ['positions', 'Positions', 'Basics', '2', () => import('../deployment/now.js'), true],
  ['trades', 'Trades & replay', 'Basics', '3', () => import('../deployment/trades.js'), true],
  ['performance', 'Performance', 'Basics', '4', () => import('../deployment/performance.js'), true],
  ['engine', 'Engine', 'Live', 'E', () => import('./engine.js')],
  ['decisions', 'Decisions', 'Live', 'D', () => import('./decisions.js')],
];
const SETTINGS = ['settings', 'Settings at work', 'Exposure', 'S', () => import('./settings.js')];
// Views of a strategy's own, between Live and Exposure.
const OWN = {
  strangle_monthly_v2: [
    ['map', 'Cycle map', 'Anatomy', 'M', () => import('../deployment/days.js'), true],
    ['cycles', 'Cycles', 'Anatomy', 'C', () => import('./cycles.js')],
    ['cycle', 'Cycle', 'Anatomy', '', () => import('./cycle.js'), false, true],   // one cycle, from Cycles or Adjustments
    ['adjustments', 'Adjustments', 'Anatomy', 'A', () => import('./adjustments.js')],
    ['premium', 'Premium & decay', 'Anatomy', 'P', () => import('./premium.js')],
    ['entries', 'Entries', 'Anatomy', 'N', () => import('./entries.js')],
  ],
};
export const viewsFor = strategy => [...BASICS, ...(OWN[strategy] || []), SETTINGS];
export const GROUPS = ['Basics', 'Live', 'Anatomy', 'Exposure'];
