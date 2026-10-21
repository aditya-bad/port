// The open legs against the index (GET /v2/deployments/:id/underlying):
// the spot, the future that prices them and the basis between them, each
// leg's distance from both, the book's delta per point of the future, and
// what a move of the future would do to it. On the positions page.

import { html } from '../core/dom.js';
import { inr, price, tone, dStamp } from '../core/format.js';
import { sect, empty } from './components.js';

const sgn = (v, dp = 0) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: dp, minimumFractionDigits: dp })}`);

// The book's delta in words: "Loses ₹42 for every point the future rises".
export function deltaText(d) {
  if (d == null) return null;
  if (Math.abs(d) < 0.5) return 'About flat to small moves of the future';
  return `${d < 0 ? 'Loses' : 'Makes'} ${inr(Math.abs(d), { sign: false })} for every point the future rises`;
}

// Where between −max and +max a scenario's bar sits (0..50 % each side).
export const barPct = (v, max) => (max ? Math.min(50, Math.abs(v) / max * 50) : 0);

export function underlyingPanel(u) {
  // always one wrapper, so a refresh replaces it in place
  if (u === null) return html`<div data-underlying>${sect('Against the index')}<div class="skel" style="height:140px"></div></div>`;
  if (!u || u instanceof Error) return html`<div data-underlying></div>`;
  if (!u.available) return html`<div data-underlying>${sect('Against the index')}${empty('', u.why)}</div>`;
  const f = u.future, b = u.basis, n = u.net;
  const tile = (l, v, s, cls = '') => html`<div class="ul-tile"><span>${l}</span><b class="${cls}">${v}</b>${s ? html`<i>${s}</i>` : ''}</div>`;
  const maxS = n ? Math.max(1, ...n.scenarios.map(s => Math.abs(s.pnl))) : 1;
  return html`<div data-underlying>
    ${sect('Against the index', 'options are priced off the future, not the spot')}
    <div class="ul-tiles">
      ${tile(u.index, price(u.spot.price), u.spot.change_today != null ? `${sgn(u.spot.change_today)} today` : '')}
      ${f ? tile(`Future · ${f.symbol}`, price(f.price), `${f.change_today != null ? `${sgn(f.change_today)} today` : ''}${f.source === 'stored' && f.as_of ? ` · as of ${dStamp(f.as_of)}` : ''}`) : tile('Future', '—', u.why || '')}
      ${b ? tile('Basis', `${sgn(b.pts)} pts`, `${b.pct.toFixed(2)}% · ${b.annual_pct != null ? `${b.annual_pct.toFixed(1)}% a year · ` : ''}${b.dte} days${b.change_today != null ? ` · ${sgn(b.change_today)} today` : ''}`) : ''}
      ${n ? tile('Delta', `${inr(n.delta)}/pt`, deltaText(n.delta), tone(n.delta)) : ''}
    </div>
    ${u.legs.length ? html`<div class="ul-legs">
      <div class="ul-r th"><span>Leg</span><span>From ${u.index}</span><span>From future</span><span>IV</span><span>Delta ₹/pt</span></div>
      ${u.legs.map(l => html`<div class="ul-r"><b class="mono ${l.kind === 'CE' ? 'leg-ce' : l.kind === 'PE' ? 'leg-pe' : ''}">${l.kind && l.strike ? `${l.kind} ${l.strike}` : l.symbol} <span class="tiny mute">${l.side === 'short' ? 'sold' : 'bought'}</span></b>
        <span class="mono">${sgn(l.from_spot)}</span><span class="mono"><b>${sgn(l.from_fut)}</b></span>
        <span class="mono">${l.iv != null ? `${l.iv.toFixed(1)}%` : '—'}</span><span class="mono ${tone(l.delta)}">${l.delta != null ? inr(l.delta, { dp: 1 }) : '—'}</span></div>`)}
    </div>` : ''}
    ${n && n.legs_valued ? html`<div class="ul-sc" aria-label="If the future moves">
      <span class="tiny mute">If the future moves (each leg revalued, same volatility${n.legs_valued < n.legs ? `; ${n.legs_valued} of ${n.legs} legs` : ''})</span>
      <div class="ul-bars">${n.scenarios.map(s => html`<div class="ul-b" data-tip="${`Future ${sgn(s.move)} pts\nBook  ${inr(s.pnl)}`}">
        <span class="mono tiny">${sgn(s.move)}</span>
        <div class="ul-track"><i class="${s.pnl >= 0 ? 'up' : 'dn'}" style="${s.pnl >= 0 ? 'left:50%' : 'right:50%'};width:${barPct(s.pnl, maxS).toFixed(1)}%"></i></div>
        <b class="mono tiny ${tone(s.pnl)}">${inr(s.pnl)}</b></div>`)}</div>
    </div>` : ''}
  </div>`;
}
