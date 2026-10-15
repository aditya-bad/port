// Cycle Replay chart (Detail -> Cycle Replay tab; data from
// routers/cycles.py). Two panes sharing one time axis and one crosshair
// -- underlying price on top, the cycle's gross P&L below -- rather than
// one chart with two y-scales, which would let the relative scaling of
// two unrelated units fake a correlation. X is by candle index, not
// wall-clock time, so overnight/weekend gaps of a multi-day cycle don't
// stretch the chart into long flat lines.
//
// Plain SVG in real pixel coordinates (re-rendered on resize), so
// markers keep their shape. Everything shown on hover is also in the
// tables the Detail tab renders under the chart.

const CycleChart = {
  _state: null,        // { el, data, showStrikes, idx, ... } of the chart on screen
  _resizeTimer: null,

  render(el, data, opts = {}) {
    this._state = { el, data, showStrikes: opts.showStrikes !== false, idx: null };
    this._draw();
    if (!this._resizeBound) {
      this._resizeBound = true;
      window.addEventListener('resize', () => {
        clearTimeout(this._resizeTimer);
        this._resizeTimer = setTimeout(() => {
          if (this._state && document.body.contains(this._state.el)) this._draw();
        }, 120);
      });
    }
  },

  setShowStrikes(on) {
    if (!this._state) return;
    this._state.showStrikes = on;
    this._draw();
  },

  _draw() {
    const st = this._state;
    const { el, data } = st;
    const t = data.series.t;
    const n = t.length;
    if (n < 2) {
      el.innerHTML = emptyHtml('Not enough data points in this cycle to draw a chart.');
      return;
    }
    const narrow = el.clientWidth < 560;
    const W = Math.max(320, el.clientWidth);
    const M = { l: narrow ? 52 : 70, r: narrow ? 8 : 16 };
    // Pane titles sit in the gap ABOVE each pane, clear of strike labels.
    const TOP = { y: 22, h: narrow ? 190 : 250 };
    const BOT = { y: TOP.y + TOP.h + 30, h: narrow ? 140 : 180 };
    const AXIS_Y = BOT.y + BOT.h + 6;
    const H = AXIS_Y + 22;
    const plotW = W - M.l - M.r;
    const xOf = i => M.l + (i / (n - 1)) * plotW;
    // Fractional index for an arbitrary timestamp, interpolated between
    // timeline points (fills, snapshots, strike segment ends).
    const idxOfTime = ms => {
      if (ms <= t[0]) return 0;
      if (ms >= t[n - 1]) return n - 1;
      let lo = 0, hi = n - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (t[mid] <= ms) lo = mid; else hi = mid;
      }
      return lo + (ms - t[lo]) / ((t[hi] - t[lo]) || 1);
    };
    st.geom = { W, H, M, TOP, BOT, plotW, n, xOf, idxOfTime };

    const parts = [];

    // ── Top pane: underlying + strikes ──
    const u = data.underlying || [];
    const uByT = new Map(u.map(c => [c.t, c.c]));
    const uVals = t.map(x => (uByT.has(x) ? uByT.get(x) : null));
    st.uVals = uVals;
    const strikeSegs = st.showStrikes
      ? data.positions.filter(p => p.strike != null && p.option_type).map(p => ({
          p, x0: xOf(idxOfTime(p.opened_at)),
          x1: xOf(idxOfTime(p.closed_at != null ? p.closed_at : t[n - 1])),
        }))
      : [];
    const uKnown = uVals.filter(v => v != null);
    let uMin = Math.min(...uKnown), uMax = Math.max(...uKnown);
    strikeSegs.forEach(s => { uMin = Math.min(uMin, s.p.strike); uMax = Math.max(uMax, s.p.strike); });
    if (uKnown.length) {
      const pad = (uMax - uMin) * 0.06 || uMax * 0.002 || 1;
      uMin -= pad; uMax += pad;
    }
    const yU = v => TOP.y + TOP.h - ((v - uMin) / ((uMax - uMin) || 1)) * TOP.h;
    st.yU = yU;

    parts.push(this._paneFrame(M.l, TOP.y, plotW, TOP.h));
    if (uKnown.length) {
      parts.push(this._yTicks(uMin, uMax, yU, M.l, plotW, v => fmtNum(v, 0)));
      strikeSegs.forEach(s => {
        const y = yU(s.p.strike).toFixed(1);
        const label = `${s.p.side === 'short' ? 'Short' : 'Long'} ${s.p.option_type} ${fmtNum(s.p.strike, 2)}`;
        parts.push(`<line class="cc-strike ${s.p.side}" x1="${s.x0.toFixed(1)}" x2="${Math.max(s.x1, s.x0 + 2).toFixed(1)}" y1="${y}" y2="${y}"><title>${escapeHtml(label)}</title></line>`);
        parts.push(`<text class="cc-strike-label" x="${(s.x0 + 3).toFixed(1)}" y="${(Number(y) - 4).toFixed(1)}">${escapeHtml(label)}</text>`);
      });
      parts.push(`<path class="cc-underlying" d="${this._linePath(uVals, xOf, yU)}"/>`);
    } else {
      parts.push(`<text class="cc-empty" x="${(M.l + plotW / 2).toFixed(1)}" y="${(TOP.y + TOP.h / 2).toFixed(1)}" text-anchor="middle">${escapeHtml(data.underlying_error || 'No underlying history for this window.')}</text>`);
    }
    parts.push(`<text class="cc-pane-title" x="${M.l}" y="${TOP.y - 8}">${escapeHtml((data.underlying_symbol || 'Underlying') + ' — price')}</text>`);

    // ── Bottom pane: P&L ──
    const pnl = data.series.pnl;
    const snap = (data.snapshot_pnl || []).map(s => ({ i: idxOfTime(s.t), v: s.pnl }));
    const pKnown = pnl.filter(v => v != null).concat(snap.map(s => s.v)).concat([0]);
    let pMin = Math.min(...pKnown), pMax = Math.max(...pKnown);
    const pPad = (pMax - pMin) * 0.08 || 100;
    pMin -= pPad; pMax += pPad;
    const yP = v => BOT.y + BOT.h - ((v - pMin) / ((pMax - pMin) || 1)) * BOT.h;
    st.yP = yP;
    const zeroY = yP(0);

    parts.push(this._paneFrame(M.l, BOT.y, plotW, BOT.h));
    parts.push(this._yTicks(pMin, pMax, yP, M.l, plotW, v => fmtSignedMoney(Math.round(v))));
    parts.push(`<line class="cc-zero" x1="${M.l}" x2="${M.l + plotW}" y1="${zeroY.toFixed(1)}" y2="${zeroY.toFixed(1)}"/>`);
    const clipId = `cc-clip-${Math.random().toString(36).slice(2)}`;
    parts.push(`<defs>
      <clipPath id="${clipId}-gain"><rect x="${M.l}" y="${BOT.y}" width="${plotW}" height="${Math.max(0, zeroY - BOT.y).toFixed(1)}"/></clipPath>
      <clipPath id="${clipId}-loss"><rect x="${M.l}" y="${zeroY.toFixed(1)}" width="${plotW}" height="${Math.max(0, BOT.y + BOT.h - zeroY).toFixed(1)}"/></clipPath>
    </defs>`);
    const area = this._areaPath(pnl, xOf, yP, zeroY);
    const line = this._linePath(pnl, xOf, yP);
    parts.push(`<path class="cc-area gain" d="${area}" clip-path="url(#${clipId}-gain)"/>`);
    parts.push(`<path class="cc-area loss" d="${area}" clip-path="url(#${clipId}-loss)"/>`);
    parts.push(`<path class="cc-pnl gain" d="${line}" clip-path="url(#${clipId}-gain)"/>`);
    parts.push(`<path class="cc-pnl loss" d="${line}" clip-path="url(#${clipId}-loss)"/>`);
    if (snap.length > 1 && pnl.some(v => v == null)) {
      const d = snap.map((s, k) => `${k ? 'L' : 'M'}${xOf(s.i).toFixed(1)},${yP(s.v).toFixed(1)}`).join('');
      parts.push(`<path class="cc-snapshot" d="${d}"/>`);
    }
    parts.push(`<text class="cc-pane-title" x="${M.l}" y="${BOT.y - 8}">Cycle P&amp;L (gross)</text>`);

    // ── Day separators + x-axis labels ──
    const dayKey = ms => new Date(ms).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
    const multiDay = dayKey(t[0]) !== dayKey(t[n - 1]);
    const labels = [];
    if (multiDay) {
      for (let i = 1; i < n; i++) {
        if (dayKey(t[i]) !== dayKey(t[i - 1])) {
          const x = xOf(i).toFixed(1);
          parts.push(`<line class="cc-day" x1="${x}" x2="${x}" y1="${TOP.y}" y2="${BOT.y + BOT.h}"/>`);
          labels.push({ x: xOf(i), text: new Date(t[i]).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' }) });
        }
      }
    } else {
      const want = narrow ? 4 : 7;
      for (let k = 0; k < want; k++) {
        const i = Math.round((k / (want - 1)) * (n - 1));
        labels.push({ x: xOf(i), text: new Date(t[i]).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }) });
      }
    }
    // Drop labels that would collide.
    let lastX = -Infinity;
    labels.forEach(lb => {
      if (lb.x - lastX < 46) return;
      lastX = lb.x;
      parts.push(`<text class="cc-axis-x" x="${lb.x.toFixed(1)}" y="${AXIS_Y + 14}" text-anchor="middle">${escapeHtml(lb.text)}</text>`);
    });

    // ── Events: a hairline through both panes + a shape on each pane ──
    const SHAPES = {
      entry: (x, y) => `<path class="cc-mk entry" d="M${x},${y - 6}L${x + 6},${y + 5}L${x - 6},${y + 5}Z"/>`,
      adjustment: (x, y) => `<path class="cc-mk adjustment" d="M${x},${y - 6}L${x + 6},${y}L${x},${y + 6}L${x - 6},${y}Z"/>`,
      exit: (x, y) => `<path class="cc-mk exit" d="M${x - 6},${y - 5}L${x + 6},${y - 5}L${x},${y + 6}Z"/>`,
    };
    st.eventIdx = [];
    (data.events || []).forEach(ev => {
      const fi = idxOfTime(ev.t);
      const i = Math.min(n - 1, Math.max(0, Math.round(fi)));
      st.eventIdx.push({ i, ev });
      const x = xOf(fi);
      parts.push(`<line class="cc-event-line" x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${TOP.y}" y2="${BOT.y + BOT.h}"/>`);
      const uy = uVals[i] != null ? yU(uVals[i]) : TOP.y + TOP.h - 10;
      const py = pnl[i] != null ? yP(pnl[i]) : zeroY;
      parts.push(SHAPES[ev.kind](Math.round(x), Math.round(uy)));
      parts.push(SHAPES[ev.kind](Math.round(x), Math.round(py)));
    });

    // ── Hover layer ──
    parts.push(`<line class="cc-crosshair" id="ccCross" x1="0" x2="0" y1="${TOP.y}" y2="${BOT.y + BOT.h}" style="display:none"/>`);
    parts.push(`<circle class="cc-dot" id="ccDotU" r="4" style="display:none"/>`);
    parts.push(`<circle class="cc-dot" id="ccDotP" r="4" style="display:none"/>`);
    parts.push(`<rect class="cc-hit" x="${M.l}" y="${TOP.y}" width="${plotW}" height="${BOT.y + BOT.h - TOP.y}"
      onmousemove="CycleChart._pointer(event)" onmouseleave="CycleChart._clear()"
      ontouchstart="CycleChart._touch(event)" ontouchmove="CycleChart._touch(event)" ontouchend="CycleChart._clear()"/>`);

    el.innerHTML = `<svg class="cc-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Underlying price and cycle P&L over this cycle" tabindex="0"
      onkeydown="CycleChart._key(event)" onblur="CycleChart._clear()">${parts.join('')}</svg>`;
  },

  _paneFrame(x, y, w, h) {
    return `<rect class="cc-pane" x="${x}" y="${y}" width="${w}" height="${h}"/>`;
  },

  _yTicks(min, max, yOf, left, plotW, fmt) {
    const out = [];
    for (let k = 0; k <= 3; k++) {
      const v = min + (k / 3) * (max - min);
      const y = yOf(v).toFixed(1);
      out.push(`<line class="cc-grid" x1="${left}" x2="${left + plotW}" y1="${y}" y2="${y}"/>`);
      out.push(`<text class="cc-axis-y" x="${left - 6}" y="${(Number(y) + 3.5).toFixed(1)}" text-anchor="end">${escapeHtml(fmt(v))}</text>`);
    }
    return out.join('');
  },

  // Breaks the line at nulls rather than bridging unknown stretches.
  _linePath(vals, xOf, yOf) {
    let d = '', pen = false;
    vals.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  },

  _areaPath(vals, xOf, yOf, zeroY) {
    let d = '', run = [];
    const flush = () => {
      if (run.length > 1) {
        d += `M${xOf(run[0]).toFixed(1)},${zeroY.toFixed(1)}`;
        run.forEach(i => { d += `L${xOf(i).toFixed(1)},${yOf(vals[i]).toFixed(1)}`; });
        d += `L${xOf(run[run.length - 1]).toFixed(1)},${zeroY.toFixed(1)}Z`;
      }
      run = [];
    };
    vals.forEach((v, i) => { if (v == null) flush(); else run.push(i); });
    flush();
    return d;
  },

  // ── Hover / touch / keyboard ──
  _pointer(event) {
    const st = this._state;
    if (!st || !st.geom) return;
    const svg = event.currentTarget.ownerSVGElement;
    const rect = svg.getBoundingClientRect();
    const g = st.geom;
    const x = (event.clientX - rect.left) * (g.W / rect.width);
    const i = Math.round(Math.max(0, Math.min(1, (x - g.M.l) / g.plotW)) * (g.n - 1));
    this._show(i, event.clientX, event.clientY);
  },

  _touch(event) {
    event.preventDefault();
    const touch = event.touches[0];
    if (!touch) return;
    this._pointer({ currentTarget: event.currentTarget, clientX: touch.clientX, clientY: touch.clientY });
  },

  _key(event) {
    const st = this._state;
    if (!st || !st.geom) return;
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const g = st.geom;
    let i = st.idx == null ? 0 : st.idx;
    if (event.key === 'ArrowLeft') i = Math.max(0, i - 1);
    if (event.key === 'ArrowRight') i = Math.min(g.n - 1, i + 1);
    if (event.key === 'Home') i = 0;
    if (event.key === 'End') i = g.n - 1;
    const svg = event.currentTarget;
    const rect = svg.getBoundingClientRect();
    this._show(i, rect.left + (g.xOf(i) / g.W) * rect.width, rect.top + rect.height * 0.3);
  },

  _show(i, clientX, clientY) {
    const st = this._state;
    const { data, geom: g } = st;
    st.idx = i;
    const x = g.xOf(i).toFixed(1);
    const cross = document.getElementById('ccCross');
    if (cross) { cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.style.display = ''; }
    const u = st.uVals[i];
    const p = data.series.pnl[i];
    const dotU = document.getElementById('ccDotU');
    const dotP = document.getElementById('ccDotP');
    if (dotU) {
      if (u != null) { dotU.setAttribute('cx', x); dotU.setAttribute('cy', st.yU(u).toFixed(1)); dotU.style.display = ''; }
      else dotU.style.display = 'none';
    }
    if (dotP) {
      if (p != null) { dotP.setAttribute('cx', x); dotP.setAttribute('cy', st.yP(p).toFixed(1)); dotP.style.display = ''; }
      else dotP.style.display = 'none';
    }

    const tMs = data.series.t[i];
    const when = new Date(tMs).toLocaleString('en-IN', {
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata',
    });
    // Same rule as the backend: a point reflects the state at its candle's close.
    const evalT = tMs + (data.series.eval_offset_ms || 0);
    const rows = [];
    data.positions.forEach((pos, k) => {
      if (pos.opened_at > evalT) return;
      const closed = pos.closed_at != null && pos.closed_at <= evalT;
      rows.push({ pos, v: data.series.pos[k][i], closed });
    });
    rows.sort((a, b) => (a.closed - b.closed) || (b.pos.opened_at - a.pos.opened_at));
    const shown = rows.slice(0, 12);
    const posHtml = shown.map(r => {
      const val = r.v == null ? '<span class="cc-tip-muted">no price</span>' : `<b class="${r.v >= 0 ? 'pos' : 'neg'}">${escapeHtml(fmtSignedMoney(r.v))}</b>`;
      const tag = r.closed ? 'closed' : 'open';
      return `<div class="cc-tip-row">${val}<span>${escapeHtml(r.pos.symbol)} · ${r.pos.side} · ${tag}</span></div>`;
    }).join('');
    const more = rows.length > shown.length ? `<div class="cc-tip-muted">+${rows.length - shown.length} more legs</div>` : '';
    const evs = st.eventIdx.filter(e => e.i === i).map(e =>
      `<div class="cc-tip-event ${e.ev.kind}">${escapeHtml(e.ev.kind)}${e.ev.reason ? ` — ${escapeHtml(e.ev.reason)}` : ''}</div>`).join('');
    const html = `
      <div class="cc-tip-time">${escapeHtml(when)} IST</div>
      ${evs}
      <div class="cc-tip-row"><b>${u != null ? escapeHtml(fmtNum(u, 2)) : '—'}</b><span>${escapeHtml(data.underlying_symbol || 'Underlying')}</span></div>
      <div class="cc-tip-row"><b class="${(p || 0) >= 0 ? 'pos' : 'neg'}">${p != null ? escapeHtml(fmtSignedMoney(p)) : '—'}</b><span>Cycle P&amp;L (gross)</span></div>
      ${posHtml ? `<div class="cc-tip-sep"></div>${posHtml}${more}` : ''}`;
    const tip = ChartTooltip._ensure();
    tip.classList.add('cc-tip');
    ChartTooltip.show(clientX, clientY, html);
  },

  _clear() {
    ['ccCross', 'ccDotU', 'ccDotP'].forEach(id => {
      const node = document.getElementById(id);
      if (node) node.style.display = 'none';
    });
    if (this._state) this._state.idx = null;
    ChartTooltip.hide();
    if (ChartTooltip._el) ChartTooltip._el.classList.remove('cc-tip');
  },
};
