// Code on the left, a note per step on the right, each pointing at its lines.
//   notes: [[said, first line, last line, note title, note text], …]
function codeScene(title, intro, code, notes, o = {}) {
  const S = beats([[intro, { focus: 'all' }, o.min0 || 1.2], ...notes.map(n => [n[0], {}])]);
  scene({ title, dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const cw = o.cw || 880, nx = cw + 80, nw = 1520 - cw - 40;
    const cb = codeBlock(B, 40, 116, cw, code, o.lh || 25, o.head || '', o.cls || 'mono');
    const nh = Math.min(118, (740) / notes.length);
    const ns = notes.map(([, a, b, h, txt], i) => {
      const y = 116 + i * (nh + 10), g = box(B, nx, y, nw, nh, '', { cls: 'chip' });
      T(g, nx + 18, y + 30, h, 'cl', 'start');
      const tp = typer(g, nx + 18, y + 58, txt, Math.floor((nw - 30) / 8.4), 'txt', 21);
      const ln = a == null ? null : line(L, `M${nx} ${y + nh / 2}C${nx - 40} ${y + nh / 2} ${cw + 80} ${cb.lineY(a)} ${cw + 44} ${cb.lineY(a)}`, 'ink', { arrow: false });
      return { g, tp, ln, a, b };
    });
    return (t, rt) => {
      pop(cb, t, 0.2);
      cb.type(t, 0.5, S.to[0] - 0.2, rt);
      ns.forEach(({ g, tp, ln, a, b }, i) => {
        const s = S.at[i + 1], e = S.to[i + 1];
        pop(g, t, s + 0.2); tp.go(t, s + 0.5, e - 0.3, rt);
        if (ln) reveal(ln, t, s + 0.3, s + 0.8);
        if (a != null) cb.hl(t, a, b, s + 0.2, e + 0.05);
        flash(g, t, s + 0.2, 0.7);
      });
    };
  });
}

// 3. What you write.
codeScene('What you write', 'Here is a whole strategy, in Python. When the SuperTrend on NIFTY’s 15-minute bars flips, it sells a weekly option, hedged with a cheaper one further out.', [
  'from linck import Strategy, State, on, param',
  'from linck.ind import supertrend',
  '',
  'class TrendSpread(Strategy):',
  '    width = param(200, min=100, max=500, label="Hedge width")',
  '    state = State(side=None)',
  '',
  '    def subscribe(self, ctx):',
  '        return [supertrend(ctx.index("NIFTY").bars("15m"), 10, 3)]',
  '',
  '    @on.bar',
  '    async def on_bar(self, ctx, st):',
  '        if st.flipped and st.trend != ctx.state.side:',
  '            chain = ctx.chain("NIFTY", expiry="this_week")',
  '            short = chain.by_premium("PE" if st.trend == "up" else "CE", 100)',
  '            hedge = short.further(self.width)',
  '            await ctx.orders.basket([hedge.buy(), short.sell()],',
  '                                    policy="hedge_first", tag="entry")',
  '            ctx.state.side = st.trend',
  '        else:',
  '            ctx.decide("entry", "no_action", f"trend {st.trend}, no flip")',
], [
  ['A param is a setting. Its type, its limits and its label become the deploy form in the app, so nobody edits code to change a number.', 4, 4, 'param → the deploy form', 'type, limits and label become the form in the app'],
  ['State is what the strategy remembers. The platform saves it after every batch, so a restart, a move or a replay picks up exactly here.', 5, 5, 'State → saved after every batch', 'a restart, a move or a replay picks up here'],
  ['subscribe says what it listens to. Here, a SuperTrend on 15-minute bars: a computed stream, worked out once for everyone who asks, not inside this strategy.', 7, 8, 'subscribe → a shared stream', 'computed once for everyone who asks'],
  ['A handler runs on each event it subscribed to: here, each new bar.', 10, 11, 'a handler per event', 'here: each new bar'],
  ['The option chain comes from ctx, and answers from the machine’s own cache. Picking the strike nearest 100 rupees makes no call to the broker.', 13, 15, 'ctx.chain → local', 'no broker call to pick a strike'],
  ['Orders are a basket, placed hedge first: the buy leg must fill before the sell leg goes. The strategy never names an order; the SDK does.', 16, 17, 'a basket, hedge first', 'buy fills first; the SDK names the orders'],
  ['And when it does nothing, it says why. ctx.decide writes the decision log behind Geek mode, so “why didn’t it trade?” always has an answer.', 19, 20, 'ctx.decide → the decision log', '“why didn’t it trade?” always has an answer'],
], { cw: 940, cls: 'lab' });

