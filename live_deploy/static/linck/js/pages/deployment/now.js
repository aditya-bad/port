// Now: what it holds, this cycle's timeline, and the strategy's own state
// (each value tagged LIVE or CHECKPOINT by where it comes from).

import { api } from '../../core/api.js';
import { html, mount, $, setText, setTone } from '../../core/dom.js';
import { inr, price, dStamp, hhmm, plural, tone } from '../../core/format.js';
import { pnlOf } from '../../core/basis.js';
import { listen } from '../../core/store.js';
import * as book from '../../data/book.js';
import { sect, empty, loading } from '../../ui/components.js';
import { legHead, legRow, liveTag, tickLegs } from '../../ui/legs.js';

const GAP = 90 * 1000;

// Fills since the cycle began → Entry / Adjust / Exit rows.
function timeline(lots, start, v) {
  const L = lots.filter(l => new Date(l.executed_at) >= start).sort((a, b) => new Date(a.executed_at) - new Date(b.executed_at));
  const groups = [];
  for (const l of L) {
    const t = new Date(l.executed_at).getTime();
    const g = groups[groups.length - 1];
    if (g && t - g.last <= GAP) { g.fills.push(l); g.last = t; } else groups.push({ t, last: t, fills: [l] });
  }
  const rows = groups.map((g, i) => {
    const reasons = [...new Set(g.fills.map(f => f.reason).filter(Boolean))].join(', ');
    const kind = i === 0 ? 'Entry' : /eod_rollout/.test(reasons) ? 'EOD roll-out' : /exit|stop|target|checkpoint|flatten|close/i.test(reasons) ? 'Exit' : 'Adjust';
    const text = g.fills.map(f => `${f.action === 'sell' ? 'Sold' : 'Bought'} ${Number(f.qty)} ${f.symbol} @ ${price(f.price)}`).join('; ');
    return { t: dStamp(g.t), k: kind, x: text, why: reasons };
  }).reverse();
  const last = v.trades[v.trades.length - 1];
  if (last) {
    const p = pnlOf(last);
    rows.push({ t: dStamp(last.closed_at), k: 'Exit', x: `Previous cycle closed${last.exit_reason ? ` · ${last.exit_reason}` : ''}`, val: inr(p), tone: tone(p) });
  }
  return rows;
}

export default {
  render(el, v0) {
    let lots = null, openPos = null, status = null;
    function paint() {
      const v = book.find(v0.id) || v0;
      const x = book.live(v);
      const start = openPos && openPos.length ? new Date(Math.min(...openPos.map(p => new Date(p.episode_opened_at || p.opened_at)))) : null;
      const tl = lots && start ? timeline(lots, start, v) : lots ? timeline([], new Date(), v) : null;
      mount(el, html`
        <div class="pad-x tab-body grid-side">
          <div class="stack" style="gap:28px">
            <div class="scroll-x">
              ${sect('Holding now', v.legs.length ? plural(v.legs.length, 'leg') : 'no open legs', html`${liveTag(v.legs)} <span class="mono ${tone(x.unreal)}" data-unr>${inr(x.unreal)}</span>`)}
              ${v.legs.length ? html`${legHead()}${v.legs.map(legRow)}`
                : empty('', v.rawStatus === 'stopped' ? 'Stopped and flat.' : `Flat. Waiting for the next entry${v.raw.config && v.raw.config.entry_time ? ` at ${v.raw.config.entry_time}` : ''}.`)}
            </div>
            <div>
              ${sect(v.legs.length ? 'This cycle' : 'Last cycle')}
              ${tl === null ? loading(3) : tl.length ? tl.map(r => html`
                <div class="tr cycle-grid small"><span class="mono tiny mute">${r.t}</span><b style="${r.k === 'EOD roll-out' ? 'color:var(--c5)' : ''}">${r.k}</b>
                  <span class="mono tiny mute pretty">${r.x}${r.why ? html`<br><span class="b" style="font-family:var(--f-body)">${r.why}</span>` : ''}</span>
                  <span class="mono tiny right ${r.tone || ''}">${r.val || ''}</span></div>`) : empty('', 'No cycle yet.')}
            </div>
          </div>
          <div>
            ${sect('Strategy state', null, html`<span class="tiny mute">${status ? (status.source === 'live' ? 'live from the running strategy' : status.source === 'persisted' ? 'last saved by the strategy' : 'not available') : ''}</span>`)}
            ${status === null ? loading(4) : status.fields && status.fields.length ? status.fields.map(f => html`
              <div class="state-row">
                <div class="row"><b class="grow">${f.label}</b><span class="fresh ${status.source === 'live' ? '' : 'ck'}">${status.source === 'live' ? 'LIVE' : 'CHECKPOINT'}</span></div>
                <span class="mono state-v">${fmtVal(f.value)}</span>
              </div>`) : empty('', status.source === 'unavailable' ? 'This strategy doesn’t report its state.' : 'No state yet. It appears once the strategy has run.')}
            <p class="tiny mute" style="padding-top:10px;margin:0">LIVE values recompute every tick. CHECKPOINT values are what the strategy last saved and may lag until it saves again.</p>
          </div>
        </div>`);
    }
    function fmtVal(val) {
      if (val == null) return '—';
      if (typeof val === 'number') return Number.isInteger(val) ? val.toLocaleString('en-IN') : val.toLocaleString('en-IN', { maximumFractionDigits: 2 });
      if (typeof val === 'boolean') return val ? 'Yes' : 'No';
      if (typeof val === 'object') return JSON.stringify(val);
      return String(val);
    }
    Promise.all([
      api.lots(v0.id, 200).then(r => { lots = r.lots || []; }).catch(() => { lots = []; }),
      api.positions(v0.id, 'open').then(r => { openPos = r; }).catch(() => { openPos = []; }),
      api.strategyStatus(v0.id).then(r => { status = r; }).catch(() => { status = { fields: [], source: 'unavailable' }; }),
    ]).then(paint);
    paint();
    const offs = [listen('ticks', () => {
      const v = book.find(v0.id);
      if (!v) return;
      tickLegs(el, v.legs);
      const u = $('[data-unr]', el), x = book.live(v);
      if (u) { setText(u, inr(x.unreal)); setTone(u, tone(x.unreal)); }
    })];
    const iv = setInterval(() => api.strategyStatus(v0.id).then(r => { status = r; paint(); }).catch(() => {}), 30000);
    // the Live tag turns stale when ticks stop, even with nothing to repaint
    const iv2 = setInterval(() => { const v = book.find(v0.id); if (v) tickLegs(el, v.legs); }, 5000);
    return () => { offs.forEach(f => f()); clearInterval(iv); clearInterval(iv2); };
  },
};
