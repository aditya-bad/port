// Running Linck, as a narrated explainer: how it is deployed and released,
// how it changes without stopping trading, the small flows in a strategy's
// life, what happens when each part fails, a trading day, how it's watched,
// and how it grows. Colours as in the architecture video: LinckTrade blue,
// LinckLens purple, Linck Platform orange; Rust red, Go green, Python yellow.

// Text that types on line by line between a and b, as it's said.
function typer(p, x, y, s, n, cls = 'txt', lh = 22, anchor = 'start') {
  const lines = wrapLines(s, n), els = lines.map((l, i) => { const e = T(p, x, y + i * lh, '', cls, anchor); op(e, 0); return e; });
  const total = lines.reduce((a, l) => a + l.length, 0) || 1;
  return {
    lines, els, h: lines.length * lh,
    go(t, a, b, rt) { let c = a; lines.forEach((l, i) => { const d = (b - a) * l.length / total; if (t < c) txt(els[i], ''); else typeOn(els[i], t, c, c + d, l, rt); op(els[i], els[i].textContent ? 1 : 0); c += d; }); },
    full() { lines.forEach((l, i) => { txt(els[i], l); op(els[i], 1); }); },
  };
}

// Several lines, each its own typer, typed one after another.
function typeList(p, x, y, lines, n, cls = 'txt', lh = 26, gap = 6) {
  let yy = y;
  const ts = lines.map(s => { const tp = typer(p, x, yy, s, n, cls, lh); yy += tp.h + gap; return tp; });
  const total = lines.reduce((a, s) => a + s.length, 0) || 1;
  return {
    ts, h: yy - y,
    go(t, a, b, rt) { let c = a; ts.forEach((tp, i) => { const d = (b - a) * lines[i].length / total; tp.go(t, c, c + d, rt); c += d; }); },
  };
}

// A table told row by row: each row's first cell is there from the start,
// the rest type on while that row is said.
//   cols: [[header, x, chars a line]], rows: [[first, …cells, said]]
function tableScene(title, intro, cols, rows, o = {}) {
  const S = beats([[intro, { focus: 'all' }, o.min0 || 1.2], ...rows.map((r, i) => [r[r.length - 1], { spot: ['r' + i], pad: 3 }])]);
  scene({ title, dur: S.dur, steps: S.steps }, p => {
    const B = G(p);
    const y0 = o.y0 || 160;
    const hdr = G(B); cols.forEach(([h, x]) => T(hdr, x, y0 - 18, h, 'fl', 'start')); op(hdr, 0);
    const rh = o.rh || Math.min(104, (850 - y0) / rows.length);
    const rs = rows.map((r, i) => {
      const y = y0 + i * rh, g = named('r' + i, box(B, 40, y, 1520, rh - 10, '', { cls: 'chip' }));
      const cells = cols.map(([, x, n], j) => {
        const nl = wrapLines(r[j], n).length, cy = y + (rh - 10) / 2 + 6 - (nl - 1) * 10;
        return typer(g, x, cy, r[j], n, j === 0 ? 'cl' : 'txt', 20);
      });
      cells[0].full();
      return { g, cells };
    });
    return (t, rt) => {
      fade(hdr, t, 0.2);
      rs.forEach(({ g }, i) => pop(g, t, 0.3 + i * Math.max(0.1, (S.to[0] - 1) / rs.length)));
      rs.forEach(({ g, cells }, i) => {
        flash(g, t, S.at[i + 1], 0.6);
        const rest = cells.slice(1), n = rest.length, a = S.at[i + 1] + 0.2, b = S.to[i + 1] - 0.3;
        rest.forEach((c, j) => c.go(t, a + (b - a) * j / n, a + (b - a) * (j + 1) / n, rt));
      });
    };
  });
}

// A panel: a dashed frame with a heading, for content that changes per step.
function panel(p, x, y, w, h, title) {
  const g = box(p, x, y, w, h, '', { cls: 'frame' });
  if (title) T(g, x + 20, y + 30, title, 'fl', 'start');
  return g;
}

// typeOn, hidden until it starts (no cursor blinking on an empty line).
function typeAt(el, t, a, b, s, rt) {
  if (t < a) txt(el, ''); else typeOn(el, t, a, b, s, rt);
  op(el, el.textContent ? 1 : 0);
}
