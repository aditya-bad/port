// One straddle end to end (DTT, #/d/:id/geek/straddle?s=<id>): each
// leg's premium against its stop, the combined premium against the
// target, the index through the day, how it ended, and every check the
// strategy noted meanwhile.

import { api } from '../../core/api.js';
import { notesPanel } from '../../ui/notes.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, dStamp, dFull } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { head, tiles, table, poll, tone } from './ui.js';
import { mins } from './st.js';
import { exitName, EXIT_COLOR, linesChart } from './dtt.js';

const OUT = { acted: 'Acted', no_action: 'No action', blocked: 'Blocked' };
const kv = vals => (vals ? Object.entries(vals).filter(([, v]) => v != null && typeof v !== 'object')
  .map(([k, v]) => html`<span><span class="mute">${k.replace(/_/g, ' ')}</span> <b class="mono">${typeof v === 'number' ? v.toLocaleString('en-IN') : String(v)}</b></span>`) : []);

export default {
  render(el, v, g) {
    let data = null;
    const want = g.params && g.params.s;
    let notes = null;
    function paint() {
      if (data === null) { mount(el, html`${head('Straddle')}${loading(6)}`); return; }
      if (data instanceof Error) { mount(el, html`${head('Straddle')}${empty('Couldn’t load this straddle', data.message)}`); return; }
      const r = data.straddle;
      if (!r) {
        mount(el, html`${head('Straddle')}${empty(data.n ? 'There’s no such straddle' : 'No straddles yet', data.n ? 'It may be from another deployment.' : 'One appears here after the first entry.')}
          ${data.n ? html`<p><a href="${g.href('straddles')}">← All straddles</a></p>` : ''}`);
        return;
      }
      if (!notes) notes = notesPanel({ deploymentId: v.id, day: r.day });
      const S = data.series, L = data.lines;
      const marks = [{ t: r.entry_t, label: 'Sold', color: 'var(--acc)' }];
      if (r.exit_t) marks.push({ t: r.exit_t, label: exitName(r.reason), color: EXIT_COLOR[r.reason] || 'var(--mute)' });
      mount(el, html`
        <div class="row wrap" style="gap:10px;align-items:baseline">
          <a href="${g.href('straddles')}" class="small">← All straddles</a><span class="grow"></span>
          ${data.prev ? html`<a class="btn btn-sm" href="${g.href('straddle')}?s=${data.prev}">← Older</a>` : ''}
          <span class="mono small">${data.no} of ${data.n}</span>
          ${data.next ? html`<a class="btn btn-sm" href="${g.href('straddle')}?s=${data.next}">Newer →</a>` : ''}
        </div>
        ${head(`Straddle ${data.no}`, `${dFull(new Date(r.entry_t))}: the ${r.strike ? `${r.strike.toLocaleString('en-IN')} ` : ''}straddle, sold ${dStamp(r.entry_t).slice(-5)}${r.exit_t ? `, bought back ${dStamp(r.exit_t).slice(-5)}` : ', still open'}.`, r.open ? 'open' : exitName(r.reason))}
        ${tiles([
          ['Strike', r.strike ? r.strike.toLocaleString('en-IN') : '—', [r.spot ? `index ${Math.round(r.spot).toLocaleString('en-IN')}` : '', r.dte != null ? `${r.dte}d to expiry` : ''].filter(Boolean).join(' · ')],
          ['CE', r.ce ? `${price(r.ce.entry)} → ${r.ce.exit == null ? '…' : price(r.ce.exit)}` : '—', r.ce && r.ce.pnl != null ? inr(r.ce.pnl) : '', r.ce ? tone(r.ce.pnl) : ''],
          ['PE', r.pe ? `${price(r.pe.entry)} → ${r.pe.exit == null ? '…' : price(r.pe.exit)}` : '—', r.pe && r.pe.pnl != null ? inr(r.pe.pnl) : '', r.pe ? tone(r.pe.pnl) : ''],
          ['Combined', r.combined_entry == null ? '—' : price(r.combined_entry), r.combined_exit == null ? 'sold' : `→ ${price(r.combined_exit)} · ${r.decay_pct > 0 ? 'fell' : 'rose'} ${Math.abs(r.decay_pct)}%`],
          ['Ended by', r.open ? 'Open' : exitName(r.reason), r.stop_leg ? `the ${r.stop_leg} leg · held ${mins(r.held_min)}` : `held ${mins(r.held_min)}`],
          ['P&L', r.pnl == null ? '—' : inr(r.pnl), r.qty ? `${r.qty} qty a leg` : '', tone(r.pnl)],
        ], 6)}
        <div class="gk-panel"><h3>Each leg against its stop <span class="s"><b class="leg-ce">CE</b> · <b class="leg-pe">PE</b> · dashed: the stops</span></h3>
          ${S.CE.length > 1 || S.PE.length > 1 ? linesChart([{ name: 'CE', data: S.CE, color: 'var(--c5)' }, { name: 'PE', data: S.PE, color: 'var(--c4)' }],
            { levels: Object.entries(L.stops).map(([k, val]) => ({ key: `${k} stop`, value: val, color: k === 'CE' ? 'var(--c5)' : 'var(--c4)', dash: true })), marks, label: 'Each leg’s premium' })
            : empty('', 'No premium prices stored for these legs.')}</div>
        <div class="gk-panel"><h3>The combined premium <span class="s">CE + PE · dashed: what it was sold at and the target</span></h3>
          ${S.combined.length > 1 ? linesChart([{ name: 'Combined', data: S.combined, color: 'var(--ink)', width: 2 }],
            { levels: [{ key: 'sold', value: r.combined_entry, color: 'var(--acc)', dash: true }, { key: 'target', value: L.target, color: 'var(--g)', dash: true }], marks, label: 'Combined premium' })
            : empty('', 'Needs both legs’ prices.')}</div>
        <div class="gk-panel"><h3>The index <span class="s">through the straddle</span></h3>
          ${S.index.length > 1 ? linesChart([{ name: 'Index', data: S.index, color: 'var(--ink)' }], { levels: r.strike ? [{ key: 'strike', value: r.strike, color: 'var(--acc)', dash: true }] : [], marks, label: 'The index', fmt: x => Math.round(x).toLocaleString('en-IN'), h: 200 })
            : empty('', 'No index prices stored for this day.')}</div>
        ${r.exit_values ? html`<div class="gk-panel"><h3>What ended it: ${exitName(r.reason)}</h3><p class="small" style="display:flex;flex-wrap:wrap;gap:6px 16px">${kv(r.exit_values)}</p></div>` : ''}
        ${notes.html()}
        <div class="gk-panel"><h3>Checks meanwhile <span class="s">what the strategy noted from 30 minutes before the entry</span></h3>
          ${data.decisions.length ? table([['When', '120px'], ['Rule', '150px'], ['Outcome', '110px'], ['What it saw', 'minmax(0,1fr)']],
            data.decisions.map(d => [html`<span class="mono tiny">${dStamp(d.t)}</span>`, html`<b>${d.label}</b>`, html`<span class="oc ${d.outcome}">${OUT[d.outcome] || d.outcome}</span>`,
              html`<span class="col" style="gap:2px"><span>${d.summary}</span>${d.why ? html`<span class="mono tiny mute">${d.why}</span>` : ''}</span>`]), { minw: 640 })
            : empty('', 'Nothing noted around this straddle.')}</div>`);
      notes.attach(el);
    }
    const stop = poll(() => api.geek(g.id, `straddle${want ? `?s=${encodeURIComponent(want)}` : ''}`), 60000, d => { data = d; paint(); });
    return () => { stop(); if (notes) notes.destroy(); };
  },
};
