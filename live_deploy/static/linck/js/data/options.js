// Option prices in the browser, the same way the server prices them
// (app/geek/optionmath.py): Black-Scholes off the spot, Black-76 off the
// future, a flat rate, time to 15:30 IST on the expiry day in calendar
// years. The Replay's payoff view draws its "today" curve with these, so
// at the index's own level it lands on the legs' actual prices.

export const MIN_T = 1 / (365 * 24 * 60);   // a minute: at expiry an option is worth what it's in the money

// The standard normal CDF (Abramowitz & Stegun 7.1.26: within 1.5e-7).
export function ncdf(x) {
  const z = Math.abs(x) / Math.SQRT2, t = 1 / (1 + 0.3275911 * z);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z);
  return x >= 0 ? (1 + y) / 2 : (1 - y) / 2;
}

export const intrinsic = (kind, s, k) => (kind === 'CE' ? Math.max(0, s - k) : Math.max(0, k - s));

// Black-Scholes price of a CE or PE: spot s, strike k, t years, vol a fraction.
export function bs(kind, s, k, t, vol, r) {
  if (t <= MIN_T || !(vol > 0) || !(s > 0) || !(k > 0)) return intrinsic(kind, s, k);
  const sq = vol * Math.sqrt(t);
  const d1 = (Math.log(s / k) + (r + vol * vol / 2) * t) / sq, d2 = d1 - sq;
  return kind === 'CE' ? s * ncdf(d1) - k * Math.exp(-r * t) * ncdf(d2) : k * Math.exp(-r * t) * ncdf(-d2) - s * ncdf(-d1);
}

// Black-76: priced off the future f; the rate only discounts.
export const black76 = (kind, f, k, t, vol, r) => Math.exp(-r * t) * bs(kind, f, k, t, vol, 0);

// Years from `at` to 15:30 IST on `expiry` (YYYY-MM-DD), at least a minute.
export function yearsTo(expiry, at) {
  const end = new Date(`${expiry}T15:30:00+05:30`).getTime();
  return Math.max(MIN_T, (end - new Date(at).getTime()) / (365 * 86400e3));
}
