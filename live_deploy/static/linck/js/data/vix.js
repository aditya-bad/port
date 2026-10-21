// India VIX for Geek mode (app/vix.py): which instrument it is, its live
// value from the tick stream, else the last stored minute, and what it
// implies. VIX is annualised over calendar days, so the one-day move it
// implies is VIX / √365 per cent.

import { api } from '../core/api.js';
import { prices } from './live.js';
import { emit } from '../core/store.js';

const v = { token: null, last: null, loaded: false };
let loading = null;

export function loadVix() {
  if (v.loaded || loading) return loading;
  loading = api.vix().then(d => {
    v.token = d.token;
    const c = d.candles && d.candles.length ? d.candles[d.candles.length - 1] : null;
    v.last = d.last || d.latest_stored || (c ? { price: c.c, at: new Date(c.t).toISOString() } : null);
    v.loaded = true;
    emit('data', ['vix']);
  }).catch(() => { v.loaded = true; }).finally(() => { loading = null; });
  return loading;
}

// {price, prevClose} — live when ticking, else the last stored value.
export function vixNow() {
  const t = v.token != null ? prices.get(v.token) : null;
  if (t) return { price: t.price, prevClose: t.prevClose, live: true };
  return v.last ? { price: v.last.price, prevClose: null, live: false } : null;
}

export const expectedMovePct = (vix, days = 1) => vix / Math.sqrt(365) * Math.sqrt(days);

export function vixTip(x) {
  if (!x) return 'India VIX\nNot known yet: it appears once Kite is logged in.';
  const ch = x.prevClose ? x.price - x.prevClose : null;
  return [`India VIX${x.live ? '' : ' (last recorded)'}`, `Now  ${x.price.toFixed(2)}`,
    ...(ch != null ? [`Since yesterday  ${ch >= 0 ? '+' : '−'}${Math.abs(ch).toFixed(2)}`] : []),
    `Expected move today  ±${expectedMovePct(x.price).toFixed(2)}%`,
    `Over a week  ±${expectedMovePct(x.price, 7).toFixed(2)}%`,
    'Built from Nifty options: for other indices, a stand-in.'].join('\n');
}
