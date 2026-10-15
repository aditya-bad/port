// Tools — investment and brokerage calculators (calculators.js, the
// same engine the classic Tools page uses, verified against Zerodha).
import { html, mount, $, $$, on, money, pct, num } from '../util.js';
import { kpi } from '../components.js';

const TOOLS = [
  ['brokerage', 'Brokerage'], ['sip', 'SIP'], ['stepup', 'Step-up SIP'], ['lumpsum', 'Lumpsum + step-up'], ['cagr', 'CAGR'],
];
// calculators.js declares a top-level const (not a window property).
// eslint-disable-next-line no-undef
const C = () => Calculators;
const field = (id, label, value, opts = '') => html`<label class="field"><span>${label}</span><input class="input" type="number" step="any" id="${id}" value="${value}" ${opts}></label>`;
const v = (root, id) => Number($(`#${id}`, root).value);

const forms = {
  brokerage: () => html`<div class="form-grid">
    <label class="field"><span>Segment</span><select class="input" id="seg">
      <option value="options" selected>Options</option><option value="futures">Futures</option><option value="equity_intraday">Equity intraday</option></select></label>
    ${field('buy', 'Buy price', 100)}${field('sell', 'Sell price', 110)}${field('qty', 'Quantity', 75)}</div>`,
  sip: () => html`<div class="form-grid">${field('m', 'Monthly investment (₹)', 10000)}${field('r', 'Expected return (% a year)', 12)}${field('y', 'Years', 10)}</div>`,
  stepup: () => html`<div class="form-grid">${field('m', 'Monthly investment (₹)', 10000)}${field('s', 'Yearly step-up (%)', 10)}${field('r', 'Expected return (% a year)', 12)}${field('y', 'Years', 10)}</div>`,
  lumpsum: () => html`<div class="form-grid">${field('l', 'Lumpsum today (₹)', 100000)}${field('m', 'Monthly investment (₹)', 10000)}${field('s', 'Yearly step-up (%)', 10)}${field('r', 'Expected return (% a year)', 12)}${field('y', 'Years', 10)}</div>`,
  cagr: () => html`<div class="form-grid">${field('a', 'Starting value (₹)', 100000)}${field('b', 'Ending value (₹)', 200000)}${field('y', 'Years', 5)}</div>`,
};

function growth(r) {
  const gainShare = r.maturityValue > 0 ? Math.max(0, r.estimatedReturns) / r.maturityValue * 100 : 0;
  return html`<div class="grid kpis">${kpi('Invested', money(r.investedAmount, { dp: 0 }))}${kpi('Estimated returns', html`<span class="pos">${money(r.estimatedReturns, { dp: 0 })}</span>`)}${kpi('Total value', money(r.maturityValue, { dp: 0 }))}</div>
    <div style="margin-top:16px"><div class="meter" style="height:10px" role="img" aria-label="${pct(100 - gainShare, { dp: 0 })} invested, ${pct(gainShare, { dp: 0 })} returns">
      <i style="left:0;width:${100 - gainShare}%;background:var(--ink-3)"></i><i style="left:${100 - gainShare}%;width:${gainShare}%;background:var(--gain)"></i></div>
      <div class="legend" style="margin-top:8px"><span><i class="lk" style="border-color:var(--ink-3)"></i>Invested ${pct(100 - gainShare, { dp: 0 })}</span><span><i class="lk pos"></i>Returns ${pct(gainShare, { dp: 0 })}</span></div></div>
    ${r.yearlyBreakdown && r.yearlyBreakdown.length ? html`<div class="table-wrap" style="margin-top:16px"><table class="t"><thead><tr><th>Year</th><th class="num">Invested</th><th class="num">Value</th><th class="num">Returns</th></tr></thead>
      <tbody>${r.yearlyBreakdown.map(y => html`<tr><td>${y.year}</td><td class="num">${money(y.invested, { dp: 0 })}</td><td class="num">${money(y.corpus, { dp: 0 })}</td><td class="num pos">${money(y.corpus - y.invested, { dp: 0 })}</td></tr>`)}</tbody></table></div>` : ''}`;
}

