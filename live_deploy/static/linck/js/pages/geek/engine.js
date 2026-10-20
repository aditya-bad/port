// Engine: every rule in the order the strategy checks it, how close each
// is to acting, the next move, where the strikes sit, the side balance
// over this cycle, price freshness and the state the strategy keeps.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { inr, price, hhmmss, hhmm } from '../../core/format.js';
import { empty, loading } from '../../ui/components.js';
import { hasView } from './views.js';
import { stPanel } from './st.js';
import { ddPanel } from './dtt.js';
import { head, nextMove, gauge, stateChip, table, tiles, poll, svg, path, money, tone } from './ui.js';

function ruler(k) {
  const pts = [k.lower_be, k.upper_be, k.pe, k.ce, k.spot].filter(x => x != null);
  const span = Math.max(...pts) - Math.min(...pts);
  const lo = Math.min(...pts) - span * 0.06, hi = Math.max(...pts) + span * 0.06;
  const W = 560, x = v => 16 + (v - lo) / (hi - lo) * (W - 32);
  let s = `<rect x="${x(k.lower_be)}" y="40" width="${x(k.upper_be) - x(k.lower_be)}" height="26" fill="var(--gs)"/>`;
  s += `<line x1="16" x2="${W - 16}" y1="53" y2="53" stroke="var(--line2)"/>`;
  const mark = (v, c, l) => `<line x1="${x(v)}" x2="${x(v)}" y1="30" y2="66" stroke="${c}" stroke-width="3"/><text class="lab" x="${x(v)}" y="22" text-anchor="middle" style="fill:${c}">${l}</text>`;
  s += mark(k.pe, 'var(--c4)', `PE ${k.pe.toLocaleString('en-IN')}`) + mark(k.ce, 'var(--c5)', `CE ${k.ce.toLocaleString('en-IN')}`);
  if (k.spot != null) {
    s += `<path d="M${x(k.spot)} 44 l-6 -10 h12z" fill="var(--ink)"/><line x1="${x(k.spot)}" x2="${x(k.spot)}" y1="44" y2="66" stroke="var(--ink)" stroke-width="2"/>`;
    s += `<text class="lab" x="${x(k.spot)}" y="100" text-anchor="middle">spot ${Math.round(k.spot).toLocaleString('en-IN')}</text>`;
  }
  s += `<text x="${x(k.lower_be)}" y="84" text-anchor="middle">BE ${Math.round(k.lower_be).toLocaleString('en-IN')}</text>`;
  s += `<text x="${x(k.upper_be)}" y="84" text-anchor="middle">BE ${Math.round(k.upper_be).toLocaleString('en-IN')}</text>`;
  return svg(W, 108, s, 'Spot against the short strikes and break-evens');
}

function balance(series, eod, cont) {
  const W = 560, H = 170, n = series.length;
  const lo = Math.min(0.3, ...series.map(p => p.ratio)) - 0.02;
  const y = r => 10 + (1 - (r - lo) / (1 - lo)) * 130, x = i => 34 + (n === 1 ? 0.5 : i / (n - 1)) * (W - 44);
  let s = '';
  if (eod && eod.band) s += `<rect x="34" y="${y(eod.band[1])}" width="${W - 44}" height="${y(eod.band[0]) - y(eod.band[1])}" fill="var(--gs)"/>`;
  [0.4, 0.6, 0.8, 1].filter(v => v >= lo).forEach(v => { s += `<line x1="34" x2="${W - 10}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="28" y="${y(v) + 3}" text-anchor="end">${v * 100}%</text>`; });
  if (eod) s += `<line x1="34" x2="${W - 10}" y1="${y(eod.mark)}" y2="${y(eod.mark)}" stroke="var(--r)" stroke-dasharray="4 3"/><text class="lab" x="${W - 12}" y="${y(eod.mark) - 4}" text-anchor="end" style="fill:var(--r)">EOD floor ${Math.round(eod.mark * 100)}%</text>`;
  if (cont) s += `<line x1="34" x2="${W - 10}" y1="${y(cont.mark)}" y2="${y(cont.mark)}" stroke="var(--r)" stroke-width="1.5"/><text class="lab" x="${W - 12}" y="${y(cont.mark) - 4}" text-anchor="end" style="fill:var(--r)">roll now ${Math.round(cont.mark * 100)}%</text>`;
  s += `<path d="${path(series.map((p, i) => [x(i), y(p.ratio)]))}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>`;
  const L = series[n - 1];
  s += `<circle cx="${x(n - 1)}" cy="${y(L.ratio)}" r="4" fill="var(--acc)"/>`;
  s += `<text x="34" y="${H - 6}">${hhmm(series[0].t)} ${new Date(series[0].t).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })}</text>`;
  s += `<text x="${W - 10}" y="${H - 6}" text-anchor="end">${hhmm(L.t)} · ${Math.round(L.ratio * 100)}%</text>`;
  return svg(W, H, s, 'Smaller side over bigger side through this cycle');
}

