// One strategy across its deployments, for the strategy page. Pure: no
// DOM, no store, so it's tested on its own.
//
// The selection is kept as the deployments left OUT: every deployment is
// in by default, including ones created later.

import { periodMetrics } from './metrics.js';

export const deploymentsOf = (all, name) => all.filter(v => v.strategy === name);

// The deployments in view: every one not left out.
export const selected = (deps, off = []) => deps.filter(v => !off.includes(v.id));

// Quick selections: [key, label, ids left out].
export function presets(deps) {
  const out = id => deps.filter(v => !id(v)).map(v => v.id);
  return [
    ['all', 'All', []],
    ['active', 'Running', out(v => v.rawStatus === 'active')],
    ['live', 'Not stopped', out(v => v.rawStatus !== 'stopped')],
  ].filter(([k, , o], i, a) => k === 'all' || (o.length && o.length < deps.length && a.findIndex(x => x[2].join() === o.join()) === i));
}
export const presetOf = (deps, off) => (presets(deps).find(([, , o]) => o.length === off.length && o.every(id => off.includes(id))) || [null])[0];

// Toggle one deployment in or out (never all out: the last one stays).
export function toggleOff(deps, off, id) {
  if (off.includes(id)) return off.filter(x => x !== id);
  const next = [...off, id];
  return selected(deps, next).length ? next : off;
}

// Each deployment's all-time figures, for the table.
export function deploymentRows(deps, pnlOf, live) {
  return deps.map(v => {
    const m = periodMetrics({ trades: v.trades, daily: v.daily, cap: v.cap, pnlOf, period: 'all' });
    const x = live(v);
    return { v, m, x };
  });
}

// Every setting of the strategy: its default, and what the deployments in
// view use. `differs` when any of them doesn't use the default.
export function settingsSpread(fields, deps) {
  const show = x => (x == null ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x));
  return (fields || []).map(f => {
    const vals = new Map();
    for (const v of deps) {
      const cfg = (v.raw && v.raw.config) || {};
      const val = f.key in cfg ? cfg[f.key] : f.default;
      const k = show(val);
      if (!vals.has(k)) vals.set(k, { v: k, n: 0, names: [] });
      const e = vals.get(k); e.n++; e.names.push(v.name);
    }
    const values = [...vals.values()].sort((a, b) => b.n - a.n);
    const def = show(f.default);
    return { key: f.key, label: f.label, help: f.help, prefix: f.prefix || '', suffix: f.suffix || '', def, values,
      differs: values.some(x => x.v !== def), mixed: values.length > 1 };
  });
}
