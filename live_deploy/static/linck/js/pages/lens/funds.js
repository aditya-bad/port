// LinckLens › Funds: every mutual fund, what kind it is, Direct or
// Regular, Growth or IDCW, what it cost and is worth at its last NAV, and
// the SIPs feeding them.

import { html } from '../../core/dom.js';
import { inr, price, num, plural, tone, dShort, fromDay } from '../../core/format.js';
import { sect, stat } from '../../ui/components.js';
import { pctOr, inr0, signed, bars } from './parts.js';

const PLAN = { direct: 'Direct', regular: 'Regular' };
const OPTION = { growth: 'Growth', payout: 'IDCW payout', reinvestment: 'IDCW reinvest', idcw: 'IDCW' };
const FREQ = { monthly: 'a month', weekly: 'a week', fortnightly: 'a fortnight', quarterly: 'a quarter', daily: 'a day' };

function split(funds) {
  const total = funds.reduce((a, f) => a + f.value, 0) || 1;
  const by = k => funds.filter(f => f.plan === k).reduce((a, f) => a + f.value, 0);
  const d = by('direct'), r = by('regular'), u = total - d - r;
  return [['Direct', d], ['Regular', r], ['Plan not known', u]].filter(x => x[1] > 0.005)
    .map(([label, value]) => ({ label, value, weight: value / total * 100 }));
}

function kinds(funds) {
  const total = funds.reduce((a, f) => a + f.value, 0) || 1;
  const by = {};
  for (const f of funds) {
    const label = { mf_equity: 'Equity', mf_elss: 'Tax-saver (ELSS)', mf_index: 'Index', mf_hybrid: 'Hybrid', mf_debt: 'Debt', mf_liquid: 'Liquid', mf_fof: 'Fund of funds', mf_other: 'Other' }[f.asset] || 'Other';
    const x = by[label] || (by[label] = { label, value: 0, count: 0 });
    x.value += f.value; x.count += 1;
  }
  return Object.values(by).sort((a, b) => b.value - a.value).map(x => ({ ...x, weight: x.value / total * 100 }));
}

