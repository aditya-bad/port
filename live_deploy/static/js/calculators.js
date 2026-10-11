// live_deploy — Calculators: plain, dependency-free financial math with
// ZERO DOM/fetch/UI coupling (no `document`, no `Api`, no app-specific
// formatting) — deliberately so this file can be dropped into any other
// page of this app, or any other project entirely, and used exactly as
// a library. Every function takes a plain options object and returns a
// plain object; nothing here reads or writes global state. Attached to
// `window.Calculators` (not an ES module) to match this app's existing
// plain-script-tag convention (same as Api/UIKit) — load this file
// before any page that calls into it (see index.html's own script order,
// right after api.js, ahead of every page controller).
//
// The Tools page (tools.js) is this module's first and only caller
// today, but nothing here references it or anything else page-specific.

const Calculators = {};

// ── SIP / Step-up SIP / Lumpsum+Step-up SIP ─────────────────────────
// All three share the same underlying model: a monthly-compounding
// annuity-DUE (each month's contribution is assumed invested at the
// START of that month, then grows for the full month) — the convention
// essentially every Indian SIP calculator (AMFI, Groww, ET Money,
// Zerodha Coin) uses. `sip()` below has a closed form for a FIXED
// monthly contribution; stepUpSip/lumpsumStepUpSip can't (the
// contribution itself changes every 12 months), so they run the same
// recurrence month-by-month instead — verified to reduce to the exact
// same closed-form result as `sip()` when annualStepUpPct/lumpsum is 0.

// `sip({ monthlyInvestment, annualReturnPct, years })` →
// `{ investedAmount, estimatedReturns, maturityValue }`
Calculators.sip = function sip({ monthlyInvestment, annualReturnPct, years }) {
  const months = Math.round(years * 12);
  const investedAmount = monthlyInvestment * months;
  const i = annualReturnPct / 100 / 12;
  const maturityValue = i === 0
    ? investedAmount
    : monthlyInvestment * (((Math.pow(1 + i, months) - 1) / i) * (1 + i));
  return { investedAmount, estimatedReturns: maturityValue - investedAmount, maturityValue };
};

// `stepUpSip({ monthlyInvestment, annualReturnPct, years, annualStepUpPct })` →
// `{ investedAmount, estimatedReturns, maturityValue, yearlyBreakdown }`
// `annualStepUpPct`: the monthly contribution rises by this % at the
// start of every subsequent 12-month block (year 1 stays at
// `monthlyInvestment`, year 2 starts at monthlyInvestment * (1+step),
// year 3 at monthlyInvestment * (1+step)^2, ...).
Calculators.stepUpSip = function stepUpSip({ monthlyInvestment, annualReturnPct, years, annualStepUpPct }) {
  return Calculators._runSipLadder({
    lumpsum: 0, monthlyInvestment, annualReturnPct, years, annualStepUpPct,
  });
};

// `lumpsumStepUpSip({ lumpsum, monthlyInvestment, annualReturnPct, years, annualStepUpPct })` →
// same shape as stepUpSip, with an upfront lumpsum compounding
// alongside the SIP in the same monthly ladder (invested at t=0, so it
// grows for the full `years`) rather than added as a separately-
// compounded figure at the end — keeps both pieces on one consistent
// monthly-compounding basis.
Calculators.lumpsumStepUpSip = function lumpsumStepUpSip({ lumpsum, monthlyInvestment, annualReturnPct, years, annualStepUpPct }) {
  return Calculators._runSipLadder({
    lumpsum, monthlyInvestment, annualReturnPct, years, annualStepUpPct,
  });
};

