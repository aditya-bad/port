// One day end to end (DTT adjusted / advanced, #/d/:id/geek/session?d=
// YYYY-MM-DD): every leg at its strike, each side's premium and the
// comparison the adjustment rule makes, the day's P&L against its target,
// the index in its break-even band, every move and every check.

import { api } from '../../core/api.js';
import { notesPanel } from '../../ui/notes.js';
import { vixPanel, sessionOf } from './vix.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, dStamp } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, tone } from './ui.js';
import { mins } from './st.js';
import { linesChart } from './dtt.js';
import { exitName, EXIT_COLOR, legsLadder, dayName, ROLE } from './adj.js';

const OUT = { acted: 'Acted', no_action: 'No action', blocked: 'Blocked' };

export default {
  render(el, v, g) {
    let data = null;
    const want = g.params && g.params.d;
    let notes = null, vx = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Session')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Session')}${empty('Couldn’t load this day', data.message)}`); return; }
      const s = data.session;
      if (!s) {
        mount(el, html`${head('Session')}${empty(data.n ? 'There’s no session on that day' : 'No sessions yet', data.n ? 'It didn’t trade that day.' : 'One appears here after the first entry.')}
          ${data.n ? html`<p><a href="${g.href('sessions')}">← All sessions</a></p>` : ''}`);
        return;
      }
      if (!notes) notes = notesPanel({ deploymentId: v.id, day: s.day });
      if (!vx) vx = vixPanel({ start: sessionOf(s.entry_t).start, end: s.open ? null : sessionOf(s.entry_t).end, sub: 'that session · the same marks as above' });
      const S = data.series, cfg = data.settings;
      const marks = [{ t: s.entry_t, label: 'Sold', color: 'var(--acc)' },
        ...s.legs.filter(l => l.opened_by && l.opened_by !== 'entry_time_reached').map(l => ({ t: l.opened, label: l.opened_by === 'roll_open' ? 'roll' : 'adj', color: 'var(--ink)' })),
        ...s.legs.filter(l => l.closed_by === 'reversal_unwind').map(l => ({ t: l.closed, label: 'unwind', color: 'var(--g)' })),
        ...(s.exit_t ? [{ t: s.exit_t, label: exitName(s.reason), color: EXIT_COLOR[s.reason] || 'var(--mute)' }] : [])];
      const ratio = S.sides.filter(p => p[1] && p[2]).map(p => [p[0], Math.round(Math.min(p[1], p[2]) / Math.max(p[1], p[2]) * 1000) / 10]);
      mount(el, html`
        <div class="row wrap" style="gap:10px;align-items:baseline">
          <a href="${g.href('sessions')}" class="small">← All sessions</a><span class="grow"></span>
          ${data.prev ? html`<a class="btn btn-sm" href="${g.href('session')}?d=${data.prev}">← Older</a>` : ''}
          <span class="mono small">${data.no} of ${data.n}</span>
          ${data.next ? html`<a class="btn btn-sm" href="${g.href('session')}?d=${data.next}">Newer →</a>` : ''}
        </div>
        ${head(dayName(s.day), `The ${s.strike ? `${s.strike.toLocaleString('en-IN')} ` : ''}straddle${s.adjustments + s.rolls ? `, ${s.adjustments + s.rolls} leg${s.adjustments + s.rolls === 1 ? '' : 's'} added on the ${s.adjusted_side} side` : ', no adjustments'}${s.unwinds ? `, ${s.unwinds} unwound` : ''}.`, s.open ? 'open' : exitName(s.reason))}
        ${tiles([
          ['Strike', s.strike ? s.strike.toLocaleString('en-IN') : '—', [s.spot ? `index ${Math.round(s.spot).toLocaleString('en-IN')}` : '', s.dte != null ? `${s.dte}d to expiry` : ''].filter(Boolean).join(' · ')],
          ['Straddle sold', s.combined_entry == null ? '—' : price(s.combined_entry), s.breakeven ? `break-even ${Math.round(s.breakeven[0]).toLocaleString('en-IN')}–${Math.round(s.breakeven[1]).toLocaleString('en-IN')}` : ''],
          ['Moves', String(s.adjustments + s.rolls + s.unwinds), `${s.adjustments} adj${s.rolls ? ` · ${s.rolls} roll` : ''} · ${s.unwinds} unwind · ${s.max_legs} legs at most`],
          ['Ended by', s.open ? 'Open' : exitName(s.reason), s.exit_t ? `${s.exit_t.slice(11, 16)} · held ${mins(s.held_min)}` : ''],
          ['Original / extra', s.pnl_original == null ? '—' : inr(s.pnl_original), s.pnl_extra == null ? 'no extra legs closed' : `extra legs ${inr(s.pnl_extra)}`, tone(s.pnl_original)],
          ['P&L', s.pnl == null ? '—' : inr(s.pnl), s.target ? `target ${inr(s.target, { sign: false })}` : '', tone(s.pnl)],
        ], 6)}
        <div class="gk-panel"><h3>Every leg <span class="s">at its strike, from sold to bought back · faded: the extra legs</span></h3>${legsLadder(s) || empty('', 'No strikes recorded.')}</div>
        <div class="gk-panel"><h3>Each side’s premium <span class="s"><b class="leg-ce">CE</b> · <b class="leg-pe">PE</b> · every leg on the side added up</span></h3>
          ${S.sides.length > 1 ? linesChart([{ name: 'CE side', data: S.sides.map(p => [p[0], p[1]]), color: 'var(--c5)' }, { name: 'PE side', data: S.sides.map(p => [p[0], p[2]]), color: 'var(--c4)' }], { marks, label: 'Each side’s premium' }) : empty('', 'Needs every open leg’s price.')}</div>
        <div class="gk-panel"><h3>Smaller side ÷ bigger <span class="s">it adjusts at ${Math.round(cfg.trigger_ratio * 100)}% and unwinds back at 100%</span></h3>
          ${ratio.length > 1 ? linesChart([{ name: 'Smaller ÷ bigger', data: ratio, color: 'var(--ink)' }], { levels: [{ key: 'adjust', value: cfg.trigger_ratio * 100, color: 'var(--r)', dash: true }, { key: 'even', value: 100, color: 'var(--g)', dash: true }], marks, label: 'Smaller side over bigger side', fmt: x => `${Math.round(x)}%`, h: 200 }) : empty('', 'Needs both sides’ prices.')}</div>
        <div class="gk-panel"><h3>The day’s P&amp;L <span class="s">booked plus open · dashed: the target</span></h3>
          ${S.pnl.length > 1 ? linesChart([{ name: 'Cycle P&L (the day)', data: S.pnl, color: 'var(--ink)', width: 2 }], { levels: [...(s.target ? [{ key: 'target', value: s.target, color: 'var(--g)', dash: true }] : []), { key: '0', value: 0, color: 'var(--line2)' }], marks, label: 'The day’s P&L', fmt: x => inr(Math.round(x)), h: 200 }) : empty('', 'Needs every open leg’s price.')}</div>
        <div class="gk-panel"><h3>The index <span class="s">against the break-even band</span></h3>
          ${S.index.length > 1 ? linesChart([{ name: 'Index', data: S.index, color: 'var(--ink)' }], { levels: s.breakeven ? [{ key: 'BE low', value: s.breakeven[0], color: 'var(--r)', dash: true }, { key: 'BE high', value: s.breakeven[1], color: 'var(--r)', dash: true }, ...(s.strike ? [{ key: 'strike', value: s.strike, color: 'var(--acc)' }] : [])] : [], marks, label: 'The index', fmt: x => Math.round(x).toLocaleString('en-IN'), h: 200 }) : empty('', 'No index prices stored for this day.')}</div>
        ${vx.html()}
        <div class="gk-panel"><h3>Every leg <span class="s">in the order it was sold</span></h3>
          ${table([['Leg', 'minmax(170px,1.4fr)'], ['Role', '84px'], ['Sold', '120px', 'num'], ['Bought back', '150px', 'num'], ['Held', '70px', 'num'], ['P&L', '90px', 'num']],
            s.legs.map(l => [html`<b class="${l.side === 'CE' ? 'leg-ce' : 'leg-pe'}">${l.symbol}</b>`, ROLE(l.role), html`<span class="mono small">${price(l.entry)} ${l.opened.slice(11, 16)}</span>`,
              l.closed ? html`<span class="mono small">${price(l.exit)} ${l.closed.slice(11, 16)} <span class="mute">${l.closed_by === 'reversal_unwind' ? 'unwind' : l.closed_by === 'roll_close' ? 'roll' : exitName(l.closed_by).toLowerCase()}</span></span>` : 'open',
              l.closed ? mins((new Date(l.closed) - new Date(l.opened)) / 60000) : '—', html`<span class="${tone(l.pnl)}">${l.pnl == null ? '—' : inr(l.pnl)}</span>`]), { minw: 760 })}</div>
        ${notes.html()}
        <div class="gk-panel"><h3>Checks meanwhile <span class="s">what the strategy noted from 30 minutes before the entry</span></h3>
          ${data.decisions.length ? table([['When', '120px'], ['Rule', '140px'], ['Outcome', '110px'], ['What it saw', 'minmax(0,1fr)']],
            data.decisions.map(d => [html`<span class="mono tiny">${dStamp(d.t)}</span>`, html`<b>${d.label}</b>`, html`<span class="oc ${d.outcome}">${OUT[d.outcome] || d.outcome}</span>`,
              html`<span class="col" style="gap:2px"><span>${d.summary}</span>${d.why ? html`<span class="mono tiny mute">${d.why}</span>` : ''}</span>`]), { minw: 640 })
            : empty('', 'Nothing noted that day.')}</div>`);
      notes.attach(el);
      vx.attach(el, marks);
    }
    const stop = poll(() => api.geek(g.id, `session${want ? `?d=${encodeURIComponent(want)}` : ''}`), 60000, d => { data = d; paint(); });
    return () => { stop(); if (notes) notes.destroy(); if (vx) vx.destroy(); };
  },
};