const results = {
  brokerage: root => {
    const r = C().brokerage({ segment: $('#seg', root).value, buyPrice: v(root, 'buy'), sellPrice: v(root, 'sell'), quantity: v(root, 'qty') });
    const rows = [['Brokerage', r.brokerage], ['STT', r.stt], ['Exchange transaction charges', r.exchangeTransactionCharges], ['SEBI charges', r.sebiCharges], ['GST', r.gst], ['Stamp duty', r.stampDuty]];
    return html`<div class="grid kpis">${kpi('Total charges', money(r.totalCharges))}${kpi('Gross P&L', html`<span class="${r.grossPnl >= 0 ? 'pos' : 'neg'}">${money(r.grossPnl, { sign: true })}</span>`)}${kpi('Net P&L', html`<span class="${r.netPnl >= 0 ? 'pos' : 'neg'}">${money(r.netPnl, { sign: true })}</span>`)}${kpi('Break-even move', `${num(r.breakEvenPoints)} pts`, 'Per unit, to cover charges')}</div>
      <div class="table-wrap" style="margin-top:16px"><table class="t"><tbody>${rows.map(([k, x]) => html`<tr><td>${k}</td><td class="num">${money(x)}</td></tr>`)}</tbody>
      <tfoot><tr><td>Total · turnover ${money(r.turnover, { dp: 0 })}</td><td class="num">${money(r.totalCharges)}</td></tr></tfoot></table></div>`;
  },
  sip: root => growth(C().sip({ monthlyInvestment: v(root, 'm'), annualReturnPct: v(root, 'r'), years: v(root, 'y') })),
  stepup: root => growth(C().stepUpSip({ monthlyInvestment: v(root, 'm'), annualStepUpPct: v(root, 's'), annualReturnPct: v(root, 'r'), years: v(root, 'y') })),
  lumpsum: root => growth(C().lumpsumStepUpSip({ lumpsum: v(root, 'l'), monthlyInvestment: v(root, 'm'), annualStepUpPct: v(root, 's'), annualReturnPct: v(root, 'r'), years: v(root, 'y') })),
  cagr: root => {
    const r = C().cagr({ initialValue: v(root, 'a'), finalValue: v(root, 'b'), years: v(root, 'y') });
    return r.cagrPct == null ? html`<div class="empty">Enter positive values for all three fields.</div>`
      : html`<div class="grid kpis">${kpi('CAGR', html`<span class="${r.cagrPct >= 0 ? 'pos' : 'neg'}">${pct(r.cagrPct, { sign: true })}</span>`, 'Compound annual growth')}${kpi('Total change', pct((v(root, 'b') / v(root, 'a') - 1) * 100, { sign: true }))}</div>`;
  },
};

export default {
  title: 'Tools',
  render(main, params) {
    const tool = TOOLS.some(([k]) => k === params.tab) ? params.tab : 'brokerage';
    mount(main, html`
      <div class="page-head"><div><h1>Tools</h1><div class="sub">Brokerage matches Zerodha’s calculator · investment projections are estimates</div></div></div>
      <nav class="tabs" aria-label="Calculators">${TOOLS.map(([k, l]) => html`<a href="#/tools/${k}" class="${k === tool ? 'on' : ''}">${l}</a>`)}</nav>
      <div class="grid two" style="align-items:start">
        <section class="card card-pad" id="toolForm">${forms[tool]()}</section>
        <section class="card card-pad" id="toolOut" aria-live="polite"></section>
      </div>`);
    const form = $('#toolForm', main), out = $('#toolOut', main);
    const compute = () => {
      try { mount(out, results[tool](form)); }
      catch (e) { mount(out, html`<div class="empty">Check the inputs — ${e.message}</div>`); }
    };
    compute();
    return on(form, 'input', 'input, select', compute);
  },
};
