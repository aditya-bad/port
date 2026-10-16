// Tools — calculators (the classic UI's verified engine in
// static/js/calculators.js) and the live price subscriptions.

import { api } from '../core/api.js';
import { html, mount, $, actions, on, debounce } from '../core/dom.js';
import { inr, inrS, pct, price, plural, tone } from '../core/format.js';
import { ui } from '../core/store.js';
import { href } from '../core/router.js';
import * as book from '../data/book.js';
import { seg, empty, loading } from '../ui/components.js';
import { say } from '../ui/toast.js';

// eslint-disable-next-line no-undef
const Calc = () => Calculators;
const TABS = [['brokerage', 'Brokerage'], ['sip', 'SIP'], ['stepup', 'Step-up SIP'], ['lumpsum', 'Lumpsum + SIP'], ['cagr', 'CAGR'], ['instruments', 'Instruments']];
const st = ui('tools', { seg: 'options', bp: '120', sp: '98', q: '75', sip: '10000', sr: '12', sy: '15', su: '10', ls: '500000', lr: '12', ly: '10', lsip: '5000', lsu: '10', cs: '100000', ce: '250000', cy: '5' });
const n = k => { const x = parseFloat(String(st.get()[k]).replace(/,/g, '')); return Number.isFinite(x) ? x : 0; };

function input(k, label, { pre = '', suf = '', hint = '', ok = x => x > 0 } = {}) {
  const good = ok(n(k));
  return html`<label class="col" style="gap:6px"><b class="small">${label}</b>
    <span class="input-group lg ${good ? '' : 'bad'}">${pre ? html`<span>${pre}</span>` : ''}<input data-k="${k}" value="${st.get()[k]}" inputmode="decimal" aria-label="${label}">${suf ? html`<span>${suf}</span>` : ''}</span>
    <span class="hint ${good ? '' : 'bad'}">${good ? hint : 'Enter a valid number.'}</span></label>`;
}
function bars(rows) {
  const mx = Math.max(1, ...rows.map(r => r.v)), step = Math.max(1, Math.ceil(rows.length / 15));
  const R = rows.filter((_, i) => (i + 1) % step === 0 || i === rows.length - 1);
  return html`<div>
    <div class="row tiny mute" style="gap:18px;padding-bottom:8px"><span class="row" style="gap:6px"><i style="width:10px;height:10px;background:var(--line)"></i>Invested</span><span class="row" style="gap:6px"><i style="width:10px;height:10px;background:var(--ink)"></i>Value</span></div>
    <div class="tbars">${R.map(r => html`<div data-tip="Year ${r.y}\nValue  ${inr(r.v, { sign: false })}\nInvested  ${inr(r.inv, { sign: false })}"><i class="grow-y" style="height:${r.v / mx * 100}%;background:var(--ink)"></i><i style="height:${r.inv / mx * 100}%;background:var(--line)"></i></div>`)}</div>
    <div class="row mono" style="gap:6px;padding-top:6px;font-size:11px;color:var(--mute)">${R.map(r => html`<span class="grow center">Y${r.y}</span>`)}</div></div>`;
}
const outs = items => html`<div class="tout">${items.map(([l, v, c]) => html`<div class="col" style="gap:2px"><span class="label tiny">${l}</span><span class="mono ${c || ''}" style="font-size:20px">${v}</span></div>`)}</div>`;
const big = (l, v, c, s) => html`<div class="col" style="gap:2px"><span class="label">${l}</span><span class="tbig ${c || ''}">${v}</span><span class="mute">${s}</span></div>`;