export function render(st) {
  const h = st.h, funds = h.mf, t = h.totals.mf;
  const sips = h.sips || [], active = sips.filter(s => s.status === 'ACTIVE');
  const sipOf = isin => active.filter(s => s.isin === isin).reduce((a, s) => a + s.monthly, 0);
  if (!funds.length && !sips.length) return html`<p class="small mute">No mutual funds or SIPs in this account.</p>`;
  return html`
    <div class="strip lens-strip">
      ${stat('In funds', inr0(t.value), plural(t.count, 'fund'))}
      ${stat('Gain on what you put in', signed(t.pnl, inr(t.pnl)), `${pctOr(t.pnl_pct)} on ${inr0(t.invested)}`)}
      ${stat('Going in by SIP', h.sip_monthly ? inr0(h.sip_monthly) : '—', h.sip_monthly ? `a month, ${plural(active.length, 'SIP')}` : 'no active SIP')}
      ${stat('Next SIP', active.length ? (() => { const n = active.filter(s => s.next).sort((a, b) => a.next.localeCompare(b.next))[0]; return n ? dShort(fromDay(n.next)) : '—'; })() : '—',
        active.length ? (() => { const n = active.filter(s => s.next).sort((a, b) => a.next.localeCompare(b.next))[0]; return n ? `${inr0(n.amount)} into ${n.fund || n.isin}` : ''; })() : '')}
    </div>
    ${funds.length ? html`<div class="lens-two">
      <div class="col" style="gap:12px;min-width:0">${sect('What kind', 'By value')}${bars(kinds(funds))}</div>
      <div class="col" style="gap:12px;min-width:0">${sect('Direct or Regular', 'Regular plans pay a distributor out of the fund every year')}${bars(split(funds))}</div>
    </div>` : ''}
    ${funds.length ? html`${sect('Funds', `${plural(t.count, 'fund')} · at each fund’s last NAV`)}
    <div class="tbl mf-tbl" role="table" data-lens="mf">
      <div class="tr th mf-row" role="row"><span>Fund</span><span class="num">Units</span><span class="num">Avg NAV</span><span class="num">NAV</span>
        <span class="num">Invested</span><span class="num">Value</span><span class="num">P&L</span><span class="num">Weight</span><span class="num">SIP</span></div>
      ${funds.map(f => html`<div class="tr mf-row" role="row" data-isin="${f.isin}">
        <span class="cell-name"><b>${f.fund}</b><span>${[f.amc, f.folio ? `folio ${f.folio}` : ''].filter(Boolean).join(' · ') || f.isin}</span>
          <span class="mf-tags">${f.plan ? html`<span class="tag ${f.plan === 'regular' ? 'warn' : ''}">${PLAN[f.plan] || f.plan}</span>` : ''}${f.option ? html`<span class="tag">${OPTION[f.option] || f.option}</span>` : ''}${f.scheme_type ? html`<span class="tag">${f.scheme_type}</span>` : ''}</span></span>
        <span class="num" data-l="Units">${num(f.units, 3)}</span>
        <span class="num" data-l="Avg NAV">${price(f.avg)}</span>
        <span class="num" data-l="NAV">${price(f.nav)}${f.nav_date ? html`<small>${dShort(fromDay(f.nav_date))}</small>` : ''}</span>
        <span class="num" data-l="Invested">${inr0(f.invested)}</span>
        <span class="num" data-l="Value">${inr0(f.value)}</span>
        <span class="num ${tone(f.pnl)}" data-l="P&L">${inr(f.pnl)}<small>${pctOr(f.pnl_pct)}</small></span>
        <span class="num" data-l="Weight">${(f.weight || 0).toFixed(1)}%</span>
        <span class="num" data-l="SIP">${sipOf(f.isin) ? `${inr0(sipOf(f.isin))}/mo` : '—'}</span>
      </div>`)}
      <div class="tr tot mf-row" role="row"><span>${plural(t.count, 'fund')}</span><span></span><span></span><span></span>
        <span class="num" data-l="Invested">${inr0(t.invested)}</span><span class="num" data-l="Value">${inr0(t.value)}</span>
        <span class="num ${tone(t.pnl)}" data-l="P&L">${inr(t.pnl)}</span><span></span><span class="num" data-l="SIP">${h.sip_monthly ? `${inr0(h.sip_monthly)}/mo` : ''}</span></div>
    </div>
    <p class="tiny mute lens-note">Kite gives each fund’s last NAV, not its history, so there’s no “today” or yearly line for funds. P&L is against your average purchase NAV.</p>` : ''}
    ${sips.length ? html`${sect('SIPs', `${active.length} active${sips.length > active.length ? ` · ${sips.length - active.length} not active` : ''}`)}
    <div class="tbl sip-tbl" role="table" data-lens="sips">
      <div class="tr th sip-row" role="row"><span>Fund</span><span class="num">Amount</span><span>How often</span><span>Next</span><span class="num">Done</span><span>Status</span></div>
      ${sips.map(s => html`<div class="tr sip-row ${s.status !== 'ACTIVE' ? 'off' : ''}" role="row" data-sip="${s.id}">
        <span class="cell-name"><b>${s.fund || s.isin}</b><span>${s.created ? `since ${dShort(fromDay(s.created))}` : ''}${s.step_up ? ` · step-up ${typeof s.step_up === 'object' ? Object.values(s.step_up).join(', ') : s.step_up}` : ''}</span></span>
        <span class="num" data-l="Amount">${inr0(s.amount)}</span>
        <span data-l="How often">${FREQ[s.frequency] || s.frequency || '—'}${s.day ? html` <span class="tiny mute">on the ${s.day}${['th', 'st', 'nd', 'rd'][(s.day % 10 > 3 || [11, 12, 13].includes(s.day % 100)) ? 0 : s.day % 10]}</span>` : ''}</span>
        <span data-l="Next">${s.next ? dShort(fromDay(s.next)) : '—'}</span>
        <span class="num" data-l="Done">${s.completed}</span>
        <span data-l="Status"><span class="tag ${s.status === 'ACTIVE' ? '' : 'warn'}">${s.status.toLowerCase()}</span></span>
      </div>`)}
    </div>` : ''}`;
}