const show = val => (val == null ? '—' : typeof val === 'boolean' ? (val ? 'Yes' : 'No') : typeof val === 'object' ? JSON.stringify(val) : String(val));
const age = s => (s == null ? '—' : s < 60 ? `${s.toFixed(1)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`);
const STATE_LABEL = {
  cycle_id: 'Cycle', contract_expiry: 'Locked expiry', cycle_realized_pnl: 'Realized this cycle', converged: 'Converged',
  convergence_premium: 'Premium at convergence', adjustments_used: 'Adjustment legs opened', eod_checked_today: 'EOD check done today',
  entered_ever: 'Has entered', last_flatten: 'Last flatten', hedges: 'Hedges open',
};

// The rules, the strikes and the legs: also shown on a running cycle's own page.
export function rulesBlock(eng) {
  const rules = eng.rules || [];
  return html`${nextMove(eng)}
    ${rules.length ? html`<div class="gk-tbl gk-rules" style="--minw:0px">
      <div class="tr th"><span>#</span><span>Rule</span><span>Distance to trigger</span><span>Reading</span><span>State</span></div>
      ${rules.map((r, i) => html`<div class="tr ${r.state === 'armed' ? 'att' : ''}" data-rule="${r.key}">
        <span class="prio">${i + 1}</span>
        <div class="cell-name"><b>${r.label}</b><span title="${r.desc}">${r.desc}</span></div>
        <div class="g">${gauge(r)}<div class="gauge-l"><span>${r.left || ''}</span><span>${r.mid || ''}</span><span>${r.right || ''}</span></div></div>
        <span class="mono small rd">${r.reading}</span>
        ${stateChip(r)}</div>`)}
    </div>` : ''}`;
}
export function strikesBlock(k) {
  return html`${ruler(k)}
    ${tiles([
      ['To CE strike', k.spot != null ? `+${Math.round(k.ce - k.spot)}` : '—', k.spot != null ? `${((k.ce - k.spot) / k.spot * 100).toFixed(2)}%` : ''],
      ['To PE strike', k.spot != null ? `−${Math.round(k.spot - k.pe)}` : '—', k.spot != null ? `${((k.spot - k.pe) / k.spot * 100).toFixed(2)}%` : ''],
      ['Upper break-even', Math.round(k.upper_be).toLocaleString('en-IN'), k.spot != null ? `+${((k.upper_be - k.spot) / k.spot * 100).toFixed(2)}%` : ''],
      ['Lower break-even', Math.round(k.lower_be).toLocaleString('en-IN'), k.spot != null ? `−${((k.spot - k.lower_be) / k.spot * 100).toFixed(2)}%` : ''],
    ])}`;
}
export function legsBlock(eng) {
  const legs = table([['Leg', 'minmax(176px,1.6fr)'], ['Role', '110px'], ['Entry', '80px', 'num'], ['LTP', '80px', 'num'], ['Last tick', '80px', 'num'], ['P&L', '96px', 'num']],
    eng.legs.map(l => [html`<b class="${l.opt === 'CE' ? 'leg-ce' : l.opt === 'PE' ? 'leg-pe' : ''}">${l.symbol}</b>`, l.role || '—', price(l.entry), price(l.ltp), age(l.tick_age_s), html`<span class="${tone(l.pnl)}">${money(l.pnl)}</span>`]), { minw: 620 });
  return html`${eng.legs.length ? legs : empty('', 'No open legs.')}
    ${eng.hedges && eng.hedges.length ? html`<p class="gk-note">Hedges: ${eng.hedges.map(h => `${h.symbol} @${price(h.entry)}`).join(' · ')}</p>` : ''}
    ${eng.running && eng.quote_guard !== false && eng.rules && eng.rules.length ? html`<p class="gk-note">${eng.cooldowns.length ? html`<b>Waiting on quotes:</b> ${eng.cooldowns.map(c => `${c.rule.replace(/_/g, ' ')} until ${hhmmss(c.until)}`).join(' · ')}` : 'No trigger is waiting on a quote check.'} Decisions wait until every leg has traded this session.</p>` : ''}`;
}