function calc(tab) {
  const s = st.get();
  try {
    if (tab === 'brokerage') {
      const r = Calc().brokerage({ segment: s.seg, buyPrice: n('bp'), sellPrice: n('sp'), quantity: n('q') });
      const items = [['Brokerage', r.brokerage], ['STT', r.stt], ['Exchange transaction charges', r.exchangeTransactionCharges], ['SEBI charges', r.sebiCharges], ['GST', r.gst], ['Stamp duty', r.stampDuty]];
      return {
        inputs: html`<div class="col" style="gap:6px"><b class="small">Segment</b>${seg([['options', 'Options'], ['futures', 'Futures'], ['equity_intraday', 'Equity intraday']], s.seg, 'seg')}</div>
          ${input('bp', 'Buy price', { pre: '₹', hint: 'per unit' })}${input('sp', 'Sell price', { pre: '₹', hint: 'per unit' })}
          ${input('q', 'Quantity', { suf: 'units', hint: s.seg === 'equity_intraday' ? 'shares' : 'lot size × lots, e.g. 75 for 1 NIFTY lot' })}`,
        output: html`${big('Net P&L after charges', inr(r.netPnl, { dp: 2 }), tone(r.netPnl), `Gross ${inr(r.grossPnl, { dp: 2 })} · charges eat ${r.grossPnl ? `${Math.abs(r.totalCharges / r.grossPnl * 100).toFixed(1)}% of it` : '—'}`)}
          ${outs([['Turnover', inr(r.turnover, { sign: false })], ['Total charges', inr(r.totalCharges, { sign: false, dp: 2 })], ['Points to break even', price(r.breakEvenPoints)]])}
          <div class="stack" style="border-top:1px solid var(--line)">${items.map(([l, v]) => html`<div class="row" style="justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--line)"><span>${l}</span><span class="mono">${inr(v, { sign: false, dp: 2 })}</span></div>`)}
            <div class="row b" style="justify-content:space-between;padding:11px 0;border-bottom:1.5px solid var(--ink)"><span>Total charges</span><span class="mono">${inr(r.totalCharges, { sign: false, dp: 2 })}</span></div></div>`,
        note: 'Zerodha’s published rates (the same formula the server uses for the After charges basis). Check zerodha.com/charges before relying on it; rates change.',
      };
    }
    if (tab === 'sip' || tab === 'stepup') {
      const yrs = Math.min(50, Math.round(n('sy'))), su = tab === 'stepup' ? n('su') : 0;
      const r = Calc().stepUpSip({ monthlyInvestment: n('sip'), annualReturnPct: n('sr'), years: yrs, annualStepUpPct: su });
      return {
        inputs: html`${input('sip', 'Monthly investment', { pre: '₹' })}${input('sr', 'Expected return', { suf: '% a year', hint: 'Long-run equity index average is around 12%' })}
          ${input('sy', 'Time period', { suf: 'years', hint: 'Up to 50', ok: x => x > 0 && x <= 50 })}${tab === 'stepup' ? input('su', 'Annual step-up', { suf: '%', hint: 'Raise the SIP by this much each year', ok: x => x >= 0 && x <= 100 }) : ''}`,
        output: html`${big('Expected value', inrS(r.maturityValue, { sign: false }), '', `after ${plural(yrs, 'year')}${su ? `, stepping up ${su}% a year` : ''}`)}
          ${outs([['Invested', inrS(r.investedAmount, { sign: false })], ['Gains', inrS(r.estimatedReturns), tone(r.estimatedReturns)], ['Multiple', `${(r.maturityValue / (r.investedAmount || 1)).toFixed(2)}×`]])}
          ${bars((r.yearlyBreakdown || []).map(y => ({ y: y.year, v: y.corpus, inv: y.invested })))}`,
        note: 'Assumes the same return every month, compounded monthly. Real returns vary year to year.',
      };
    }
    if (tab === 'lumpsum') {
      const yrs = Math.min(50, Math.round(n('ly')));
      const r = Calc().lumpsumStepUpSip({ lumpsum: n('ls'), monthlyInvestment: n('lsip'), annualReturnPct: n('lr'), years: yrs, annualStepUpPct: n('lsu') });
      return {
        inputs: html`${input('ls', 'Lumpsum today', { pre: '₹' })}${input('lr', 'Expected return', { suf: '% a year' })}${input('ly', 'Time period', { suf: 'years', ok: x => x > 0 && x <= 50 })}
          ${input('lsip', 'Plus monthly SIP', { pre: '₹', hint: 'Optional, 0 for lumpsum only', ok: x => x >= 0 })}${input('lsu', 'SIP step-up', { suf: '% a year', ok: x => x >= 0 })}`,
        output: html`${big('Expected value', inrS(r.maturityValue, { sign: false }), '', `lumpsum ${inrS(n('ls'), { sign: false })}${n('lsip') ? ` plus ${inr(n('lsip'), { sign: false })}/month` : ''}`)}
          ${outs([['Invested', inrS(r.investedAmount, { sign: false })], ['Gains', inrS(r.estimatedReturns), tone(r.estimatedReturns)], ['Multiple', `${(r.maturityValue / (r.investedAmount || 1)).toFixed(2)}×`]])}
          ${bars((r.yearlyBreakdown || []).map(y => ({ y: y.year, v: y.corpus, inv: y.invested })))}`,
        note: 'The lumpsum and the SIP both compound monthly at the same expected return.',
      };
    }
    const r = Calc().cagr({ initialValue: n('cs'), finalValue: n('ce'), years: n('cy') });
    const c = r.cagrPct ?? 0, abs = n('cs') ? (n('ce') / n('cs') - 1) * 100 : 0;
    const Y = [];
    for (let y = 1; y <= Math.min(30, Math.round(n('cy'))); y++) Y.push({ y, inv: n('cs'), v: n('cs') * (1 + c / 100) ** y });
    return {
      inputs: html`${input('cs', 'Starting value', { pre: '₹' })}${input('ce', 'Ending value', { pre: '₹' })}${input('cy', 'Years', { suf: 'years', hint: 'Fractions allowed, e.g. 2.5' })}`,
      output: html`${big('CAGR', r.cagrPct == null ? '—' : pct(c), tone(c), 'compound annual growth rate')}
        ${outs([['Absolute return', pct(abs, 1), tone(abs)], ['Money multiple', `${(n('ce') / (n('cs') || 1)).toFixed(2)}×`], ['Doubles every', c > 0 ? `${(Math.log(2) / Math.log(1 + c / 100)).toFixed(1)} years` : '—']])}
        ${Y.length ? bars(Y) : ''}`,
      note: 'CAGR smooths the path: it is the steady yearly rate that turns the start value into the end value.',
    };
  } catch (e) {
    return { inputs: '', output: empty('', `Check the inputs: ${e.message}`), note: '' };
  }
}

