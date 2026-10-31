// Variations (app/variations.py), as the pages need them: a family side by
// side, their P&L over each other, and the settings that differ. Pure, for
// tests; the family comes from data/book.js.

import { periodMetrics, tradeMetrics } from './metrics.js';

export const MAX = 10;
export const COLORS = ['var(--ink)', 'var(--wr)', 'var(--b)', 'var(--c4)', 'var(--c5)'];
// The primary in ink; variations in the next hues, then dashed: never a colour alone (the table names each).
export const styleOf = i => ({ color: COLORS[i % COLORS.length], dash: i >= COLORS.length ? '6 4' : '' });

// Each one's figures, all-time.
export function rows(family, pnlOf, live) {
  return family.map((v, i) => {
    const m = periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period: 'all' });
    const t = tradeMetrics(v.trades.map(pnlOf));
    const x = live ? live(v) : { overall: m.pnl, cycle: null };
    return { v, i, primary: i === 0, label: i === 0 ? 'Primary' : v.variationLabel || v.name, overall: x.overall, cycle: x.cycle,
      ret: v.cap ? x.overall / v.cap * 100 : 0, trades: t.n, win: t.n ? t.win : null, dd: m.ddp, worst: t.n ? t.worst : null };
  });
}

// Cumulative realized P&L of each, on one time axis: the days any of them
// closed a trade, from the first. [{ days: [ms], series: [[cum]] }].
export function curves(family, pnlOf) {
  const day = t => { const d = new Date(t.closed_at); return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()); };
  const days = [...new Set(family.flatMap(v => v.trades.map(day)))].sort((a, b) => a - b);
  const series = family.map(v => {
    const by = new Map();
    v.trades.forEach(t => by.set(day(t), (by.get(day(t)) || 0) + pnlOf(t)));
    let c = 0;
    return days.map(d => (c += by.get(d) || 0));
  });
  return { days, series };
}

// The settings that differ from the primary's, per variation:
// [{ key, primary, values: [value per variation] }], keys any of them changed.
// A key a deployment doesn't set runs on the strategy's default (defaults).
export function differences(family, defaults = {}) {
  const [p, ...vs] = family;
  const eff = v => ({ ...defaults, ...((v && v.raw.config) || {}) });
  const pc = eff(p), cs = vs.map(eff);
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const keys = [...new Set([...Object.keys(pc), ...cs.flatMap(c => Object.keys(c))])].filter(k => cs.some(c => !same(c[k], pc[k])));
  return keys.sort().map(key => ({ key, primary: pc[key], values: cs.map(c => c[key]) }));
}

export const show = v => (v === undefined || v === null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
