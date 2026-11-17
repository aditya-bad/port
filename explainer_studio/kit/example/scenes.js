// The example: two short scenes that use the camera, the spotlight and
// every effect in life.js. Copy this folder to start a new explainer.

scene({
  title: 'A message, from your phone to the server',
  dur: 14,
  steps: [
    [0, 'Here is your phone, and a server that does the work.', { focus: 'all' }],
    [1.6, 'You tap a button, and a message sets off, along the line, to the server.', { spot: ['phone', 'server'] }],
    [4.2, 'Right now the server is busy with someone else, so the message waits its turn.', { focus: ['server', 'busy'] }],
    [7.6, 'The server finishes, picks the message up, and does the job.', { focus: 'all' }],
    [10.2, 'Done! The answer comes back to your phone, and the server has a nap until the next one.'],
  ],
}, p => {
  const L = G(p), B = G(p), M = G(p);
  const phone = named('phone', box(B, 120, 330, 220, 140, 'Your phone', { sub: 'tap', cls: 'out' }));
  const srv = named('server', box(B, 820, 300, 320, 200, 'Server', { sub: 'does the work', cls: 'key' }));
  const there = line(L, 'M340 380H816', 'b'), back = line(L, 'M820 440H344', 'g');
  const msg = packet(M, 'save my note', 'b');
  const ans = packet(M, 'saved ✓', 'g');
  const busy = named('busy', bubble(B, 980, 296, 'busy…'));
  const drops = sweat(B, 1150, 300);
  const conf = burst(M, 230, 330, { seed: 4 });
  const nap = zzz(B, 1150, 300);
  const flow = stream(M, there, 'b', 3, 1.4);
  const cog = gear(B, 1090, 345, 16);                    // turns while the server does the job
  return (t, rt) => {
    pop(phone, t, 0.2); pop(srv, t, 0.6);
    reveal(there, t, 1.6, 2.2);
    travel(msg, there, t, 2.0, 4.0, true);
    if (t > 4.0 && t < 8.0) { tr(msg, 760, 380); op(msg, 1); bob(msg._in, rt, 3); }   // waiting at the door
    if (t >= 8.0) travel(msg, there, t, 8.0, 8.6, false);
    squash(msg._in, t, 4.0);
    busy.go(t, 4.4, 7.6, rt);
    drops.go(rt, t > 4.4 && t < 7.6);
    wobble(srv, rt, t > 4.4 && t < 7.6 ? 1.2 : 0);
    flash(srv, t, 8.6, 1.2);
    spin(cog, rt); op(cog, t > 8.6 && t < 10.4 ? 1 : 0);
    reveal(back, t, 10.2, 10.8);
    travel(ans, back, t, 10.4, 12.0, true);
    squash(ans._in, t, 12.0);
    conf.go(t, 12.0);
    nap.go(rt, t > 12.4);
    flow(t < 4 ? -1 : rt, 1e9);                           // nothing flows until it's said
  };
});

scene({
  title: 'Two copies, one stamp',
  dur: 15,
  steps: [
    [0, 'Two copies of one program both think they\u2019re in charge. The old one lost contact but keeps running: a ghost.', { focus: 'all' }],
    [3.0, 'Each copy sends an order. Each order carries a number: which copy sent it.'],
    [5.6, 'The order desk only accepts the newest copy.', { focus: ['desk', 'stamp'] }],
    [6.6, 'So the ghost’s order gets stamped: refused!'],
    [9.4, 'The real copy’s order goes through.', { focus: 'all' }],
    [11.4, 'Then a new version launches, and the ghost finally fades away.'],
  ],
}, p => {
  const L = G(p), B = G(p), M = G(p);
  const old = named('old', box(B, 100, 200, 300, 130, 'Old copy', { sub: 'copy 1 · lost contact' }));
  const neu = named('new', box(B, 100, 520, 300, 130, 'New copy', { sub: 'copy 2', cls: 'key' }));
  const desk = named('desk', box(B, 900, 340, 320, 170, 'Order desk', { sub: 'newest copy only', cls: 'key' }));
  const boo = named('ghost', ghost(B, 440, 230));
  const pa = line(L, 'M400 265C640 265 700 400 896 400', 'wt'), pb = line(L, 'M400 585C640 585 700 460 896 460', 'g');
  const oa = packet(M, 'order · 1', 'wt'), ob = packet(M, 'order · 2', 'g');
  const no = named('stamp', stamp(M, 1060, 250, 'Refused'));
  const yes = sparkle(M, 1060, 425, { r: 140 });
  const pr = line(L, 'M150 820C400 760 600 760 900 700', 'pu', { arrow: false });
  const rk = rocket(M);
  const counter = T(B, 1060, 600, '', 'big');
  return (t, rt) => {
    pop(old, t, 0.2); pop(neu, t, 0.5); pop(desk, t, 0.9);
    fade(boo, t, 1.6, 0.6, 12.6); boo.go(rt);
    [pa, pb].forEach((l, i) => reveal(l, t, 3.0 + i * 0.2, 3.6 + i * 0.2));
    travel(oa, pa, t, 3.6, 5.6, t < 7.0);
    if (t >= 7.0) { placeOn(oa, pa, 1 - 0.55 * ease(k(t, 7.0, 9.2))); op(oa, 1 - k(t, 8.4, 9.2)); }   // sent back to the ghost, fading
    squash(oa._in, t, 5.6);
    flash(desk, t, 5.8, 0.7);                              // the desk reads the number
    no.go(t, 6.6);
    travel(ob, pb, t, 9.4, 10.6, true);
    squash(ob._in, t, 10.6);
    yes.go(t, 10.6);
    countUp(counter, t, 10.6, 11.4, 0, 1, v => `placed ${Math.round(v)}`);
    pop(counter, t, 10.4);
    reveal(pr, t, 11.4, 11.6);
    op(pr, 0);
    rk.go(pr, t, 11.6, 13.6, rt);
    if (t > 12.6) quake(t, 12.6, 0.25, 3);
  };
});