// Shared engine behind stepUpSip/lumpsumStepUpSip (sip() itself uses
// the closed form above, not this, since it doesn't need a month-by-
// month loop at all). `yearlyBreakdown`: one row per completed year —
// invested-to-date and corpus-value-at-that-point — the same shape a
// "year by year" table on the page can render directly.
Calculators._runSipLadder = function _runSipLadder({ lumpsum = 0, monthlyInvestment, annualReturnPct, years, annualStepUpPct = 0 }) {
  const months = Math.round(years * 12);
  const i = annualReturnPct / 100 / 12;
  let corpus = lumpsum;
  let invested = lumpsum;
  let contribution = monthlyInvestment;
  const yearlyBreakdown = [];
  for (let m = 1; m <= months; m++) {
    if (m > 1 && (m - 1) % 12 === 0) contribution *= (1 + annualStepUpPct / 100);
    corpus = (corpus + contribution) * (1 + i);
    invested += contribution;
    if (m % 12 === 0) yearlyBreakdown.push({ year: m / 12, invested, corpus });
  }
  return { investedAmount: invested, estimatedReturns: corpus - invested, maturityValue: corpus, yearlyBreakdown };
};

// ── CAGR ─────────────────────────────────────────────────────────────
// `cagr({ initialValue, finalValue, years })` → `{ cagrPct }` — null
// (not NaN/Infinity) when the inputs can't produce a meaningful rate
// (non-positive value or duration), same "not computed" convention
// this app's own stat cards already use elsewhere (see api.js's
// computeMaxDrawdown, detail.js's profitFactor, etc.) rather than
// rendering garbage.
Calculators.cagr = function cagr({ initialValue, finalValue, years }) {
  if (!(initialValue > 0) || !(finalValue > 0) || !(years > 0)) return { cagrPct: null };
  return { cagrPct: (Math.pow(finalValue / initialValue, 1 / years) - 1) * 100 };
};

// ── Brokerage (Zerodha-equivalent) ──────────────────────────────────
// Rates below are cross-checked against Zerodha's own published
// charges (zerodha.com/charges), Zerodha's support articles, and NSE's
// own transaction-charge circular as of this writing, THEN corrected
// against a real, live side-by-side check against zerodha.com's own
// brokerage calculator (two Equity Options trades, qty 400 and 40000,
// buy 100/sell 110 both times) -- the exchange transaction charge
// figure this started with for OPTIONS (0.03503%, from a source
// claiming an Oct 2024 NSE revision) undershot Zerodha's actual
// displayed number on BOTH trades; the pre-"revision" figure
// (0.03553%) matches exactly, so that claimed revision was wrong (or
// never applied to this segment). Re-ran the full breakdown
// (brokerage/STT/exchange/SEBI/GST/stamp duty/total/net) against both
// trades and every figure now matches Zerodha's to the paisa --
// including stamp duty needing to be ROUNDED to the nearest whole
// rupee (not left as a raw decimal), which the formula below now does.
//
// equity_intraday/futures' exchangePct were reverted the same way
// (back to their own pre-"revision" figures, 0.00307%/0.00183%) on the
// INFERENCE that the same wrong "revision" source was the problem for
// them too, not from an independent live check the way options was --
// worth a similar side-by-side Equity Intraday / Futures trade on
// zerodha.com/brokerage-calculator to actually confirm.
//
// THREE of these figures are government/exchange-set and change
// independently of Zerodha (most recently: F&O STT revised w.e.f. 1
// Apr 2026) — re-verify against zerodha.com/charges directly before
// trusting this for anything beyond an estimate, and update this one
// table (nothing else) if a rate changes.
//
// Models a plain buy-then-sell (or sell-then-buy) round trip fully
// squared off before expiry — exactly what Zerodha's own brokerage
// calculator computes from a Buy Price/Sell Price/Quantity input, and
// the overwhelming majority of real trades. Deliberately NOT modeled:
// STT on an option EXERCISED/expired ITM (charged on intrinsic value,
// only relevant if a position is held all the way to expiry rather
// than squared off) — Zerodha's own calculator doesn't model this
// either, for the same reason.
Calculators.BROKERAGE_RATES = {
  equity_intraday: {
    label: 'Equity Intraday',
    brokeragePct: 0.0003, brokerageCap: 20,         // 0.03% or Rs 20/executed order, whichever is lower
    sttPct: 0.00025, sttBothSides: false,            // 0.025% of trade value, sell side only
    exchangePct: 0.0000307,                          // NSE: Rs 3.07 per lakh of turnover (both sides)
    stampDutyPct: 0.00003,                           // 0.003% of trade value, buy side only
  },
  futures: {
    label: 'Equity Futures',
    brokeragePct: 0.0003, brokerageCap: 20,
    sttPct: 0.0005, sttBothSides: false,             // 0.05% of trade value, sell side only (w.e.f. 1 Apr 2026)
    exchangePct: 0.0000183,                          // NSE: Rs 1.83 per lakh of turnover
    stampDutyPct: 0.00002,                           // 0.002% of trade value, buy side only
  },
  options: {
    label: 'Equity Options',
    brokerageFlat: 20,                               // flat Rs 20 per executed order -- NOT percentage-based
    sttPct: 0.0015, sttBothSides: false,             // 0.15% of PREMIUM value, sell side only (w.e.f. 1 Apr 2026)
    exchangePct: 0.0003553,                          // NSE: Rs 35.53 per lakh of PREMIUM turnover -- verified exact (see comment above)
    stampDutyPct: 0.00003,                           // 0.003% of premium value, buy side only
  },
};
// SEBI turnover fee: Rs 10 per crore (= 0.0001%), both sides, every segment.
Calculators.SEBI_CHARGE_PCT = 0.000001;
// GST: 18%, levied on (brokerage + exchange transaction charges + SEBI
// charges) ONLY -- never on STT or stamp duty, both of which are
// themselves taxes, not a taxable service charge.
Calculators.GST_PCT = 0.18;

