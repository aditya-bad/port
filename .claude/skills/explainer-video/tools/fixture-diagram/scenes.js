// selftest: diagram.js and beats.js. Scene 1: a service whose components
// light up in turn, timed by beats(), with a stream that loops every 1.0 s
// (the checker must not mistake it for a pause). Scene 2: a flow told hop
// by hop. Scene 3: rows 10 px apart, spotlit one at a time with pad 3.
{
  const S = beats([
    ['A service with three parts.', { focus: 'all' }],
    ['First it reads, then it checks, then it writes, and the dots keep flowing all the while.', { spot: ['svc'] }],
  ]);
  scene({ title: 'A service', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const s = named('svc', svc(B, 200, 200, 700, 300, 'orders', { lang: 'Go', kind: 'Deployment · 3', part: 'trade', does: 'Checks and records every order.', comps: ['read', 'check', 'write'], cols: 3 }));
    const d = db(B, 1100, 260, 260, 160, 'Postgres', 'orders, outbox');
    const l = link(L, s, d, 'b', { label: 'one transaction' });
    const f = stream(M, l, 'b', 2, 1.0, 6);
    return (t, rt) => { pops([s, d], t, 0.2, 0.4); show(l, t, 1.0); f(rt, t > 1.4 ? 0 : 1e9); walk(Object.values(s.comp), t, S.at[1], S.to[1]); };
  });
}
flowScene('A flow', (L, B, M) => {
  const a = node(B, 100, 300, 360, 'sender', 'Rust', 'trade', { sub: 'makes a message' });
  const b = node(B, 700, 300, 360, 'receiver', 'Python', 'lens', { sub: 'reads it' });
  const c = box(B, 1200, 300, 300, 96, 'screen', { sub: 'shows it', cls: 'out' });
  return { edges: [[link(L, a, b, 'b', { label: 'gRPC' }), a, b], [link(L, b, c, 'g', { label: 'SSE' }), b, c, 'g']] };
}, [
  ['One message, from a sender to a screen.', { focus: 'all' }],
  ['The sender passes it over gRPC to the receiver, which pushes it to the screen.', {}, [0, 1], 'hello'],
]);
{
  const S = beats([['Three rows, close together.', { focus: 'all' }], ['The middle one.', { spot: ['r1'], pad: 3 }, 3], ['The last one.', { spot: ['r2'], pad: 3 }, 3]]);
  scene({ title: 'Rows', dur: S.dur, steps: S.steps }, p => {
    const rows = [0, 1, 2].map(i => named('r' + i, box(G(p), 200, 300 + i * 70, 1200, 60, `row ${i + 1}`, { cls: 'chip' })));
    return (t, rt) => { pops(rows, t, 0.2, 0.3); rows.forEach((r, i) => flash(r, t, S.at[i] || 0, 0.5)); };
  });
}
