// LinckLens's holdings at new prices: what /sse/lens sends (app/lens_feed.py,
// {symbol: [price, prev close]}) applied to what /v2/lens/holdings gave, the
// way app/lens.py works it out (equity_row, totals, allocation,
// concentration). Pure: a new object, the old one untouched.

const r2 = v => Math.round(v * 100) / 100;

function equityRow(row, ltp, prev) {
  const value = row.qty * ltp, pnl = value - row.invested;
  const day = prev ? (ltp - prev) * row.qty : null;
  return {
    ...row, ltp, prev_close: prev || row.prev_close, value: r2(value), pnl: r2(pnl),
    pnl_pct: row.invested ? r2(pnl / row.invested * 100) : null,
    day_change: day == null ? null : r2(day), day_change_pct: prev ? r2((ltp / prev - 1) * 100) : null,
  };
}

function totals(rows, day) {
  const invested = rows.reduce((a, r) => a + r.invested, 0), value = rows.reduce((a, r) => a + r.value, 0);
  const out = { count: rows.length, invested: r2(invested), value: r2(value), pnl: r2(value - invested),
    pnl_pct: invested ? r2((value - invested) / invested * 100) : null };
  if (day) {
    const known = rows.filter(r => r.day_change != null), change = known.reduce((a, r) => a + r.day_change, 0);
    out.day_change = known.length ? r2(change) : null;
    out.day_change_pct = known.length && value - change ? r2(change / (value - change) * 100) : null;
    out.advancing = rows.filter(r => (r.day_change || 0) > 0).length;
    out.declining = rows.filter(r => (r.day_change || 0) < 0).length;
  }
  return out;
}

export function applyPrices(h, prices) {
  if (!h || !prices) return h;
  let moved = false;
  const equity = h.equity.map(row => {
    const p = prices[row.symbol];
    if (!p || p[0] == null || (p[0] === row.ltp && (p[1] || row.prev_close) === row.prev_close)) return row;
    moved = true;
    return equityRow(row, p[0], p[1]);
  });
  if (!moved) return h;
  const all = [...equity, ...h.mf], total = all.reduce((a, r) => a + r.value, 0);
  const w = r => ({ ...r, weight: total ? r2(r.value / total * 100) : null });
  const eq = equity.map(w), mf = h.mf.map(w);
  const by = {};
  for (const r of [...eq, ...mf]) {
    const a = by[r.asset] || (by[r.asset] = { ...(h.allocation.find(x => x.key === r.asset) || { key: r.asset, label: r.asset }), value: 0, count: 0 });
    a.value += r.value; a.count += 1;
  }
  const allocation = Object.values(by).sort((a, b) => b.value - a.value)
    .map(a => ({ ...a, value: r2(a.value), weight: total ? r2(a.value / total * 100) : null }));
  const ws = [...eq, ...mf].map(r => [total ? r.value / total : 0, r]).sort((a, b) => b[0] - a[0]);
  const sq = ws.reduce((a, [x]) => a + x * x, 0);
  const concentration = !total ? h.concentration : {
    count: ws.length, top: { name: ws[0][1].symbol || ws[0][1].fund, weight: r2(ws[0][0] * 100) },
    top5: r2(ws.slice(0, 5).reduce((a, [x]) => a + x, 0) * 100), effective: sq ? Math.round(1 / sq * 10) / 10 : null,
  };
  return {
    ...h, equity: eq, mf,
    totals: { equity: totals(eq, true), mf: h.totals.mf, all: totals([...eq, ...mf], false) },
    allocation, concentration,
  };
}
