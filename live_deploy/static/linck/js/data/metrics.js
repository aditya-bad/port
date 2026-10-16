// Pure analytics over closed trades and daily realized P&L. No DOM, no
// fetching: give it numbers, get numbers and words back. Definitions
// follow the design spec's "Data rules":
//   Return/DD: above 3 excellent, 1.5–3 good, 0.8–1.5 fair, below weak.
//   Sharpe/Sortino: daily returns on capital, annualised with 252 days.
//   Calmar: yearly return ÷ worst drawdown.

import { ist, MONTHS, WEEKDAYS, fromDay, dayKey, inr, inrS, pct, dFull } from '../core/format.js';

export function verdictRdd(r) {
  if (!Number.isFinite(r)) return '';
  return r > 3 ? 'excellent' : r >= 1.5 ? 'good' : r >= 0.8 ? 'fair' : r >= 0 ? 'weak' : 'losing';
}
export function verdictPf(pf) {
  if (!Number.isFinite(pf)) return '';
  return pf >= 2 ? 'strong edge' : pf >= 1.3 ? 'healthy edge' : pf >= 1 ? 'thin edge' : 'losing';
}
export const fmtRatio = (r, dp = 2) => (Number.isFinite(r) && r < 99 ? r.toFixed(dp) : '—');

// pnls: closed-trade results, oldest first.
export function tradeMetrics(pnls) {
  const n = pnls.length;
  const wins = pnls.filter(p => p > 0), losses = pnls.filter(p => p <= 0);
  const gw = wins.reduce((a, b) => a + b, 0), gl = -losses.reduce((a, b) => a + b, 0);
  let streak = 0;
  for (let i = n - 1; i >= 0 && pnls[i] <= 0; i--) streak++;
  return {
    n, pnl: gw - gl, win: n ? wins.length / n * 100 : 0,
    pf: gl > 0 ? gw / gl : gw > 0 ? 99 : 0,
    aw: wins.length ? gw / wins.length : 0, al: losses.length ? -gl / losses.length : 0,
    best: n ? Math.max(...pnls) : 0, worst: n ? Math.min(...pnls) : 0, streak,
  };
}

// Equity path from a list of day P&Ls → drawdown series and the worst dip.
export function drawdown(values) {
  let peak = 0, cum = 0, worst = 0;
  const cumul = [], dd = [];
  for (const v of values) {
    cum += v; peak = Math.max(peak, cum);
    cumul.push(cum); dd.push(cum - peak); worst = Math.min(worst, cum - peak);
  }
  return { cumul, dd, worst };
}
// Drawdown of an already-cumulative series.
export function ddSeries(cum) {
  let peak = -Infinity;
  return cum.map(v => { peak = Math.max(peak, v); return v - peak; });
}

export function periodStart(p, now = new Date()) {
  const t = ist(now);
  if (p === 'month') return new Date(`${t.y}-${String(t.mo + 1).padStart(2, '0')}-01T00:00:00+05:30`);
  if (p === 'year') return new Date(`${t.y}-01-01T00:00:00+05:30`);
  return new Date(0);
}

// Everything the leaderboard and compare need for one deployment over a
// period. trades: v2 trades (any basis via pnlOf); daily: v2 daily rows.
export function periodMetrics({ trades, daily, cap, pnlOf, period = 'all' }) {
  const from = periodStart(period);
  const T = trades.filter(t => new Date(t.closed_at) >= from);
  const D = daily.filter(r => fromDay(r.day) >= from);
  const m = tradeMetrics(T.map(pnlOf));
  const dayVals = D.map(pnlOf);
  const pnl = dayVals.reduce((a, b) => a + b, 0);
  const { worst } = drawdown(dayVals);
  const ret = cap ? pnl / cap * 100 : 0, ddp = cap ? worst / cap * 100 : 0;
  const months = {};
  D.forEach(r => { const k = r.day.slice(0, 7); months[k] = (months[k] || 0) + pnlOf(r); });
  const mv = Object.values(months).filter(x => x !== 0);
  return {
    ...m, pnl, ret, dd: worst, ddp, rdd: ddp < 0 ? ret / -ddp : pnl > 0 ? 99 : 0,
    cons: mv.length ? mv.filter(x => x > 0).length / mv.length * 100 : 0, months: mv.length,
  };
}

