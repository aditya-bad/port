// SVG charts for the Desk. Real pixel coordinates (redrawn on resize),
// thin marks, recessive grid, one crosshair/tooltip per chart. P&L is
// diverging: green above zero, red below — and every hover readout says
// the number, so colour is never the only carrier.
import { esc, money, moneyCompact, num, date, weekday, time, tone } from './util.js';
import { tip } from './ui.js';

function observe(el, draw) {
  draw();
  let w = el.clientWidth;
  const ro = new ResizeObserver(() => {
    if (Math.abs(el.clientWidth - w) > 2) { w = el.clientWidth; draw(); }
  });
  ro.observe(el);
  return () => ro.disconnect();
}
const svgOpen = (w, h, label) => `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">`;

// Compact (₹1.2L) unless that would repeat a label on a narrow range.
function axisFmt(ticks) {
  const labels = ticks.map(moneyCompact);
  return new Set(labels).size === labels.length ? moneyCompact : v => money(v, { dp: 0 });
}
function niceTicks(min, max, n = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min, step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || step0;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

// ── Daily P&L bars ─────────────────────────────────────────────────
// data: [{ t: ms|iso, value, label?, detail? }] oldest → newest
export function bars(el, data, { height = 200, label = 'Daily P&L', onSelect } = {}) {
  const draw = () => {
    if (!data.length) { el.innerHTML = '<div class="empty">No closed trades in this range yet.</div>'; return; }
    const W = Math.max(280, el.clientWidth), H = height;
    const M = { l: 58, r: 8, t: 10, b: 24 };
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    const vals = data.map(d => d.value || 0);
    const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
    const lo = ticks[0], hi = ticks[ticks.length - 1];
    const y = v => M.t + ph - ((v - lo) / (hi - lo || 1)) * ph;
    const fmtAxis = axisFmt(ticks);
    const slot = pw / data.length;
    const bw = Math.max(2, Math.min(28, slot - 2));
    const parts = [svgOpen(W, H, label)];
    ticks.forEach(v => {
      parts.push(`<line class="${v === 0 ? 'zero-line' : 'grid-line'}" x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}"/>`);
      parts.push(`<text class="axis" x="${M.l - 8}" y="${y(v) + 4}" text-anchor="end">${esc(fmtAxis(v))}</text>`);
    });
    const r = Math.min(4, bw / 2);
    data.forEach((d, i) => {
      const v = d.value || 0;
      const x = M.l + i * slot + (slot - bw) / 2;
      const y0 = y(0), y1 = y(v);
      const h = Math.max(1, Math.abs(y1 - y0));
      // rounded at the data end, square at the baseline
      const top = v >= 0 ? y1 : y0;
      const path = v >= 0
        ? `M${x},${y0}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${y0}Z`
        : `M${x},${y0}V${y0 + h - r}Q${x},${y0 + h} ${x + r},${y0 + h}H${x + bw - r}Q${x + bw},${y0 + h} ${x + bw},${y0 + h - r}V${y0}Z`;
      parts.push(`<path class="${v >= 0 ? 'bar-pos' : 'bar-neg'}" d="${h <= 2 * r ? `M${x},${Math.min(y0, y1)}h${bw}v${h}h${-bw}Z` : path}"/>`);
    });
    // x labels: first, last, and a few between
    const want = Math.min(data.length, Math.max(2, Math.floor(pw / 90)));
    const seen = new Set();
    for (let k = 0; k < want; k++) {
      const i = Math.round((k / Math.max(1, want - 1)) * (data.length - 1));
      if (seen.has(i)) continue;
      seen.add(i);
      const x = M.l + i * slot + slot / 2;
      parts.push(`<text class="axis" x="${x}" y="${H - 6}" text-anchor="middle">${esc(data[i].label || date(data[i].t))}</text>`);
    }
    parts.push(`<rect class="hit" x="${M.l}" y="${M.t}" width="${pw}" height="${ph}"/>`);
    parts.push(`<rect class="bar-hover" id="hb" x="0" y="${M.t}" width="${slot}" height="${ph}" style="display:none"/>`);
    parts.push('</svg>');
    el.innerHTML = parts.join('');
    const svg = el.firstElementChild;
    const hb = svg.querySelector('#hb');
    const at = e => {
      const rect = svg.getBoundingClientRect();
      const i = Math.floor((e.clientX - rect.left - M.l) / slot);
      return i >= 0 && i < data.length ? i : null;
    };
    svg.querySelector('.hit').addEventListener('mousemove', e => {
      const i = at(e);
      if (i == null) return;
      hb.setAttribute('x', M.l + i * slot);
      hb.style.display = '';
      const d = data[i];
      tip.show(e.clientX, e.clientY, `<div class="tip-head">${esc(d.label ? d.label : weekday(d.t))}</div>
        <div class="tip-row"><b class="${d.value >= 0 ? 'tpos' : 'tneg'}">${esc(money(d.value, { sign: true }))}</b></div>
        ${d.detail ? `<div class="tip-sep"></div>${d.detail}` : ''}`);
    });
    svg.querySelector('.hit').addEventListener('mouseleave', () => { hb.style.display = 'none'; tip.hide(); });
    if (onSelect) svg.querySelector('.hit').addEventListener('click', e => { const i = at(e); if (i != null) onSelect(data[i]); });
  };
  return observe(el, draw);
}

// ── Line / area (equity) ───────────────────────────────────────────
// points: [{ t, v }], fill: 'accent' | 'sign' (green/red around baseline)
export function line(el, points, { height = 220, label = 'Equity', baseline = null, fmt = v => money(v), fill = 'accent' } = {}) {
  const draw = () => {
    if (points.length < 2) { el.innerHTML = '<div class="empty">Not enough history to draw this yet.</div>'; return; }
    const W = Math.max(280, el.clientWidth), H = height;
    const M = { l: 78, r: 10, t: 10, b: 24 };
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    const vs = points.map(p => p.v);
    const ticks = niceTicks(Math.min(...vs, baseline ?? Infinity), Math.max(...vs, baseline ?? -Infinity));
    const lo = ticks[0], hi = ticks[ticks.length - 1];
    const x = i => M.l + (i / (points.length - 1)) * pw;
    const y = v => M.t + ph - ((v - lo) / (hi - lo || 1)) * ph;
    const base = baseline != null ? y(baseline) : M.t + ph;
    const fmtAxis = axisFmt(ticks);
    const parts = [svgOpen(W, H, label)];
    ticks.forEach(v => {
      parts.push(`<line class="grid-line" x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}"/>`);
      parts.push(`<text class="axis" x="${M.l - 8}" y="${y(v) + 4}" text-anchor="end">${esc(fmtAxis(v))}</text>`);
    });
    const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
    const area = `${d}L${x(points.length - 1)},${base}L${x(0)},${base}Z`;
    if (fill === 'sign' && baseline != null) {
      const id = `c${Math.random().toString(36).slice(2, 8)}`;
      parts.push(`<defs><clipPath id="${id}a"><rect x="${M.l}" y="${M.t}" width="${pw}" height="${Math.max(0, base - M.t)}"/></clipPath>
        <clipPath id="${id}b"><rect x="${M.l}" y="${base}" width="${pw}" height="${Math.max(0, M.t + ph - base)}"/></clipPath></defs>`);
      parts.push(`<path class="area pos" d="${area}" clip-path="url(#${id}a)"/><path class="area neg" d="${area}" clip-path="url(#${id}b)"/>`);
      parts.push(`<line class="zero-line" x1="${M.l}" x2="${W - M.r}" y1="${base}" y2="${base}"/>`);
      parts.push(`<path class="line pos" d="${d}" clip-path="url(#${id}a)"/><path class="line neg" d="${d}" clip-path="url(#${id}b)"/>`);
    } else {
      parts.push(`<path class="area accent" d="${area}" opacity=".6"/><path class="line accent" d="${d}"/>`);
      if (baseline != null) parts.push(`<line class="zero-line" x1="${M.l}" x2="${W - M.r}" y1="${base}" y2="${base}" stroke-dasharray="3 3"/>`);
    }
    parts.push(`<text class="axis" x="${M.l}" y="${H - 6}">${esc(date(points[0].t))}</text>`);
    parts.push(`<text class="axis" x="${W - M.r}" y="${H - 6}" text-anchor="end">${esc(date(points[points.length - 1].t))}</text>`);
    parts.push(`<line class="cross" id="cx" y1="${M.t}" y2="${M.t + ph}" style="display:none"/><circle class="hover-dot" id="cd" r="4" style="display:none"/>`);
    parts.push(`<rect class="hit" x="${M.l}" y="${M.t}" width="${pw}" height="${ph}"/></svg>`);
    el.innerHTML = parts.join('');
    const svg = el.firstElementChild, cx = svg.querySelector('#cx'), cd = svg.querySelector('#cd');
    const show = (i, cxp, cyp) => {
      const p = points[i];
      cx.setAttribute('x1', x(i)); cx.setAttribute('x2', x(i)); cx.style.display = '';
      cd.setAttribute('cx', x(i)); cd.setAttribute('cy', y(p.v)); cd.style.display = '';
      const delta = baseline != null ? p.v - baseline : null;
      tip.show(cxp, cyp, `<div class="tip-head">${esc(weekday(p.t))}</div><div class="tip-row"><b>${esc(fmt(p.v))}</b></div>
        ${delta != null ? `<div class="tip-row"><span>vs start</span><b class="${delta >= 0 ? 'tpos' : 'tneg'}">${esc(money(delta, { sign: true }))}</b></div>` : ''}`);
    };
    const hit = svg.querySelector('.hit');
    hit.addEventListener('mousemove', e => {
      const rect = svg.getBoundingClientRect();
      const i = Math.round(Math.max(0, Math.min(1, (e.clientX - rect.left - M.l) / pw)) * (points.length - 1));
      show(i, e.clientX, e.clientY);
    });
    hit.addEventListener('mouseleave', () => { cx.style.display = 'none'; cd.style.display = 'none'; tip.hide(); });
  };
  return observe(el, draw);
}

// ── Sparkline (string) ─────────────────────────────────────────────
export function sparkline(values, { w = 96, h = 28 } = {}) {
  if (!values || values.length < 2) return `<svg class="spark" width="${w}" height="${h}"></svg>`;
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = i => (i / (values.length - 1)) * (w - 2) + 1;
  const y = v => h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4);
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const t = tone(values[values.length - 1] - values[0]);
  const color = t === 'pos' ? 'var(--gain)' : t === 'neg' ? 'var(--loss)' : 'var(--ink-3)';
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" stroke="${color}"/></svg>`;
}