export default {
  title: 'Tools',
  render(main, params) {
    const tab = TABS.some(t => t[0] === params.tab) ? params.tab : 'brokerage';
    let subs = null, results = null, q = '', searching = false, searchErr = '';
    function body() {
      if (tab !== 'instruments') {
        const r = calc(tab);
        return html`<div class="tools-grid"><div class="stack" style="gap:16px">${r.inputs}</div>
          <div class="stack" style="gap:20px" data-out>${r.output}<p class="tiny mute pretty" style="margin:0">${r.note}</p></div></div>`;
      }
      const held = new Set(book.deployments().flatMap(v => v.legs.map(l => l.instrument_token)));
      const subset = new Set((subs || []).map(x => x.instrument_token));
      const list = q.length >= 2 ? (results || []).map(r => ({ token: r.instrument_token, sym: r.tradingsymbol, type: r.instrument_type, ex: r.exchange }))
        : (subs || []).map(x => ({ token: x.instrument_token, sym: x.symbol, type: x.static ? 'Always on' : '', ex: '', fixed: x.static }));
      return html`<div class="stack" style="gap:14px">
        <div class="row wrap"><input class="input" style="width:420px;max-width:100%;height:42px" type="search" placeholder="Search NIFTY 26NOV 25000 CE, RELIANCE…" value="${q}" data-iq aria-label="Search instruments">
          <span class="mute small">${subs ? `${plural(subs.length, 'instrument')} streaming` : ''}</span></div>
        ${searchErr ? html`<div class="err">${searchErr}</div>` : ''}
        <div class="tr th inst-grid"><span>Instrument</span><span class="hide-phone">Type</span><span class="hide-phone">Exchange</span><span class="right">Live prices</span></div>
        ${subs === null || searching ? loading(4) : list.length ? list.slice(0, 40).map(i => {
          const on = subset.has(i.token), used = held.has(i.token);
          return html`<div class="tr inst-grid"><span class="mono small ell">${i.sym}</span><span class="small mute hide-phone">${i.type}</span><span class="small mute hide-phone">${i.ex}</span>
            <span class="row" style="justify-content:flex-end;gap:8px"><span class="tiny mute hide-phone">${used ? 'held by a deployment' : i.fixed ? 'used by the app' : ''}</span>
              <button type="button" class="toggle sm" role="switch" aria-checked="${on}" aria-label="Stream ${i.sym}" data-act="sub" data-t="${i.token}" data-s="${i.sym}"
                ${(on && (used || i.fixed)) ? html`aria-disabled="true" data-reason="${i.sym} is ${used ? 'used by open positions' : 'used by the app'} and can’t be unsubscribed."` : ''}></button></span></div>`;
        }) : empty('', q.length >= 2 ? 'No instruments match.' : 'Nothing subscribed.')}
        ${list.length > 40 ? html`<span class="tiny mute">Showing 40 of ${list.length}. Refine the search to see more.</span>` : ''}
        <span class="tiny mute">Searching the instrument list needs Kite connected. Kite allows up to 3,000 instruments per connection.</span>
      </div>`;
    }
    function paint() {
      mount(main, html`
        <div class="page-head" style="padding-bottom:0"><div class="col"><h1 class="t-h1">Tools</h1><span class="mute">Calculators and the live price subscriptions your deployments use.</span></div></div>
        <nav class="tabs pad-x" style="margin-top:18px" aria-label="Tools">${TABS.map(([k, l]) => html`<a href="${href(`tools/${k}`)}" class="${k === tab ? 'on' : ''}">${l}</a>`)}</nav>
        <div class="pad-x" style="padding-top:28px;padding-bottom:var(--px)">${body()}</div>`);
    }
    const loadSubs = () => api.instruments().then(r => { subs = r.subscribed || []; paint(); }).catch(e => { subs = []; searchErr = e.message; paint(); });
    const search = debounce(async () => {
      if (q.length < 2) { results = null; searching = false; paint(); return; }
      try { results = (await api.searchInstruments(q)).results; searchErr = ''; } catch (e) { results = []; searchErr = e.message; }
      searching = false; paint();
      const el = $('[data-iq]', main); if (el) { el.focus(); el.setSelectionRange(q.length, q.length); }
    }, 350);
    const offIn = on(main, 'input', '[data-k],[data-iq]', (e, el) => {
      if (el.dataset.iq !== undefined) { q = el.value.trim(); searching = q.length >= 2; search(); return; }
      st.set({ [el.dataset.k]: el.value });
      const k = el.dataset.k, pos = el.selectionStart;
      paint();
      const n2 = $(`[data-k="${k}"]`, main); if (n2) { n2.focus(); n2.setSelectionRange(pos, pos); }
    });
    const off = actions(main, {
      seg: d => { st.set({ seg: d.v }); paint(); },
      sub: async (d, el) => {
        if (el.dataset.reason) return say(el.dataset.reason);
        const token = +d.t, on2 = el.getAttribute('aria-checked') === 'true';
        try {
          if (on2) await api.unsubscribe(token); else await api.subscribe([{ instrument_token: token, symbol: d.s }]);
          say(on2 ? `Stopped streaming ${d.s}.` : `Streaming ${d.s}.`);
          await loadSubs();
        } catch (e) { say(e.message); }
      },
    });
    if (tab === 'instruments') loadSubs();
    paint();
    return () => { off(); offIn(); };
  },
};