// Weekdays from a to b inclusive (IST dates), as "YYYY-MM-DD".
export function weekdays(a, b) {
  const out = [];
  let d = fromDay(dayKey(a));
  const end = fromDay(dayKey(b));
  while (d <= end) {
    const wd = ist(d).wd;
    if (wd > 0 && wd < 6) out.push(dayKey(d));
    d = new Date(d.getTime() + 864e5);
  }
  return out;
}

// Daily rows → one entry per weekday in the running window (0 when
// nothing closed), each with its running total.
export function dailySeries(rows, pnlOf, from, to = new Date()) {
  const map = {};
  rows.forEach(r => { map[r.day] = (map[r.day] || 0) + pnlOf(r); });
  const first = rows.length ? rows.map(r => r.day).sort()[0] : null;
  const start = from || (first ? fromDay(first) : to);
  let cum = 0;
  return weekdays(start, to).map(k => {
    const has = k in map;
    cum += has ? map[k] : 0;
    return { key: k, date: fromDay(k), day: has ? map[k] : 0, has, cum };
  });
}

const mix = (v, a) => `color-mix(in oklch, ${v > 0 ? 'var(--pos)' : 'var(--r)'} ${Math.round(a)}%, var(--paper))`;
function heat(v, mx) {
  if (!v) return ['var(--soft)', 'var(--mute)'];
  const a = Math.min(1, Math.abs(v) / (mx || 1));
  return [mix(v, 16 + a * 74), a > .6 ? 'var(--paper)' : 'var(--ink)'];
}
export const heatLegend = () => [-95, -72, -50, -28, 0, 28, 50, 72, 95].map(a => (a ? mix(a, Math.abs(a)) : 'var(--soft)'));
const tone = v => (v > 0 ? 'var(--g)' : v < 0 ? 'var(--r)' : 'var(--mute)');