// 4. ctx: the only door.
{
  const S = beats([
    ['Everything a strategy touches goes through one object: ctx. It is the only door to the world.', { focus: 'all' }],
    ['ctx.now is the only clock: the time of the batch, not the machine’s. ctx.random is the only randomness, seeded for each deployment and day.', {}],
    ['ctx.state is what it remembers. ctx.index, the bars and the chain are the market, all answered on the machine.', {}],
    ['ctx.orders places orders and baskets. ctx.decide records what it decided and why. And ctx.report raises an issue about itself, with the day attached.', {}],
    ['Timers are events too: a square-off at 15:15 is a handler, like a bar.', {}],
    ['Swap what is behind ctx, and the same code runs live, in a backtest, or in a replay. The strategy can’t tell, and doesn’t need to.', {}],
  ]);
  scene({ title: 'ctx: the only door', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const cx = 800, cy = 470;
    const hub = G(B); E('circle', { cx, cy, r: 92, class: 'f-y' }, hub); E('circle', { cx, cy, r: 92, class: 'ln c-ink' }, hub); T(hub, cx, cy + 14, 'ctx', 'huge'); op(hub, 0);
    const door = typer(B, cx, cy + 140, 'the only door to the world', 40, 'cl', 24, 'middle');
    const sp = [['ctx.now', 'the only clock', 1], ['ctx.random', 'the only randomness', 1], ['ctx.state', 'what it remembers', 2], ['ctx.index · bars', 'the market, local', 2],
      ['ctx.chain', 'option chains, local', 2], ['ctx.orders', 'orders and baskets', 3], ['ctx.decide', 'the decision log', 3], ['ctx.report', 'an issue, with its day', 3], ['@on.time', 'timers: 15:15 square-off', 4]];
    const nodes = sp.map(([a, b, st], i) => {
      const ang = -Math.PI / 2 + i * (2 * Math.PI / sp.length), x = cx + Math.cos(ang) * 330, y = cy + Math.sin(ang) * 270;
      const g = box(B, x - 150, y - 42, 300, 84, a, { sub: b, cls: 'chip', lcls: 'lab' });
      const l = line(L, `M${cx + Math.cos(ang) * 96} ${cy + Math.sin(ang) * 96}L${x - Math.cos(ang) * 60} ${y - Math.sin(ang) * 40}`, 'ink', { arrow: false });
      return { g, l, st, path: l };
    });
    const envs = ['live', 'backtest', 'replay'].map((s, i) => box(B, 1270, 130 + i * 70, 270, 56, s, { cls: 'out', lcls: 'lab' }));
    const behind = T(B, 1405, 120, 'behind ctx', 'fl'); op(behind, 0);
    const dots = nodes.map(n => stream(M, n.path, 'b', 1, 1.6, 5));
    return (t, rt) => {
      pop(hub, t, 0.4); door.go(t, 0.9, S.to[0] - 0.2, rt); if (t > S.at[1]) door.go(t, 1e9, 1e9);
      nodes.forEach(({ g, l, st }, i) => {
        const a = st === 1 ? S.at[1] + 0.3 + (i % 2) * 1.6 : S.at[st] + 0.3 + (i - sp.findIndex(x => x[2] === st)) * 1.0;
        pop(g, t, a); reveal(l, t, a, a + 0.4); flash(g, t, a, 0.8);
        dots[i](rt, t > a + 0.5 ? 0 : 1e9);
      });
      pop(behind, t, S.at[5] + 0.2);
      envs.forEach((e, i) => pop(e, t, S.at[5] + 0.4 + i * 0.3));
      const cyc = Math.floor(((t - S.at[5] - 1.4) / 1.4)) % 3;
      envs.forEach((e, i) => e.classList.toggle('hot', t > S.at[5] + 1.4 && cyc === i));
    };
  });
}

