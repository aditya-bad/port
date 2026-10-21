// Trades & replay: every closed trade; click one to replay it — the
// underlying above the trade's P&L on one time axis, entry/adjust/exit
// markers, a step slider, and per-leg P&L on hover.

import { api } from '../../core/api.js';
import { html, raw, mount, $, actions, on, download } from '../../core/dom.js';
import { inr, pct, price, dShort, dStamp, hhmm, plural, tone, toneVar } from '../../core/format.js';
import { pnlOf, basis } from '../../core/basis.js';
import { ui } from '../../core/store.js';
import * as book from '../../data/book.js';
import { path, yOf } from '../../ui/charts.js';
import { registerScrub } from '../../ui/tooltip.js';
import { chips, empty, loading, fixTag } from '../../ui/components.js';
import { say } from '../../ui/toast.js';

const st = ui('trades', { f: 'all', sk: 'date', sd: -1 });
const pnlGross = t => Number(t.pnl);
const KIND = { entry: ['Entry', 'var(--ink)'], adjustment: ['Adjustment', 'var(--b)'], rollout: ['EOD roll-out', 'var(--c5)'], exit: ['Exit', 'var(--b)'] };

export default {
  render(el, v0) {
    let lim = 30, sel = null, replay = null, cycles = null;
    const v = () => book.find(v0.id) || v0;

    function rows() {
      const s = st.get();
      let R = v().trades.map((t, i) => {
        const o = new Date(t.opened_at), c = new Date(t.closed_at), p = pnlOf(t);
        return { i, t, o, c, p, held: Math.round((c - o) / 864e5), ret: p / v().cap * 100 };
      });
      const cnt = { all: R.length, win: R.filter(r => r.p > 0).length, loss: R.filter(r => r.p <= 0).length };
      R = R.filter(r => s.f === 'all' || (s.f === 'win' ? r.p > 0 : r.p <= 0));
      const SK = { n: r => r.i, date: r => +r.c, held: r => r.held, reason: r => r.t.exit_reason || '', pnl: r => r.p };
      const f = SK[s.sk] || SK.date;
      R.sort((a, b) => { const p = f(a), q = f(b); return (typeof p === 'string' ? p.localeCompare(q) : p - q) * s.sd; });
      return { R, cnt };
    }

    function replayPanel() {
      if (!sel) return '';
      const r = v().trades[sel.i];
      const head = html`<div class="row base"><h3 class="t-h3">Replay · trade ${sel.i + 1}</h3><span class="grow"></span>
        <button type="button" class="b" style="padding:2px 6px" data-act="closeReplay" aria-label="Close replay">×</button></div>
        <p class="small mute" style="margin:0">${r.exit_reason || 'closed'} · ${inr(pnlOf(r))} · ${v().modeKey === 'intraday' ? 'intraday' : `held ${plural(Math.max(1, Math.round((new Date(r.closed_at) - new Date(r.opened_at)) / 864e5)), 'day')}`}</p>`;
      if (!replay) return html`<aside class="replay">${head}${loading(4)}</aside>`;
      if (replay.error) return html`<aside class="replay">${head}${empty('', replay.error)}</aside>`;
      const R = replay, pos = Math.min(sel.pos, R.pts.length - 1), cur = R.pts[pos];
      const lo = Math.min(0, ...R.pts.map(p => p[1])), hi = Math.max(0, ...R.pts.map(p => p[1]));
      const fx = t => (t - R.t0) / ((R.t1 - R.t0) || 1);
      const xy = R.pts.map(([t, val]) => [fx(t), val]);
      const dFull = xy.map(([x, val], i) => `${i ? 'L' : 'M'}${(x * 1000).toFixed(1)} ${yOf(val, 160, lo, hi).toFixed(1)}`).join(' ');
      const dNow = xy.slice(0, pos + 1).map(([x, val], i) => `${i ? 'L' : 'M'}${(x * 1000).toFixed(1)} ${yOf(val, 160, lo, hi).toFixed(1)}`).join(' ');
      const ulo = R.u.length ? Math.min(...R.u.map(c => c.c)) : 0, uhi = R.u.length ? Math.max(...R.u.map(c => c.c)) : 1;
      const uPath = R.u.length ? R.u.map((c, i) => `${i ? 'L' : 'M'}${(fx(c.t) * 1000).toFixed(1)} ${yOf(c.c, 80, ulo, uhi).toFixed(1)}`).join(' ') : '';
      return html`<aside class="replay">${head}
        ${R.u.length ? html`<div class="tiny mute">${R.sym || 'Underlying'} · ${price(R.u[0].c)} → ${price(R.u[R.u.length - 1].c)}</div>
          <svg viewBox="0 0 1000 80" preserveAspectRatio="none" style="width:100%;height:80px;border-bottom:1px solid var(--line)" data-scrub="rp" aria-label="Underlying">
            ${raw(`<path d="${uPath}" fill="none" stroke="var(--mute)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`)}</svg>` : html`<div class="tiny mute">${R.uErr || 'No underlying history for this window.'}</div>`}
        <div class="replay-plot">
          <svg viewBox="0 0 1000 160" preserveAspectRatio="none" style="width:100%;height:160px;overflow:visible;border-bottom:1px solid var(--line)" data-scrub="rp" aria-label="Trade P&L">
            ${raw(`<line x1="0" x2="1000" y1="${yOf(0, 160, lo, hi).toFixed(1)}" y2="${yOf(0, 160, lo, hi).toFixed(1)}" stroke="var(--line2)" stroke-dasharray="4 5" vector-effect="non-scaling-stroke"/>
              <path d="${dFull}" fill="none" stroke="var(--line2)" stroke-width="2" vector-effect="non-scaling-stroke"/>
              <path d="${dNow}" fill="none" stroke="${toneVar(R.final)}" stroke-width="2.5" vector-effect="non-scaling-stroke"/>`)}
          </svg>
          ${R.events.map(e => html`<span class="rp-ev" style="left:${fx(e.t) * 100}%;background:${(KIND[e.kind] || KIND.entry)[1]}" data-tip="${(KIND[e.kind] || KIND.entry)[0]} · ${dStamp(e.t)}\n${e.fills.map(f => `${f.action.toUpperCase()} ${f.qty} ${f.symbol} @ ${price(f.price)}`).join('\n')}"></span>`)}
          <span class="rp-dot" style="left:${xy[pos][0] * 100}%;top:${yOf(cur[1], 160, lo, hi) / 160 * 100}%"></span>
        </div>
        <input type="range" min="0" max="${R.pts.length - 1}" value="${pos}" data-slider aria-label="Replay position" style="width:100%;accent-color:currentColor">
        <div class="row" style="gap:6px">
          <button type="button" class="btn btn-sm" data-act="step" data-d="-1">‹ Step</button>
          <button type="button" class="btn btn-sm" data-act="step" data-d="1">Step ›</button>
          <span class="grow"></span><span class="mono small ${tone(cur[1])}">${dStamp(cur[0])} · MTM ${inr(cur[1])}</span>
        </div>
        <div class="stack">${R.events.map(e => html`
          <button type="button" class="rp-step ${e.t <= cur[0] ? '' : 'later'} ${Math.abs(e.idx - pos) <= 0 ? 'on' : ''}" data-act="jump" data-i="${e.idx}">
            <span class="mono tiny mute">${dStamp(e.t)}</span><span><b>${(KIND[e.kind] || KIND.entry)[0]}</b> ${e.reason ? html`<span class="mute">· ${e.reason}</span>` : ''}<br>
            <span class="mono tiny mute">${e.fills.map(f => `${f.action === 'sell' ? 'Sold' : 'Bought'} ${f.qty} ${f.symbol}`).join('; ')}</span></span></button>`)}</div>
        ${R.notes.map(n => html`<p class="tiny mute" style="margin:0">${n}</p>`)}
        ${basis() !== 'gross' ? html`<p class="tiny mute" style="margin:0">The replay line is gross; the trade’s result above uses your P&L basis.</p>` : ''}
      </aside>`;
    }

    async function loadReplay(i) {
      replay = null; paint();
      const t = v().trades[i];
      try {
        if (!cycles) cycles = await api.cycles(v0.id);
        const o = new Date(t.opened_at).getTime(), c = new Date(t.closed_at).getTime();
        const cy = cycles.find(x => { const s = new Date(x.start).getTime(), e = x.end ? new Date(x.end).getTime() : Date.now(); return o >= s - 60000 && o <= e; });
        if (!cy) throw new Error('This trade’s fills aren’t available to replay.');
        const ch = await api.cycleChart(v0.id, cy.key);
        const S = ch.series;
        // Same rule as the server's own curve: if any leg lacks prices,
        // use the 5-minute account snapshots instead.
        let pts = S.t.map((tt, k) => [tt + (S.eval_offset_ms || 0), S.pnl[k]]);
        if (pts.some(p => p[1] == null) && ch.snapshot_pnl.length) pts = [[o, 0], ...ch.snapshot_pnl.map(p => [p.t, p.pnl])];
        pts = pts.filter(p => p[1] != null && p[0] >= o - 5 * 60000 && p[0] <= c + 5 * 60000);
        if (pts.length && pts[pts.length - 1][0] < c) pts.push([c, pnlGross(t)]);
        if (pts.length < 2) throw new Error(ch.notes[0] || 'Not enough price history recorded for this trade to replay it.');
        const events = ch.events.map(e => ({ ...e, idx: pts.reduce((best, p, k) => (Math.abs(p[0] - e.t) < Math.abs(pts[best][0] - e.t) ? k : best), 0) }));
        const legs = ch.positions;
        const legAt = tt => {
          const k = S.t.reduce((best, x, j) => (Math.abs(x - tt) < Math.abs(S.t[best] - tt) ? j : best), 0);
          return legs.map((lg, j) => [lg.symbol, S.pos[j] ? S.pos[j][k] : null]).filter(x => x[1] != null);
        };
        replay = { pts, t0: pts[0][0], t1: pts[pts.length - 1][0], u: ch.underlying || [], uErr: ch.underlying_error, sym: ch.underlying_symbol, events, notes: ch.notes || [], final: pnlOf(t), legAt };
        sel.pos = 0;
        registerScrub('rp', f => {
          const tt = replay.t0 + f * (replay.t1 - replay.t0);
          const p = replay.pts.reduce((best, x) => (Math.abs(x[0] - tt) < Math.abs(best[0] - tt) ? x : best), replay.pts[0]);
          const u = replay.u.length ? replay.u.reduce((best, x) => (Math.abs(x.t - tt) < Math.abs(best.t - tt) ? x : best), replay.u[0]) : null;
          return { t: [`${dShort(p[0])} ${hhmm(p[0])}`, u ? `${replay.sym || 'Underlying'}  ${price(u.c)}` : null, `Trade P&L  ${inr(p[1])}`, ...replay.legAt(p[0]).map(([s, val]) => `${s}  ${inr(val)}`)].filter(Boolean).join('\n') };
        });
      } catch (e) {
        replay = { error: e.message };
      }
      paint();
    }

    function paint() {
      const { R, cnt } = rows();
      const s = st.get();
      const cols = [['n', '#'], ['date', 'Opened', 'o'], ['date', 'Closed'], ['held', 'Days', 'r'], ['reason', 'Exit reason'], ['pnl', 'P&L', 'r'], ['pnl', 'Return', 'r']];
      mount(el, html`
        <div class="pad-x tab-body trades-wrap ${sel ? 'with-replay' : ''}">
          <div class="scroll-x" style="min-width:0">
            <div class="row wrap" style="gap:8px;padding-bottom:12px">
              <div class="chips">${chips([['all', 'All', cnt.all], ['win', 'Wins', cnt.win], ['loss', 'Losses', cnt.loss]], s.f, 'tf', { strong: true })}</div>
              <span class="grow"></span><span class="small mute hide-phone">Click a trade to replay it</span>
              <button type="button" class="btn btn-sm" data-act="csv">Export CSV</button>
            </div>
            ${R.length ? html`
              <div class="tr th trade-grid">${cols.map(([k, l, a], j) => html`<button type="button" class="${a === 'r' ? 'right' : ''} ${s.sk === k && j !== 1 && j !== 6 ? 'on' : ''}" data-act="tsort" data-k="${k}">${l}${s.sk === k && j !== 1 && j !== 6 ? (s.sd > 0 ? ' ↑' : ' ↓') : ''}</button>`)}</div>
              ${R.slice(0, lim).map(r => html`
                <div class="tr click trade-grid small ${sel && sel.i === r.i ? 'att' : ''}" data-act="pick" data-i="${r.i}" role="button" tabindex="0">
                  <span class="mono tiny mute">${r.i + 1}</span><span class="mono tiny">${dShort(r.o)}</span><span class="mono tiny">${dShort(r.c)}</span>
                  <span class="num tiny">${r.held || 'same day'}</span><span style="padding-left:14px" class="ell">${fixTag(r.t.repaired)} ${r.t.exit_reason || '—'}</span>
                  <span class="num ${tone(r.p)}">${inr(r.p)}</span><span class="num tiny ${tone(r.p)}">${pct(r.ret)}</span></div>`)}`
              : empty('', 'No trades yet. The first one appears here when a cycle closes.')}
            <div class="row small" style="padding-top:12px"><span class="mute">${R.length ? `Showing ${Math.min(lim, R.length)} of ${R.length} trades` : ''}</span><span class="grow"></span>
              ${R.length > lim ? html`<button type="button" class="btn btn-sm" data-act="more">Show 30 more</button>` : ''}</div>
          </div>
          ${replayPanel()}
        </div>`);
    }
    const off = actions(el, {
      tf: d => { st.set({ f: d.v }); paint(); },
      tsort: d => { const s = st.get(); st.set({ sk: d.k, sd: s.sk === d.k ? -s.sd : -1 }); paint(); },
      more: () => { lim += 30; paint(); },
      pick: d => { sel = { i: +d.i, pos: 0 }; loadReplay(+d.i); },
      closeReplay: () => { sel = null; replay = null; paint(); },
      step: d => { if (!replay || replay.error) return; sel.pos = Math.max(0, Math.min(replay.pts.length - 1, sel.pos + +d.d)); paint(); },
      jump: d => { sel.pos = +d.i; paint(); },
      csv: () => {
        const { R } = rows();
        const lines = ['trade,opened,closed,days_held,exit_reason,adjustments,pnl,return_pct'].concat(R.map(r =>
          [r.i + 1, r.o.toISOString(), r.c.toISOString(), r.held, `"${(r.t.exit_reason || '').replace(/"/g, '""')}"`, r.t.adjustments, r.p.toFixed(2), r.ret.toFixed(3)].join(',')));
        download(`${v().name.replace(/\s+/g, '_')}_trades.csv`, lines.join('\n'));
        say(`Exported ${plural(R.length, 'trade')} to CSV.`);
      },
    });
    const offSlide = on(el, 'input', '[data-slider]', (e, inp) => { sel.pos = +inp.value; paint(); const n = $('[data-slider]', el); if (n) n.focus(); });
    const offKey = on(el, 'keydown', '[data-act="pick"]', (e, row) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); row.click(); } });
    paint();
    return () => { off(); offSlide(); offKey(); };
  },
};
