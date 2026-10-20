// Compare groups: several deployments combined into one, compared like a
// single deployment. Combined means one portfolio: daily P&L added up day
// by day (every basis), trades pooled, capital added up, started when the
// first one did. So its return and drawdown are on the total capital, and
// its drawdown is the portfolio's own (members that offset each other
// show it), not an average of theirs.
//
// An item is { ids: [deployment ids] }; one id is that deployment itself.
// In a link, items are comma-separated and a group's ids joined by "."
// (compare?ids=a,b.c.d compares a with b+c+d).

export const keyOf = ids => [...ids].sort().join('.');
export const encode = items => items.map(it => keyOf(it.ids)).join(',');
export const decode = s => (s || '').split(',').filter(Boolean).map(k => ({ ids: [...new Set(k.split('.').filter(Boolean))] }));

// Sum every numeric field of the day rows that share a day.
function addDays(rows) {
  const by = new Map();
  for (const r of rows) {
    const o = by.get(r.day) || { day: r.day };
    for (const [k, v] of Object.entries(r)) {
      if (typeof v === 'number') o[k] = (o[k] || 0) + v;   // pnl, pnl_net, pnl_net_slip, closes
    }
    by.set(r.day, o);
  }
  return [...by.values()].sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
}

// A short name: "All Monthly strangle (3)", "Monthly strangle: A + B", "Monthly strangle ×3", "3 deployments".
export function groupName(vs, allOfStrategy = () => false, label = s => s) {
  const strats = [...new Set(vs.map(v => v.strategy))];
  if (strats.length === 1) {
    const l = label(strats[0]);
    if (allOfStrategy(strats[0], vs.length)) return `All ${l} (${vs.length})`;
    return vs.length === 2 ? `${l}: ${vs[0].name} + ${vs[1].name}` : `${l} ×${vs.length}`;
  }
  return vs.length === 2 ? `${vs[0].name} + ${vs[1].name}` : `${vs.length} deployments`;
}

// One deployment-shaped record for a group (what Compare and the metrics read).
export function combine(vs, name) {
  if (vs.length === 1) return { ...vs[0], key: vs[0].id, members: vs, isGroup: false };
  const strats = [...new Set(vs.map(v => v.strategy))];
  return {
    key: keyOf(vs.map(v => v.id)), id: keyOf(vs.map(v => v.id)), name, isGroup: true, members: vs,
    strategy: strats.length === 1 ? strats[0] : 'mixed', strategyLabel: strats.length === 1 ? vs[0].strategyLabel : 'mixed',
    mode: [...new Set(vs.map(v => v.mode))].join(' + '), modeKey: [...new Set(vs.map(v => v.modeKey))].join('+'),
    cap: vs.reduce((a, v) => a + v.cap, 0),
    created: new Date(Math.min(...vs.map(v => +v.created))),
    trades: vs.flatMap(v => v.trades).sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at)),
    daily: addDays(vs.flatMap(v => v.daily)),
  };
}

// Items → records, dropping ids that no longer exist (and items left empty).
export function resolve(items, all, opts = {}) {
  const byId = new Map(all.map(v => [v.id, v]));
  return items.map(it => it.ids.map(id => byId.get(id)).filter(Boolean)).filter(vs => vs.length)
    .map(vs => combine(vs, groupName(vs, opts.allOfStrategy, opts.label)));
}