// The full performance stack shared by a deployment's Performance tab
// and Reports: key numbers, monthly heatmap, trading calendar,
// distribution, rolling win rate and written insights.
//   days:   dailySeries(...) output
//   trades: [{ date: Date, pnl, reason }] oldest first
//   opts:   { cap, pctMode: bool, calRange: 'last'|year }
export function analytics(days, trades, { cap, pctMode = false, calRange = 'last' } = {}) {
  const fv = v => (pctMode ? pct(v / cap * 100, 1) : inrS(v));
  const mm = {}, dmap = {}, dn = {};
  days.forEach(q => { if (!q.has) return; const p = ist(q.date), k = p.y * 12 + p.mo; mm[k] = (mm[k] || 0) + q.day; dmap[q.key] = q.day; });
  days.forEach(q => { if (!(q.key in dmap)) dmap[q.key] = undefined; });
  trades.forEach(t => { const k = dayKey(t.date); dn[k] = (dn[k] || 0) + 1; });
  const running = new Set(days.map(q => q.key));
  const ys = [...new Set(days.map(q => ist(q.date).y))].sort((a, b) => a - b);
  const mmx = Math.max(1, ...Object.values(mm).map(Math.abs));

  // Monthly returns
  let cum = 0;
  const yRows = ys.map(y => {
    let tot = 0, any = false;
    const cells = MONTHS.map((l, mo) => {
      const v = mm[y * 12 + mo];
      const runningMonth = days.some(q => { const p = ist(q.date); return p.y === y && p.mo === mo; });
      if (v === undefined && !runningMonth) return { v: '', bg: 'transparent', fg: 'var(--mute)', tip: `${l} ${y}\nnot running` };
      const x = v || 0; tot += x; cum += x; any = true;
      const [bg, fg] = heat(x, mmx);
      return { v: x ? fv(x) : '0', bg, fg, tip: `${l} ${y}\nP&L  ${inr(x)}\nOf capital  ${pct(x / cap * 100, 2)}\nRunning total  ${inr(cum)}` };
    });
    const [tbg, tfg] = heat(tot, mmx * 3);
    return { y: String(y), cells, tot: any ? fv(tot) : '', tbg, tfg, cum: fv(cum), cc: tone(cum) };
  });
  const avg = MONTHS.map((l, mo) => {
    const L = ys.map(y => mm[y * 12 + mo]).filter(v => v !== undefined);
    const a = L.length ? L.reduce((x, y) => x + y, 0) / L.length : null;
    return { v: a === null ? '' : fv(a), fg: a === null ? 'var(--mute)' : tone(a), tip: `${l}, all years\nAverage  ${a === null ? '—' : inr(a)}\nYears  ${L.length}` };
  });
  const mList = Object.entries(mm).map(([k, v]) => ({ k: +k, v }));
  const bm = mList.reduce((a, x) => (x.v > a.v ? x : a), mList[0] || { k: 0, v: 0 });
  const wm = mList.reduce((a, x) => (x.v < a.v ? x : a), mList[0] || { k: 0, v: 0 });
  const mName = k => `${MONTHS[k % 12]} ${Math.floor(k / 12)}`;
  const gm = mList.filter(x => x.v > 0).length;
  const hNote = mList.length
    ? `Best month ${mName(bm.k)} at ${inr(bm.v)}, worst ${mName(wm.k)} at ${inr(wm.v)}. ${gm} of ${mList.length} months ended green.`
    : 'No closed trades yet.';

  // Trading calendar: weekdays as rows, weeks as columns, 4 steps each way
  // scaled to the 90th-percentile day.
  const now = new Date(), tp = ist(now);
  const r0 = calRange === 'last' ? new Date(now.getTime() - 364 * 864e5) : new Date(`${calRange}-01-01T12:00:00+05:30`);
  const r1 = calRange === 'last' ? now : new Date(Math.min(now.getTime(), new Date(`${calRange}-12-31T12:00:00+05:30`).getTime()));
  const dv = Object.values(dmap).filter(Boolean).map(Math.abs).sort((a, b) => a - b);
  const dmx = dv.length ? dv[Math.floor(dv.length * 0.9)] || dv[dv.length - 1] : 1;
  const weeks = [];
  const inR = [];
  let w = fromDay(dayKey(r0));
  w = new Date(w.getTime() - ((ist(w).wd + 6) % 7) * 864e5);
  let prevM = -1;
  const k0 = dayKey(r0), k1 = dayKey(r1);
  while (dayKey(w) <= k1) {
    const cells = [];
    for (let i = 0; i < 5; i++) {
      const dt = new Date(w.getTime() + i * 864e5), k = dayKey(dt), p = ist(dt);
      let bg = 'transparent', bd = '0', tip = '';
      if (k >= k0 && k <= k1) {
        tip = `${WEEKDAYS[p.wd]} ${p.d} ${MONTHS[p.mo]} ${p.y}\n`;
        const v = dmap[k];
        if (!running.has(k)) { bd = '1px solid var(--line)'; tip += 'not running'; }
        else if (!v) { bg = 'var(--soft)'; tip += 'no realized P&L'; inR.push({ dt, v: 0 }); }
        else {
          const lv = Math.max(1, Math.min(4, Math.ceil(Math.abs(v) / dmx * 4)));
          bg = mix(v, [0, 28, 50, 72, 95][lv]);
          tip += `P&L  ${inr(v)}\nOf capital  ${pct(v / cap * 100, 2)}${dn[k] ? `\nTrades closed  ${dn[k]}` : ''}`;
          inR.push({ dt, v });
        }
      }
      cells.push({ bg, bd, tip });
    }
    const p = ist(w);
    const lab = p.mo !== prevM && (p.d <= 7 || prevM === -1) ? MONTHS[p.mo] : '';
    if (lab) prevM = p.mo;
    weeks.push({ m: lab, cells });
    w = new Date(w.getTime() + 7 * 864e5);
  }
  let sg = 0, sr = 0, cg = 0, cr = 0;
  inR.forEach(x => { if (x.v > 0) { cg++; cr = 0; } else if (x.v < 0) { cr++; cg = 0; } sg = Math.max(sg, cg); sr = Math.max(sr, cr); });
  const bd2 = inR.reduce((a, x) => (x.v > a.v ? x : a), { v: 0, dt: null });
  const wd2 = inR.reduce((a, x) => (x.v < a.v ? x : a), { v: 0, dt: null });
  const fdd = dt => (dt ? ` · ${ist(dt).d} ${MONTHS[ist(dt).mo]}` : '');
  const calStats = [
    ['Green days', String(inR.filter(x => x.v > 0).length), 'var(--g)'],
    ['Red days', String(inR.filter(x => x.v < 0).length), 'var(--r)'],
    ['Longest green run', `${sg}d`, 'var(--ink)'],
    ['Longest red run', `${sr}d`, 'var(--ink)'],
    ['Best day', inrS(bd2.v) + fdd(bd2.dt), 'var(--g)'],
    ['Worst day', inrS(wd2.v) + fdd(wd2.dt), 'var(--r)'],
  ].map(([l, v, c]) => ({ l, v, c }));
  const yOpts = ['last', ...ys.slice().reverse().map(String)].slice(0, 4);

  // Distribution
  const tpl = trades.map(t => t.pnl);
  const lo = Math.min(0, ...tpl), hi = Math.max(0, ...tpl), NB = 18, bw = (hi - lo) / NB || 1, bins = Array(NB).fill(0);
  tpl.forEach(p => { bins[Math.min(NB - 1, Math.floor((p - lo) / bw))]++; });
  const bmx = Math.max(1, ...bins);
  const hist = bins.map((n, i) => {
    const c0 = lo + i * bw;
    return { h: n / bmx * 100, pos: c0 + bw / 2 >= 0, tip: `${inr(c0)} to ${inr(c0 + bw)}\nTrades  ${n}\nShare  ${tpl.length ? Math.round(n / tpl.length * 100) : 0}%` };
  });
  const W = tpl.filter(p => p > 0), L = tpl.filter(p => p < 0);
  const exp = tpl.length ? tpl.reduce((a, b) => a + b, 0) / tpl.length : 0;
  const med = tpl.length ? [...tpl].sort((a, b) => a - b)[Math.floor(tpl.length / 2)] : 0;

  // Rolling win rate (window scales with sample size)
  const RW = Math.min(20, Math.max(5, Math.floor(tpl.length / 3)));
  const roll = [];
  for (let i = RW - 1; i < tpl.length; i++) {
    let w2 = 0;
    for (let j = i - RW + 1; j <= i; j++) if (tpl[j] > 0) w2++;
    roll.push(w2 / RW * 100);
  }

  // Risk and quality
  const dr = days.map(q => q.day / cap), nd = dr.length || 1;
  const mean = dr.reduce((a, b) => a + b, 0) / nd;
  const sd = Math.sqrt(dr.reduce((a, b) => a + (b - mean) ** 2, 0) / nd);
  const dsd = Math.sqrt(dr.reduce((a, b) => a + (b < 0 ? b * b : 0), 0) / nd);
  const sharpe = sd ? mean / sd * Math.sqrt(252) : 0, sortino = dsd ? mean / dsd * Math.sqrt(252) : 0;
  let pk = -Infinity, mdd = 0, uw = 0, uwMax = 0, lastPk = 0;
  days.forEach((q, i) => {
    if (q.cum >= pk) { pk = q.cum; uw = 0; lastPk = i; } else { uw++; uwMax = Math.max(uwMax, uw); mdd = Math.min(mdd, q.cum - pk); }
  });
  const total = days.length ? days[days.length - 1].cum : 0;
  const yrs = Math.max(0.08, days.length / 252);
  const annR = total / cap * 100 / yrs;
  const calmar = mdd ? annR / Math.abs(mdd / cap * 100) : 0;
  const recF = mdd ? total / -mdd : 0;
  const gp = W.reduce((a, b) => a + b, 0), top5 = [...W].sort((a, b) => b - a).slice(0, 5).reduce((a, b) => a + b, 0);
  const since = days.length ? days.length - 1 - lastPk : 0;
  const keyNums = [
    ['Expectancy', inr(exp), 'average P&L per trade'],
    ['Median trade', inr(med), 'half of trades did better'],
    ['Sharpe', sharpe.toFixed(2), sharpe >= 2 ? 'very good' : sharpe >= 1 ? 'good' : sharpe > 0 ? 'low' : 'negative'],
    ['Sortino', sortino.toFixed(2), 'only counts downside swings'],
    ['Calmar', calmar.toFixed(2), 'yearly return ÷ worst drawdown'],
    ['Recovery factor', `${recF.toFixed(1)}×`, 'net profit ÷ worst drawdown'],
    ['Longest under water', `${uwMax} days`, 'below a previous high'],
    ['Since last high', since ? `${since} days` : 'at a high', since ? 'trading days' : 'new peak'],
    ['Annualised return', pct(annR, 1), 'on capital, simple'],
    ['Top 5 trades', gp ? `${Math.round(top5 / gp * 100)}%` : '—', 'share of gross profit'],
  ].map(([l, v, s]) => ({ l, v, s }));

  // Written insights
  const ins = [];
  if (mList.length) ins.push(['var(--g)', `${mName(bm.k)} was the best month (${inr(bm.v)}) and ${mName(wm.k)} the worst (${inr(wm.v)}).`]);
  if (gp) ins.push(['var(--b)', `The 5 biggest wins made ${Math.round(top5 / gp * 100)}% of gross profit${top5 / gp > 0.5 ? ', so results lean on a few big trades.' : ', so profit is spread across many trades.'}`]);
  if (W.length && L.length) {
    const aw = gp / W.length, al = -L.reduce((a, b) => a + b, 0) / L.length;
    ins.push(['var(--ink)', `An average win is ${(aw / al).toFixed(2)}× an average loss, and ${Math.round(W.length / tpl.length * 100)}% of trades win.`]);
  }
  if (roll.length > 2) {
    const nowR = roll[roll.length - 1], was = roll.reduce((a, b) => a + b, 0) / roll.length;
    ins.push([nowR >= was ? 'var(--g)' : 'var(--wr)', `Win rate over the last ${RW} trades is ${nowR.toFixed(0)}%, ${Math.abs(nowR - was) < 3 ? 'in line with' : nowR > was ? 'above' : 'below'} the ${was.toFixed(0)}% average.`]);
  }
  if (days.length) ins.push([since ? 'var(--wr)' : 'var(--g)', `${since ? `${since} trading days since the last equity high.` : 'Equity is at a new high.'} The longest stretch under water was ${uwMax} days.`]);
  if (sr >= 3) ins.push(['var(--r)', `The longest losing run in the calendar range was ${sr} trading days in a row.`]);

  return {
    yRows, avg, hNote, weeks, calStats, yOpts, legend: heatLegend(),
    hist, hx0: inr(lo), hx1: inr(hi), histSub: `${tpl.length} trades · expectancy ${inr(exp)}`,
    roll, rollW: RW, rollNow: roll.length ? `${roll[roll.length - 1].toFixed(0)}%` : '—', rollTrades: trades,
    keyNums, insights: ins.map(([c, t]) => ({ c, t })),
  };
}

// Pearson correlation of two day→pnl maps over days either traded.
export function correlation(a, b, minDays = 8) {
  const ks = Object.keys(a).filter(k => k in b && (a[k] || b[k]));
  if (ks.length < minDays) return null;
  const A = ks.map(k => a[k]), B = ks.map(k => b[k]);
  const ma = A.reduce((x, y) => x + y, 0) / ks.length, mb = B.reduce((x, y) => x + y, 0) / ks.length;
  let sab = 0, sa = 0, sb = 0;
  ks.forEach((_, i) => { const da = A[i] - ma, db = B[i] - mb; sab += da * db; sa += da * da; sb += db * db; });
  return sa && sb ? sab / Math.sqrt(sa * sb) : 0;
}

export const describeDay = d => dFull(d);