// `brokerage({ segment, buyPrice, sellPrice, quantity })` — `segment`
// is one of the BROKERAGE_RATES keys above. `quantity` is the TOTAL
// quantity (lots × lot size, already multiplied, for futures/options)
// — same single field Zerodha's own calculator asks for, so values can
// be cross-checked against it directly input-for-input.
Calculators.brokerage = function brokerage({ segment, buyPrice, sellPrice, quantity }) {
  const rates = Calculators.BROKERAGE_RATES[segment];
  if (!rates) throw new Error(`Calculators.brokerage: unknown segment "${segment}"`);

  const buyValue = buyPrice * quantity;
  const sellValue = sellPrice * quantity;
  const turnover = buyValue + sellValue;

  const brokerageAmt = rates.brokerageFlat != null
    ? rates.brokerageFlat * 2   // flat charge, once per executed order -- two orders (buy + sell)
    : Math.min(rates.brokeragePct * buyValue, rates.brokerageCap)
      + Math.min(rates.brokeragePct * sellValue, rates.brokerageCap);

  const sttAmt = rates.sttPct * (rates.sttBothSides ? turnover : sellValue);
  const exchangeAmt = rates.exchangePct * turnover;
  const sebiAmt = Calculators.SEBI_CHARGE_PCT * turnover;
  const gstAmt = Calculators.GST_PCT * (brokerageAmt + exchangeAmt + sebiAmt);
  // Rounded to the nearest whole rupee (the Indian Stamp Act's own
  // collection convention) -- unlike every other charge here, which
  // Zerodha's own calculator shows as a raw decimal. Verified against
  // both live trades above: 0.00003 * Rs 40,000 = Rs 1.20 displayed as
  // Rs 1, not Rs 1.20.
  const stampDutyAmt = Math.round(rates.stampDutyPct * buyValue);

  const totalCharges = brokerageAmt + sttAmt + exchangeAmt + sebiAmt + gstAmt + stampDutyAmt;
  const grossPnl = sellValue - buyValue;
  const netPnl = grossPnl - totalCharges;

  return {
    segment, label: rates.label, buyValue, sellValue, turnover,
    brokerage: brokerageAmt, stt: sttAmt, exchangeTransactionCharges: exchangeAmt,
    sebiCharges: sebiAmt, gst: gstAmt, stampDuty: stampDutyAmt,
    totalCharges, grossPnl, netPnl,
    // Price move (in the same unit as buyPrice/sellPrice) needed on
    // `quantity` just to cover totalCharges -- 0 if quantity is 0.
    breakEvenPoints: quantity ? totalCharges / quantity : 0,
  };
};