export default {
  render(el, v, g) {
    let eng = g.engine(), prem = null, at = new Date();
    function paint() {
      if (!eng) { mount(el, html`${head('Engine')}${loading(5)}`); return; }
      const k = eng.strikes, rules = eng.rules || [];
      const eod = rules.find(r => r.key === 'eod_gap_check'), cont = rules.find(r => r.key === 'continuous_50pct_trigger');
      mount(el, html`
        ${head('Engine', rules.length ? 'Every rule the strategy checks, in the order it checks them, and how close each one is to acting.' : 'Where the strategy stands: its own readings, the open legs and what it remembers.', `updated ${hhmmss(at)}`)}
        ${rulesBlock(eng)}
        ${k ? html`<div class="gk-two even">
          <div class="gk-panel"><h3>Where the strikes sit <span class="s">credit ${k.credit.toFixed(1)} pts</span></h3>
            ${strikesBlock(k)}</div>
          <div class="gk-panel"><h3>Side balance, this cycle <span class="s">smaller side ÷ bigger side, every 5 minutes · shaded: where rolls aim</span></h3>
            ${prem === null ? loading(3) : prem.series && prem.series.length ? balance(prem.series, eod, cont) : empty('', 'Appears after the first 5-minute snapshot of this cycle.')}</div>
        </div>` : ''}
        ${eng.supertrend ? stPanel(eng.supertrend) : ''}
        ${eng.straddle ? ddPanel(eng.straddle) : ''}
        ${eng.fields && eng.fields.length && !eng.supertrend ? html`<div class="gk-panel"><h3>Strategy readings <span class="s">${eng.fields_source === 'live' ? 'live, from the running strategy' : 'last saved, while not running'}</span></h3>
          ${tiles(eng.fields.map(f => [f.label, show(f.value), '']))}</div>` : ''}
        <div class="${eng.state ? 'gk-two' : ''}">
          <div class="gk-panel"><h3>Legs &amp; price feed <span class="s">${eng.running ? 'live' : 'not running: no live prices'}</span></h3>
            ${legsBlock(eng)}
          </div>
          ${eng.state || rules.length ? html`<div class="gk-panel"><h3>Strategy state <span class="s">what it remembers</span></h3>
            ${eng.state ? table([['', 'minmax(0,1fr)'], ['', 'minmax(0,1fr)', 'num']],
              Object.entries(eng.state).map(([key, val]) => [STATE_LABEL[key] || key.replace(/_/g, ' '), key === 'cycle_realized_pnl' ? inr(val) : show(val)]), { minw: 0, cls: 'kv' })
              : empty('', 'Only while the deployment runs.')}
          </div>` : ''}
        </div>`);
    }
    const off = g.onEngine(d => { eng = d; at = new Date(); paint(); });
    const stop = hasView(v.strategy, 'premium') ? poll(() => api.geek(g.id, 'premium'), 60000, d => { prem = d instanceof Error ? { series: [] } : d; paint(); }) : () => {};
    paint();
    return () => { off(); stop(); };
  },
};