// ── Cycle replay: underlying on top, cycle P&L below, one time axis ─
// data: GET /deployments/{id}/cycles/{key}/chart. Two panes instead of
// two y-scales on one plot (index points vs rupees aren't comparable).
export function replay(el, data, { showStrikes = true } = {}) {
  const st = { idx: null };
  const draw = () => {
    const t = data.series.t, n = t.length;
    if (n < 2) { el.innerHTML = '<div class="empty">Not enough data points in this cycle to draw a chart.</div>'; return; }
    const narrow = el.clientWidth < 560;
    const W = Math.max(300, el.clientWidth);
    const M = { l: narrow ? 50 : 66, r: 10 };
    const TOP = { y: 22, h: narrow ? 180 : 240 };
    const BOT = { y: TOP.y + TOP.h + 32, h: narrow ? 130 : 170 };
    const AX = BOT.y + BOT.h;
    const H = AX + 26;
    const pw = W - M.l - M.r;
    const xi = i => M.l + (i / (n - 1)) * pw;
    const fidx = ms => {
      if (ms <= t[0]) return 0;
      if (ms >= t[n - 1]) return n - 1;
      let a = 0, b = n - 1;
      while (b - a > 1) { const m = (a + b) >> 1; if (t[m] <= ms) a = m; else b = m; }
      return a + (ms - t[a]) / ((t[b] - t[a]) || 1);
    };
    const uMap = new Map((data.underlying || []).map(c => [c.t, c.c]));
    const u = t.map(v => (uMap.has(v) ? uMap.get(v) : null));
    const strikes = showStrikes ? data.positions.filter(p => p.strike != null && p.option_type) : [];
    const uk = u.filter(v => v != null);
    let uLo = Math.min(...uk, ...strikes.map(s => s.strike)), uHi = Math.max(...uk, ...strikes.map(s => s.strike));
    const pad = (uHi - uLo) * 0.06 || 1; uLo -= pad; uHi += pad;
    const yU = v => TOP.y + TOP.h - ((v - uLo) / (uHi - uLo || 1)) * TOP.h;
    const pnl = data.series.pnl;
    const snap = (data.snapshot_pnl || []).map(s => ({ i: fidx(s.t), v: s.pnl }));
    const pv = pnl.filter(v => v != null).concat(snap.map(s => s.v), [0]);
    const pt = niceTicks(Math.min(...pv), Math.max(...pv), 3);
    const pLo = pt[0], pHi = pt[pt.length - 1];
    const yP = v => BOT.y + BOT.h - ((v - pLo) / (pHi - pLo || 1)) * BOT.h;
    const z = yP(0);
    const P = [svgOpen(W, H, 'Underlying price and cycle P&L')];
    const path = (vals, yf) => { let d = '', pen = false; vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${xi(i).toFixed(1)},${yf(v).toFixed(1)}`; pen = true; }); return d; };
    // top pane
    P.push(`<text class="pane-title" x="${M.l}" y="${TOP.y - 8}">${esc((data.underlying_symbol || 'Underlying') + ' price')}</text>`);
    if (uk.length) {
      niceTicks(uLo, uHi, 3).filter(v => v >= uLo && v <= uHi).forEach(v => {
        P.push(`<line class="grid-line" x1="${M.l}" x2="${W - M.r}" y1="${yU(v)}" y2="${yU(v)}"/><text class="axis" x="${M.l - 6}" y="${yU(v) + 4}" text-anchor="end">${esc(num(v, 0))}</text>`);
      });
      strikes.forEach(s => {
        const x0 = xi(fidx(s.opened_at)), x1 = xi(fidx(s.closed_at != null ? s.closed_at : t[n - 1]));
        const yy = yU(s.strike).toFixed(1);
        P.push(`<line class="strike" x1="${x0}" x2="${Math.max(x1, x0 + 2)}" y1="${yy}" y2="${yy}"/><text class="strike-label" x="${x0 + 3}" y="${yy - 4}">${esc(`${s.side === 'short' ? 'Short' : 'Long'} ${s.option_type} ${num(s.strike, 0)}`)}</text>`);
      });
      P.push(`<path class="line" d="${path(u, yU)}"/>`);
    } else {
      P.push(`<text class="axis" x="${M.l + pw / 2}" y="${TOP.y + TOP.h / 2}" text-anchor="middle">${esc(data.underlying_error || 'No underlying history for this window.')}</text>`);
    }
    // bottom pane
    P.push(`<text class="pane-title" x="${M.l}" y="${BOT.y - 8}">Cycle P&amp;L (gross)</text>`);
    const pFmt = axisFmt(pt);
    pt.forEach(v => P.push(`<line class="${v === 0 ? 'zero-line' : 'grid-line'}" x1="${M.l}" x2="${W - M.r}" y1="${yP(v)}" y2="${yP(v)}"/><text class="axis" x="${M.l - 6}" y="${yP(v) + 4}" text-anchor="end">${esc(pFmt(v))}</text>`));
    const id = `r${Math.random().toString(36).slice(2, 8)}`;
    P.push(`<defs><clipPath id="${id}g"><rect x="${M.l}" y="${BOT.y - 4}" width="${pw}" height="${Math.max(0, z - BOT.y + 4)}"/></clipPath><clipPath id="${id}l"><rect x="${M.l}" y="${z}" width="${pw}" height="${Math.max(0, BOT.y + BOT.h - z + 4)}"/></clipPath></defs>`);
    let area = '', run = [];
    const flush = () => { if (run.length > 1) { area += `M${xi(run[0])},${z}` + run.map(i => `L${xi(i)},${yP(pnl[i])}`).join('') + `L${xi(run[run.length - 1])},${z}Z`; } run = []; };
    pnl.forEach((v, i) => { if (v == null) flush(); else run.push(i); }); flush();
    const pl = path(pnl, yP);
    P.push(`<path class="area pos" d="${area}" clip-path="url(#${id}g)"/><path class="area neg" d="${area}" clip-path="url(#${id}l)"/>`);
    P.push(`<path class="line pos" d="${pl}" clip-path="url(#${id}g)"/><path class="line neg" d="${pl}" clip-path="url(#${id}l)"/>`);
    if (snap.length > 1 && pnl.some(v => v == null)) P.push(`<path class="snap-line" d="${snap.map((s, k) => `${k ? 'L' : 'M'}${xi(s.i)},${yP(s.v)}`).join('')}"/>`);
    // day separators / time labels
    const dk = ms => new Date(ms).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
    const multi = dk(t[0]) !== dk(t[n - 1]);
    const labels = [];
    if (multi) {
      for (let i = 1; i < n; i++) if (dk(t[i]) !== dk(t[i - 1])) {
        P.push(`<line class="day-line" x1="${xi(i)}" x2="${xi(i)}" y1="${TOP.y}" y2="${AX}"/>`);
        labels.push([xi(i), date(t[i])]);
      }
    } else {
      const k = narrow ? 4 : 7;
      for (let j = 0; j < k; j++) { const i = Math.round(j / (k - 1) * (n - 1)); labels.push([xi(i), time(t[i])]); }
    }
    let last = -1e9;
    labels.forEach(([x, s]) => { if (x - last < 48) return; last = x; P.push(`<text class="axis" x="${x}" y="${AX + 16}" text-anchor="middle">${esc(s)}</text>`); });
    // events
    const SH = {
      entry: (x, y) => `<path class="mk entry" d="M${x},${y - 6}L${x + 6},${y + 5}L${x - 6},${y + 5}Z"/>`,
      adjustment: (x, y) => `<path class="mk adjustment" d="M${x},${y - 6}L${x + 6},${y}L${x},${y + 6}L${x - 6},${y}Z"/>`,
      exit: (x, y) => `<path class="mk exit" d="M${x - 6},${y - 5}L${x + 6},${y - 5}L${x},${y + 6}Z"/>`,
    };
    st.events = (data.events || []).map(ev => {
      const fi = fidx(ev.t), i = Math.max(0, Math.min(n - 1, Math.round(fi))), x = Math.round(xi(fi));
      P.push(`<line class="event-line" x1="${x}" x2="${x}" y1="${TOP.y}" y2="${AX}"/>`);
      P.push(SH[ev.kind](x, Math.round(u[i] != null ? yU(u[i]) : TOP.y + TOP.h - 10)));
      P.push(SH[ev.kind](x, Math.round(pnl[i] != null ? yP(pnl[i]) : z)));
      return { i, ev };
    });
    P.push(`<line class="cross" id="rx" y1="${TOP.y}" y2="${AX}" style="display:none"/><circle class="hover-dot" id="ru" r="4" style="display:none"/><circle class="hover-dot" id="rp" r="4" style="display:none"/>`);
    P.push(`<rect class="hit" x="${M.l}" y="${TOP.y}" width="${pw}" height="${AX - TOP.y}"/></svg>`);
    el.innerHTML = P.join('');
    const svg = el.firstElementChild;
    svg.setAttribute('tabindex', '0');
    const show = (i, cx, cy) => {
      st.idx = i;
      const x = xi(i);
      const rx = svg.querySelector('#rx'), ru = svg.querySelector('#ru'), rp = svg.querySelector('#rp');
      rx.setAttribute('x1', x); rx.setAttribute('x2', x); rx.style.display = '';
      if (u[i] != null) { ru.setAttribute('cx', x); ru.setAttribute('cy', yU(u[i])); ru.style.display = ''; } else ru.style.display = 'none';
      if (pnl[i] != null) { rp.setAttribute('cx', x); rp.setAttribute('cy', yP(pnl[i])); rp.style.display = ''; } else rp.style.display = 'none';
      const evalT = t[i] + (data.series.eval_offset_ms || 0);
      const legs = data.positions.map((p, k) => ({ p, v: data.series.pos[k][i] }))
        .filter(r => r.p.opened_at <= evalT)
        .map(r => ({ ...r, closed: r.p.closed_at != null && r.p.closed_at <= evalT }))
        .sort((a, b) => (a.closed - b.closed) || (b.p.opened_at - a.p.opened_at));
      const evs = st.events.filter(e => e.i === i).map(e => `<div class="tip-row"><b>${esc(e.ev.kind[0].toUpperCase() + e.ev.kind.slice(1))}</b><span>${esc(e.ev.reason || '')}</span></div>`).join('');
      tip.show(cx, cy, `<div class="tip-head">${esc(weekday(t[i]))} · ${esc(time(t[i]))} IST</div>${evs}
        <div class="tip-row"><span>${esc(data.underlying_symbol || 'Underlying')}</span><b>${u[i] != null ? esc(num(u[i], 2)) : '—'}</b></div>
        <div class="tip-row"><span>Cycle P&amp;L</span><b class="${(pnl[i] || 0) >= 0 ? 'tpos' : 'tneg'}">${pnl[i] != null ? esc(money(pnl[i], { sign: true })) : '—'}</b></div>
        ${legs.length ? '<div class="tip-sep"></div>' : ''}
        ${legs.slice(0, 12).map(r => `<div class="tip-row"><span>${esc(r.p.symbol)} · ${r.closed ? 'closed' : 'open'}</span><b class="${(r.v || 0) >= 0 ? 'tpos' : 'tneg'}">${r.v != null ? esc(money(r.v, { sign: true })) : 'no price'}</b></div>`).join('')}
        ${legs.length > 12 ? `<div class="tip-row"><span>+${legs.length - 12} more legs</span></div>` : ''}`);
    };
    const hide = () => { ['#rx', '#ru', '#rp'].forEach(s => { svg.querySelector(s).style.display = 'none'; }); tip.hide(); st.idx = null; };
    const hit = svg.querySelector('.hit');
    const fromEvent = e => {
      const rect = svg.getBoundingClientRect();
      return Math.round(Math.max(0, Math.min(1, (e.clientX - rect.left - M.l) / pw)) * (n - 1));
    };
    hit.addEventListener('mousemove', e => show(fromEvent(e), e.clientX, e.clientY));
    hit.addEventListener('mouseleave', hide);
    hit.addEventListener('touchmove', e => { const tt = e.touches[0]; if (tt) { e.preventDefault(); show(fromEvent(tt), tt.clientX, tt.clientY); } }, { passive: false });
    hit.addEventListener('touchend', hide);
    svg.addEventListener('keydown', e => {
      const map = { ArrowLeft: -1, ArrowRight: 1 };
      if (!(e.key in map) && e.key !== 'Home' && e.key !== 'End') return;
      e.preventDefault();
      let i = st.idx == null ? 0 : st.idx;
      if (e.key === 'Home') i = 0; else if (e.key === 'End') i = n - 1; else i = Math.max(0, Math.min(n - 1, i + map[e.key]));
      const rect = svg.getBoundingClientRect();
      show(i, rect.left + (xi(i) / W) * rect.width, rect.top + 60);
    });
    svg.addEventListener('blur', hide);
  };
  return observe(el, draw);
}
