// 10. Everything that can fail: each service, while it's down.
{
  const cards = [
    ['product-api', 'Python', 'lens', '', 'Screens go dark. Nothing trades differently.',
      'If the product API goes down, the screens go dark, and nothing trades any differently.'],
    ['control', 'Go', 'trade', '', 'No changes possible. Running strategies keep running.',
      'If control goes down, nothing can be changed, but running strategies keep running.'],
    ['md-ingest', 'Rust', 'trade', '', 'The other source takes its instruments. Strategies that lose prices hold and report.',
      'If one market data source fails, the other source takes over its instruments. Strategies that lose their prices hold, and report it.'],
    ['md-sequencer', 'Rust', 'trade', '', 'The standby takes over: one active copy at a time.',
      'The sequencer runs as one active copy and a standby. If the active one dies, the standby takes over.'],
    ['computed-streams', 'Rust', 'trade', '', 'Its series are marked stale. Strategies that use them hold.',
      'If computed streams fails, its series are marked stale, and the strategies that use them hold.'],
    ['oms', 'Go', 'trade', 'Heimdall', 'Intents wait on the log. Nothing lost, nothing doubled.',
      'If Heimdall goes down, intents wait on the log. Nothing is lost, and nothing is doubled.'],
    ['broker-gw', 'Go', 'trade', 'Kraken', 'That broker’s accounts stop new entries and queue exits. Others carry on.',
      'If a broker gateway goes down, that broker’s accounts stop new entries and queue their exits. Other brokers carry on.'],
    ['sim-venue', 'Rust', 'trade', '', 'Paper orders queue. Live trading is not affected.',
      'If the sim venue goes down, paper orders queue. Live trading is not affected.'],
    ['integrity', 'Go', 'trade', '', 'Findings come late, never wrong.',
      'And if integrity goes down, its findings come late, but never wrong.'],
  ];
  const S = beats([
    ['The first video showed four breakdowns: a strategy crash, a dead machine, a zombie, and a broker outage. Here is every service, and what happens while it is down.', { focus: 'all' }],
    ...cards.map((c, i) => [c[5], { spot: ['c' + i], pad: 4 }]),
  ]);
  scene({ title: 'Everything that can fail: the services', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const cs = cards.map(([n, lang, part, nick, out], i) => {
      const x = 40 + (i % 3) * 516, y = 120 + Math.floor(i / 3) * 244;
      const g = named('c' + i, node(B, x, y, 488, n, lang, part, { nick, h: 226 }));
      const st = T(g, x + 470, y + 30, '', 'warnl', 'end');
      const tp = typer(g, x + 22, y + 112, out, 36, 'bigs', 30);
      const ok = check(M, x + 450, y + 190, true);
      return { g, st, tp, ok };
    });
    return (t, rt) => {
      cs.forEach(({ g }, i) => pop(g, t, 0.3 + i * (S.to[0] - 1.0) / cs.length));
      cs.forEach(({ g, st, tp, ok }, i) => {
        const a = S.at[i + 1], b = S.to[i + 1], d = b - a;
        const down = t > a + 0.2 && t < a + d * 0.45;
        g.classList.toggle('bad', down);
        typeAt(st, t, a + 0.2, a + 0.6, 'down', rt); if (t > a + d * 0.45) op(st, 0);
        tp.go(t, a + d * 0.3, b - 0.4, rt);
        pop(ok, t, b - 0.3);
      });
    };
  });
}

// 11. When a strategy itself fails.
tableScene('When a strategy itself fails', 'And when the strategy itself is what fails, this is how each case is seen, and what happens next.',
  [['what happens', 60, 22], ['how it is seen', 420, 38], ['then', 880, 62]], [
    ['An exception or panic', 'the SDK sends Error, with the batch and the stack, and exits', 'restart from the last snapshot, and catch up on the journal',
      'An exception or a panic in a handler: the SDK sends Error, with the batch and the stack, and exits. The supervisor restarts it from the last snapshot and catches it up.'],
    ['The process dies', 'the socket closes, or an exit code', 'restart, and catch up',
      'If the process simply dies, its socket closes, and it is restarted and caught up the same way.'],
    ['A hang', 'no Done within its budget', 'killed, then restarted',
      'A hang shows up as no Done within the budget. It is killed and restarted.'],
    ['Measured catch-up', 'the prototype, killed at batch 458', 'restored from snapshot 400: 57 batches caught up in 14 ms (Go), 38 ms (Python); 1 duplicate refused',
      'Measured in the prototype: a strategy killed at batch 458 was restored from snapshot 400, and caught up 57 batches in 14 milliseconds in Go, and 38 in Python. The one order it made again was refused as a duplicate.'],
    ['It lost track of a leg', 'its legs checked against the book after every failure, and every 5 minutes', 'restart clean from the book, as today',
      'A strategy that has lost track of a leg is caught by checking its legs against the book, after every failure and every five minutes. It restarts clean from the book, as today’s app does.'],
    ['The same error twice', 'the same error, on the same batch, twice', 'quarantined: no new entries, no restart loop; square-off, stops and the kill switch still work',
      'The same error on the same batch twice is a bug, not bad luck. The deployment is quarantined: no new entries and no restart loop, while the square-off, the stops and the kill switch still work.'],
    ['Different errors', 'a new error each time', 'restarts back off: 1, 2, 4, 8 minutes, then every 15 minutes',
      'Different errors make the restarts back off: 1, 2, 4 and 8 minutes, then every 15 minutes. That is today’s breaker, applied from outside the process.'],
    ['To reproduce one', 'on a laptop', 'linck replay --deployment D --from 200 --to 250',
      'And to reproduce one on a laptop: linck replay, with the deployment and a range of batches, say 200 to 250.'],
  ]);
