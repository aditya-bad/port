// live_deploy — Tools view: SIP/CAGR/brokerage calculators. Pure UI
// (forms + results rendering) over Calculators (calculators.js) — every
// actual formula lives there, generic and DOM-free, so it's reusable
// from anywhere else in this app (or ported elsewhere) without dragging
// this file's rendering along. Five tabs, same `.tabs` + single-body-
// container pattern Account's own view already uses (see account.js).

const Tools = {
  _tab: 'sip',

  load() {
    this._render();
  },

  switchTab(tab) {
    this._tab = tab;
    document.querySelectorAll('#toolsTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    this._render();
  },

  // One entry per tab -- `form` builds that tab's markup, `calc` fills
  // in its results panel. Single table instead of two parallel
  // if/else chains so adding a 6th tool later can't desync which form
  // goes with which calculation.
  _TABS: {
    sip: { form: '_sipFormHtml', calc: 'calculateSip' },
    stepup: { form: '_stepUpFormHtml', calc: 'calculateStepUp' },
    lumpsum: { form: '_lumpsumFormHtml', calc: 'calculateLumpsum' },
    cagr: { form: '_cagrFormHtml', calc: 'calculateCagr' },
    brokerage: { form: '_brokerageFormHtml', calc: 'calculateBrokerage' },
  },

  _render() {
    const entry = this._TABS[this._tab] || this._TABS.sip;
    const body = document.getElementById('toolsBody');
    body.innerHTML = this[entry.form]();
    // Compute a default result immediately on render (every form ships
    // with sensible pre-filled inputs) rather than showing an empty
    // results panel until the user's first keystroke.
    this[entry.calc]();
  },

  // Reads a numeric input by id; returns null (not NaN) if it's empty
  // or unparseable, so every calculate*() below can show one clear
  // "enter a valid ..." message instead of rendering NaN/Infinity.
  _num(id) {
    const el = document.getElementById(id);
    if (!el || el.value === '') return null;
    const v = Number(el.value);
    return Number.isFinite(v) ? v : null;
  },

  // ── Shared result card shape for the three SIP-family calculators ──
  _sipResultHtml(result, { showSchedule = false } = {}) {
    const schedule = showSchedule && result.yearlyBreakdown.length ? `
      <div class="table-wrap" style="margin-top:16px;">
        <table><thead><tr><th>Year</th><th>Invested so far</th><th>Value so far</th></tr></thead>
        <tbody>${result.yearlyBreakdown.map(r => `<tr>
          <td>${r.year}</td><td>${fmtMoney(r.invested)}</td><td>${fmtMoney(r.corpus)}</td>
        </tr>`).join('')}</tbody></table>
      </div>` : '';
    return `
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-label">Invested Amount</div>
          <div class="stat-value">${fmtMoney(result.investedAmount)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Estimated Returns</div>
          <div class="stat-value pos">${fmtMoney(result.estimatedReturns)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Total Value</div>
          <div class="stat-value">${fmtMoney(result.maturityValue)}</div>
        </div>
      </div>
      ${schedule}`;
  },

  // ── 1. SIP ───────────────────────────────────────────────────────
  _sipFormHtml() {
    return `
      <div class="ux-tools-grid">
        <div class="ux-tools-form">
          <div class="field"><label>Monthly Investment (₹)</label><input type="number" id="sipMonthly" value="5000" min="0" step="100" oninput="Tools.calculateSip()"></div>
          <div class="field"><label>Expected Annual Return (%)</label><input type="number" id="sipReturn" value="12" min="0" step="0.5" oninput="Tools.calculateSip()"></div>
          <div class="field"><label>Investment Duration (years)</label><input type="number" id="sipYears" value="10" min="1" step="1" oninput="Tools.calculateSip()"></div>
        </div>
        <div id="sipResult"></div>
      </div>`;
  },

  calculateSip() {
    const monthlyInvestment = this._num('sipMonthly'), annualReturnPct = this._num('sipReturn'), years = this._num('sipYears');
    const out = document.getElementById('sipResult');
    if (monthlyInvestment == null || annualReturnPct == null || years == null || monthlyInvestment < 0 || years <= 0) {
      out.innerHTML = emptyHtml('Enter a monthly investment, expected return, and a duration greater than 0.');
      return;
    }
    out.innerHTML = this._sipResultHtml(Calculators.sip({ monthlyInvestment, annualReturnPct, years }));
  },

  // ── 2. Step-up SIP ───────────────────────────────────────────────
  _stepUpFormHtml() {
    return `
      <div class="ux-tools-grid">
        <div class="ux-tools-form">
          <div class="field"><label>Monthly Investment (₹)</label><input type="number" id="stepMonthly" value="5000" min="0" step="100" oninput="Tools.calculateStepUp()"></div>
          <div class="field"><label>Annual Step-up (%)</label><input type="number" id="stepUp" value="10" min="0" step="1" oninput="Tools.calculateStepUp()"></div>
          <div class="field"><label>Expected Annual Return (%)</label><input type="number" id="stepReturn" value="12" min="0" step="0.5" oninput="Tools.calculateStepUp()"></div>
          <div class="field"><label>Investment Duration (years)</label><input type="number" id="stepYears" value="10" min="1" step="1" oninput="Tools.calculateStepUp()"></div>
          <div class="ux-tools-note">The monthly investment rises by this % at the start of every subsequent year — year 1 stays at the amount above.</div>
        </div>
        <div id="stepUpResult"></div>
      </div>`;
  },

  calculateStepUp() {
    const monthlyInvestment = this._num('stepMonthly'), annualStepUpPct = this._num('stepUp');
    const annualReturnPct = this._num('stepReturn'), years = this._num('stepYears');
    const out = document.getElementById('stepUpResult');
    if (monthlyInvestment == null || annualStepUpPct == null || annualReturnPct == null || years == null || monthlyInvestment < 0 || years <= 0) {
      out.innerHTML = emptyHtml('Enter a monthly investment, step-up %, expected return, and a duration greater than 0.');
      return;
    }
    out.innerHTML = this._sipResultHtml(
      Calculators.stepUpSip({ monthlyInvestment, annualReturnPct, years, annualStepUpPct }),
      { showSchedule: true },
    );
  },

  // ── 3. Lumpsum + Step-up SIP ─────────────────────────────────────
  _lumpsumFormHtml() {
    return `
      <div class="ux-tools-grid">
        <div class="ux-tools-form">
          <div class="field"><label>Lumpsum Amount (₹)</label><input type="number" id="lsLumpsum" value="100000" min="0" step="1000" oninput="Tools.calculateLumpsum()"></div>
          <div class="field"><label>Monthly SIP (₹)</label><input type="number" id="lsMonthly" value="5000" min="0" step="100" oninput="Tools.calculateLumpsum()"></div>
          <div class="field"><label>Annual Step-up (%)</label><input type="number" id="lsUp" value="10" min="0" step="1" oninput="Tools.calculateLumpsum()"></div>
          <div class="field"><label>Expected Annual Return (%)</label><input type="number" id="lsReturn" value="12" min="0" step="0.5" oninput="Tools.calculateLumpsum()"></div>
          <div class="field"><label>Investment Duration (years)</label><input type="number" id="lsYears" value="10" min="1" step="1" oninput="Tools.calculateLumpsum()"></div>
          <div class="ux-tools-note">The lumpsum is invested upfront and grows alongside the step-up SIP for the full duration.</div>
        </div>
        <div id="lumpsumResult"></div>
      </div>`;
  },

  calculateLumpsum() {
    const lumpsum = this._num('lsLumpsum'), monthlyInvestment = this._num('lsMonthly'), annualStepUpPct = this._num('lsUp');
    const annualReturnPct = this._num('lsReturn'), years = this._num('lsYears');
    const out = document.getElementById('lumpsumResult');
    if (lumpsum == null || monthlyInvestment == null || annualStepUpPct == null || annualReturnPct == null || years == null
        || lumpsum < 0 || monthlyInvestment < 0 || years <= 0) {
      out.innerHTML = emptyHtml('Enter a lumpsum, monthly SIP, step-up %, expected return, and a duration greater than 0.');
      return;
    }
    out.innerHTML = this._sipResultHtml(
      Calculators.lumpsumStepUpSip({ lumpsum, monthlyInvestment, annualReturnPct, years, annualStepUpPct }),
      { showSchedule: true },
    );
  },

  // ── 4. CAGR ──────────────────────────────────────────────────────
  _cagrFormHtml() {
    return `
      <div class="ux-tools-grid">
        <div class="ux-tools-form">
          <div class="field"><label>Initial Value (₹)</label><input type="number" id="cagrInitial" value="100000" min="0" step="1000" oninput="Tools.calculateCagr()"></div>
          <div class="field"><label>Final Value (₹)</label><input type="number" id="cagrFinal" value="200000" min="0" step="1000" oninput="Tools.calculateCagr()"></div>
          <div class="field"><label>Duration (years)</label><input type="number" id="cagrYears" value="5" min="0.1" step="0.5" oninput="Tools.calculateCagr()"></div>
        </div>
        <div id="cagrResult"></div>
      </div>`;
  },

  calculateCagr() {
    const initialValue = this._num('cagrInitial'), finalValue = this._num('cagrFinal'), years = this._num('cagrYears');
    const out = document.getElementById('cagrResult');
    if (initialValue == null || finalValue == null || years == null || initialValue <= 0 || finalValue <= 0 || years <= 0) {
      out.innerHTML = emptyHtml('Enter a positive initial value, final value, and duration.');
      return;
    }
    const { cagrPct } = Calculators.cagr({ initialValue, finalValue, years });
    out.innerHTML = `
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-label">CAGR</div>
          <div class="stat-value ${pnlClass(cagrPct)}">${fmtPct(cagrPct)}</div>
          <div class="stat-sub">Compounded annual growth rate, ${escapeHtml(String(years))} year(s)</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Absolute Growth</div>
          <div class="stat-value ${pnlClass(finalValue - initialValue)}">${fmtSignedMoney(finalValue - initialValue)}</div>
          <div class="stat-sub">${fmtPct((finalValue / initialValue - 1) * 100)} total, uncompounded</div>
        </div>
      </div>`;
  },

  // ── 5. Brokerage (Zerodha-equivalent) ───────────────────────────
  _brokerageFormHtml() {
    return `
      <div class="ux-tools-grid">
        <div class="ux-tools-form">
          <div class="field">
            <label>Segment</label>
            <select id="brkSegment" onchange="Tools.calculateBrokerage()">
              <option value="equity_intraday">Equity Intraday</option>
              <option value="futures">Equity Futures</option>
              <option value="options">Equity Options</option>
            </select>
          </div>
          <div class="field"><label>Buy Price (₹)</label><input type="number" id="brkBuy" value="100" min="0" step="0.05" oninput="Tools.calculateBrokerage()"></div>
          <div class="field"><label>Sell Price (₹)</label><input type="number" id="brkSell" value="102" min="0" step="0.05" oninput="Tools.calculateBrokerage()"></div>
          <div class="field"><label>Quantity</label><input type="number" id="brkQty" value="1" min="1" step="1" oninput="Tools.calculateBrokerage()"></div>
          <div class="ux-tools-note">Quantity is the TOTAL quantity (lots × lot size, already multiplied) for Futures/Options — the same single field Zerodha's own calculator asks for.</div>
        </div>
        <div id="brokerageResult"></div>
      </div>`;
  },

  calculateBrokerage() {
    const segment = document.getElementById('brkSegment')?.value || 'equity_intraday';
    const buyPrice = this._num('brkBuy'), sellPrice = this._num('brkSell'), quantity = this._num('brkQty');
    const out = document.getElementById('brokerageResult');
    if (buyPrice == null || sellPrice == null || quantity == null || buyPrice < 0 || sellPrice < 0 || quantity <= 0) {
      out.innerHTML = emptyHtml('Enter a buy price, sell price, and a quantity greater than 0.');
      return;
    }
    const r = Calculators.brokerage({ segment, buyPrice, sellPrice, quantity });
    const rows = [
      ['Brokerage', r.brokerage],
      ['STT', r.stt],
      ['Exchange Transaction Charges', r.exchangeTransactionCharges],
      ['SEBI Charges', r.sebiCharges],
      ['GST', r.gst],
      ['Stamp Duty', r.stampDuty],
    ];
    out.innerHTML = `
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-label">Total Charges</div>
          <div class="stat-value neg">${fmtMoney(r.totalCharges)}</div>
          <div class="stat-sub">${r.label} · turnover ${fmtMoney(r.turnover)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Net P&amp;L</div>
          <div class="stat-value ${pnlClass(r.netPnl)}">${fmtSignedMoney(r.netPnl)}</div>
          <div class="stat-sub">Gross ${fmtSignedMoney(r.grossPnl)}, before charges</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Break-even Move</div>
          <div class="stat-value">${fmtNum(r.breakEvenPoints, 4)}</div>
          <div class="stat-sub">Price move needed just to cover charges</div>
        </div>
      </div>
      <div class="table-wrap" style="margin-top:16px;">
        <table><thead><tr><th>Charge</th><th>Amount</th></tr></thead>
        <tbody>${rows.map(([label, amt]) => `<tr><td>${label}</td><td>${fmtMoney(amt)}</td></tr>`).join('')}</tbody>
        <tfoot><tr class="positions-total-row"><td><b>Total Charges</b></td><td><b>${fmtMoney(r.totalCharges)}</b></td></tr></tfoot>
        </table>
      </div>
      <div class="ux-tools-note">
        STT and stamp duty rates are set by the government/exchanges and change periodically (most recently, F&amp;O STT
        revised 1 Apr 2026) — these match Zerodha's published charges as of this writing; cross-check
        <a href="https://zerodha.com/charges" target="_blank" rel="noopener">zerodha.com/charges</a> if a rate looks off.
        Models a plain buy-then-sell (or sell-then-buy) round trip squared off before expiry, same as Zerodha's own
        calculator — STT on an option exercised/expired in-the-money isn't modeled here either.
      </div>`;
  },
};