// 5. The manifest.
codeScene('The manifest: linck.toml', 'Every strategy has a manifest, linck.toml, versioned with its code.', [
  '# linck.toml: versioned with the code',
  '[strategy]',
  'name = "short-straddle"',
  'entry = "straddle:ShortStraddle"',
  'sdk = ">=1.4,<2"',
  'runtime = "python3.12-r7"',
  '',
  '[resources]',
  'cpu_ms_per_event = 50',
  'memory_mb = 256',
  '',
  '[markets]',
  'underlyings = ["NIFTY", "BANKNIFTY"]',
  'segments = ["NFO"]',
], [
  ['It names the strategy, its entry point, and the SDK versions it works with.', 1, 4, 'what it is', 'its name, its entry point, the SDK range'],
  ['runtime pins the exact image it runs in. That pin is part of determinism: the same code, in the same image, gives the same decisions.', 5, 5, 'runtime → a pinned image', 'part of determinism'],
  ['resources is its budget: CPU time per event, and memory. Certification checks the strategy stays inside them.', 7, 9, 'resources → its budget', 'checked by certification'],
  ['markets says which underlyings and segments it trades: here, NIFTY and BANKNIFTY options.', 11, 13, 'markets', 'what it trades'],
], { cw: 760 });

// 6. The same strategy in Go.
codeScene('The same strategy in Go', 'The same strategy, in Go. It is the same idea, in Go’s own shapes.', [
  'package main  // linck.toml says language = "go"',
  'import "linck.dev/sdk/linck"',
  '',
  'type TrendSpread struct {',
  '    Width int    `param:"200" min:"100" max:"500"`',
  '    Side  string `state:""`',
  '}',
  'func (s *TrendSpread) Subscribe(ctx *linck.Ctx) []linck.Sub {',
  '    return []linck.Sub{linck.Supertrend(ctx.Index("NIFTY").Bars("15m"), 10, 3)}',
  '}',
  'func (s *TrendSpread) OnBar(ctx *linck.Ctx, st linck.SupertrendValue) error {',
  '    if !st.Flipped || st.Trend == s.Side {',
  '        ctx.Decide("entry", "no_action", "trend "+st.Trend+", no flip")',
  '        return nil',
  '    }',
  '    short := ctx.Chain("NIFTY", linck.ThisWeek).ByPremium(linck.OptionAgainst(st.Trend), 100)',
  '    s.Side = st.Trend',
  '    return ctx.Orders.Basket(linck.HedgeFirst, "entry",',
  '        short.Further(s.Width).Buy(), short.Sell())',
  '}',
  'func main() { linck.Run(&TrendSpread{}) }',
], [
  ['Settings and state are struct fields, tagged param and state. The tags carry the limits, so the deploy form is the same as Python’s.', 3, 6, 'param and state tags', 'the same deploy form as Python'],
  ['Subscribe asks for the same computed stream, and OnBar handles each bar.', 7, 10, 'Subscribe, OnBar', 'the same stream, the same events'],
  ['Doing nothing is still a decision, with its reason.', 11, 13, 'Decide', 'no action, with a reason'],
  ['The chain is local, the basket is hedge first, and the SDK names the orders.', 15, 18, 'chain and basket', 'local; hedge first'],
  ['And main hands the strategy to the SDK. linck.Run speaks the protocol on the socket the supervisor gives it; you never write the loop.', 20, 20, 'main → linck.Run', 'the SDK owns the loop'],
  ['Go has one trap: map order is random, so the SDK’s lint flags ranging over a map, and goroutines, inside a handler. TypeScript’s trap is the clock: Date and Math.random are replaced by ctx.now and ctx.random.', null, null, 'each language’s trap', 'Go: no map ranging, no goroutines in handlers · TypeScript: no Date, no Math.random'],
], { cw: 1060, cls: 'lab', lh: 26 });
