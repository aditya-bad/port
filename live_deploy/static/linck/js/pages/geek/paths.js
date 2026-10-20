// Cycle P&L paths ([hours since it opened, ₹], from the cycles view) as
// numbers for the Cycles page: one cycle's P&L at an hour, a calmer copy
// of its path, what past cycles did hour by hour, and the same day by day
// with the running cycle placed against them. Pure: no DOM.

export const hoursOf = r => (r.path && r.path.length ? r.path[r.path.length - 1][0] : 0);
const hasPath = r => r.path && r.path.length > 1;

// Its P&L at hour h (the last point at or before it); null once it had
// ended, so a short cycle doesn't count on later days.
export function atHour(r, h) {
  if (!hasPath(r) || h > hoursOf(r)) return null;
  let v = null;
  for (const p of r.path) { if (p[0] <= h) v = p[1]; else break; }
  return v;
}

// One point per `every` hours (the last in each), keeping the first and
// last: the 5-minute jitter goes, the shape stays.
export function smooth(path, every = 2) {
  const out = [];
  let b = null;
  path.forEach((p, i) => {
    const k = Math.floor(p[0] / every);
    if (i === 0 || k !== b) { out.push(p); b = k; } else out[out.length - 1] = p;
  });
  if (path.length && out[0] !== path[0]) out.unshift(path[0]);
  return out;
}

// Linear-interpolated quantile of a sorted array.
export function quantile(a, f) {
  if (!a.length) return null;
  const pos = f * (a.length - 1), lo = Math.floor(pos), hi = Math.ceil(pos);
  return a[lo] + (a[hi] - a[lo]) * (pos - lo);
}

const pastOf = rows => rows.filter(r => r.status !== 'open' && hasPath(r));

// Past (closed) cycles hour by hour: how many were still open, and their
// typical (median), middle half and best to worst. Stops where fewer than
// `min` were still open: a "typical" of one or two cycles isn't one.
export function band(rows, stepH = 2, min = 3) {
  const past = pastOf(rows), end = Math.max(0, ...past.map(hoursOf)), out = [];
  for (let h = 0; h <= end; h += stepH) {
    const v = past.map(r => atHour(r, h)).filter(x => x != null).sort((a, b) => a - b);
    if (v.length < min) break;
    out.push({ h, n: v.length, lo: v[0], q1: quantile(v, 0.25), med: quantile(v, 0.5), q3: quantile(v, 0.75), hi: v[v.length - 1] });
  }
  return out;
}

// Where a value sits among past cycles on the same day.
export function place(v, s) {
  if (v == null || s.med == null) return '';
  if (v > s.hi) return 'above every past cycle';
  if (v < s.lo) return 'below every past cycle';
  if (v > s.q3) return 'top quarter';
  if (v < s.q1) return 'bottom quarter';
  return 'middle half';
}

// The consolidated table: one row per day since opening (P&L at the end
// of that day's 24 hours), plus a "now" row for the running cycle's age.
export function dayTable(rows, min = 3) {
  const past = pastOf(rows), run = rows.find(r => r.status === 'open' && hasPath(r));
  const lastDay = Math.ceil(Math.max(0, ...past.map(hoursOf), run ? hoursOf(run) : 0) / 24);
  const at = (h, label, now = false) => {
    const v = past.map(r => atHour(r, h)).filter(x => x != null).sort((a, b) => a - b);
    const enough = v.length >= min;
    const s = { med: enough ? quantile(v, 0.5) : null, q1: enough ? quantile(v, 0.25) : null, q3: enough ? quantile(v, 0.75) : null,
                lo: v.length ? v[0] : null, hi: v.length ? v[v.length - 1] : null };
    const reached = past.filter(r => r.ended_by === 'checkpoint_target' && hoursOf(r) <= h).length;
    const runV = run && hoursOf(run) >= h ? atHour(run, h) : null;
    return { day: h / 24, label, now, open: v.length, ...s, reached, closed: past.length, run: runV, place: place(runV, s) };
  };
  const out = [];
  for (let d = 1; d <= lastDay; d++) out.push(at(d * 24, `Day ${d}`));
  if (run) out.push(at(hoursOf(run), `Now · day ${(hoursOf(run) / 24).toFixed(1)}`, true));
  return out.filter(r => r.open || r.run != null || r.now).sort((a, b) => a.day - b.day || (a.now ? 1 : -1));
}
