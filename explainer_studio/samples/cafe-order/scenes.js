// A sample explainer for trying Explainer Studio: how a café gets your
// coffee to you. Three scenes, about a minute and a half with the voice.
// The times in scene 2 are typical estimates, said as such.

scene({
  title: 'From your order to the barista',
  dur: 15,
  steps: [
    [0, 'This is a café: a counter, a screen in the kitchen, and a barista.', { focus: 'all' }],
    [2.4, 'You order a flat white. The cashier types it in, and a ticket appears on the kitchen screen.', { spot: ['counter', 'screen'] }],
    [5.6, 'The ticket joins a queue. Drinks are made in the order they were asked for: first in, first out.', { focus: ['screen'] }],
    [9.0, 'Right now there are three tickets ahead of yours, so it waits its turn.'],
    [12.0, 'The barista takes the oldest ticket, the latte, and starts on it.', { focus: 'all' }],
  ],
}, p => {
  const L = G(p), B = G(p), M = G(p);
  const counter = named('counter', box(B, 60, 370, 250, 130, 'Counter', { sub: 'you order here', cls: 'out' }));
  const screen = named('screen', box(B, 470, 250, 600, 380, 'Kitchen screen', { top: true, cls: 'frame', sub: '' }));
  const barista = named('barista', box(B, 1240, 360, 300, 150, 'Barista', { sub: 'makes the drinks', cls: 'key' }));
  const toScreen = line(L, 'M310 435H500', 'b');
  const toBarista = line(L, 'M1070 435H1236', 'g');
  const slots = [970, 850, 730, 610];                   // the front of the queue is on the right
  const names = ['latte', 'tea', 'mocha', 'flat white'];
  const tk = names.map((n, i) => packet(M, n, i === 3 ? 'b' : 'wt', { w: 100 }));
  const order = slots.map((x, i) => T(B, x, 500, ['1st', '2nd', '3rd', '4th'][i], 'bs'));
  const ahead = T(M, 610, 380, '3 ahead', 'lab');
  const say = bubble(B, 185, 366, 'one flat white');
  const cog = gear(B, 1500, 400, 16);
  const fifo = line(L, `M${slots[3] - 40} 560H${slots[0] + 40}`, 'ink');
  const fifoL = T(B, (slots[0] + slots[3]) / 2, 600, 'made in this order', 'bs');
  return (t, rt) => {
    pop(counter, t, 0.2); pop(screen, t, 0.7); pop(barista, t, 1.2);
    tk.slice(0, 3).forEach((p, i) => { tr(p, slots[i], 435); op(p, t < 1.6 ? 0 : 1); pop(p._in, t, 1.6 + i * 0.3); });
    // yours: typed in at the counter, then along to the back of the queue
    say.go(t, 2.6, 5.4, rt);
    reveal(toScreen, t, 2.8, 3.4);
    const f = ease(k(t, 3.4, 5.2));
    tr(tk[3], lerp(330, slots[3], f), 435); op(tk[3], t < 3.4 ? 0 : 1);
    squash(tk[3]._in, t, 5.2);
    // the order they'll be made in
    order.forEach((o, i) => { pop(o, t, 5.9 + i * 0.6); });
    reveal(fifo, t, 6.0, 8.4); fade(fifoL, t, 7.0, 0.5);
    tk.forEach((p, i) => flash(p, t, 5.9 + i * 0.6, 0.6));
    // waiting its turn
    pop(ahead, t, 9.2);
    if (t > 5.4) bob(tk[3]._in, rt, 3);              // waiting its turn
    // the oldest goes to the barista
    reveal(toBarista, t, 12.0, 12.4);
    if (t >= 12.4) { const g = ease(k(t, 12.4, 13.6)); tr(tk[0], lerp(slots[0], 1300, g), 435); op(tk[0], 1 - k(t, 13.4, 13.8)); }
    op(order[0], t < 12.4 ? Math.min(1, k(t, 5.9, 6.2)) : 1 - k(t, 12.4, 12.8));
    flash(barista, t, 13.6, 1.0);
    spin(cog, rt); op(cog, t > 13.6 ? 1 : 0);
  };
});

scene({
  title: 'Making one drink: where the minute goes',
  dur: 18,
  steps: [
    [0, 'Making one drink takes about a minute. Here is where that minute goes.', { focus: 'all' }],
    [3.0, 'First the coffee is ground and pressed into the basket: about ten seconds.'],
    [6.4, 'Then the espresso runs through, for about thirty seconds. While it runs, the barista steams the milk.'],
    [11.6, 'Doing both at once is the trick. One after the other, the drink would take twenty seconds longer.', { spot: ['milk', 'later', 'extra'] }],
    [14.6, 'Last, the milk is poured: about fifteen seconds. These times are typical, not measured.', { focus: 'all' }],
  ],
}, p => {
  const L = G(p), B = G(p), M = G(p);
  const X0 = 260, PX = 20;                               // 0 s at x 260, 20 px a second: 60 s ends at 1460
  const x = s => X0 + s * PX;
  const axis = line(L, `M${x(0)} 760H${x(62)}`, 'ink', { arrow: true });
  const ticks = [0, 10, 20, 30, 40, 50, 60].map(s => T(B, x(s), 800, `${s} s`, 'bs'));
  const row = (y, a, b, label, cls, name) => named(name, box(B, x(a), y, (b - a) * PX, 64, label, { cls, rx: 8 }));
  const grind = row(200, 0, 10, 'Grind', 'chip', 'grind');
  const shot = row(320, 10, 40, 'Espresso runs', 'key', 'shot');
  const milk = row(440, 15, 35, 'Steam the milk', 'chip', 'milk');
  const later = row(560, 40, 60, 'milk, if done after', 'bad', 'later');
  const pour = row(440, 40, 55, 'Pour', 'chip', 'pour');
  const extra = named('extra', T(M, x(60) + 20, 600, '+20 s', 'warnl', 'start'));
  const head = E('line', { x1: 0, y1: 170, x2: 0, y2: 770, class: 'ln c-r' }, M);
  const clock = T(M, 1380, 150, '0 s', 'big', 'end');
  const cog = gear(M, x(25), 300, 14);
  const ok = sparkle(M, x(55), 470, { r: 90 });
  const drink = packet(M, 'one flat white', 'b', { w: 150 });
  // the playhead's time on the drink's clock, for each moment of the story
  const at = t => t < 3.0 ? 0 : t < 6.4 ? lerp(0, 10, k(t, 3.0, 6.4)) : t < 11.6 ? lerp(10, 35, k(t, 6.4, 11.6))
    : t < 14.6 ? lerp(35, 40, k(t, 11.6, 14.6)) : lerp(40, 55, k(t, 14.6, 17.4));
  return (t, rt) => {
    tr(drink, 360, 140); op(drink, 1); pop(drink._in, t, 0.4); bob(drink._in, rt, 3);   // the drink being made
    reveal(axis, t, 0.2, 1.4);
    ticks.forEach((e, i) => fade(e, t, 0.4 + i * 0.12, 0.3));
    const s = at(t);
    tr(head, x(s), 0); op(head, t < 2.8 ? 0 : 1);
    txt(clock, `${Math.round(s)} s`); op(clock, t < 0.6 ? 0 : 1);
    pop(grind, t, 3.0); pop(shot, t, 6.4); pop(milk, t, 7.8);
    spin(cog, rt); op(cog, s > 10 && s < 40 ? 1 : 0);
    pop(extra, t, 12.6);
    op(later, t < 11.8 ? 0 : t < 14.6 ? Math.min(1, k(t, 11.8, 12.4)) : 1 - 0.65 * k(t, 14.6, 15.2));
    flash(milk, t, 11.8, 1.2);
    pop(pour, t, 14.6);
    ok.go(t, 17.2);
  };
});

scene({
  title: 'Called out, and done',
  dur: 14,
  steps: [
    [0, 'Ticket by ticket the queue moves on, and yours reaches the front.', { focus: 'all' }],
    [3.6, 'The barista makes it: the same minute as every other drink.'],
    [7.4, 'Then calls your name.', { focus: 'all' }],
    [9.0, 'You pick it up. Done!'],
    [11.2, 'Lots of systems that serve many people work like this café: requests wait in a queue and are served in turn.'],
  ],
}, p => {
  const L = G(p), B = G(p), M = G(p);
  const screen = named('screen', box(B, 60, 250, 520, 380, 'Kitchen screen', { top: true, cls: 'frame' }));
  const barista = named('barista', box(B, 700, 360, 300, 150, 'Barista', { sub: 'makes the drinks', cls: 'key' }));
  const you = named('you', box(B, 1260, 360, 260, 150, 'You', { sub: 'at the pick-up end', cls: 'out' }));
  const toB = line(L, 'M580 435H696', 'g'), toYou = line(L, 'M1000 435H1256', 'g');
  const slots = [470, 350, 230];
  const tk = ['tea', 'mocha', 'flat white'].map((n, i) => packet(M, n, i === 2 ? 'b' : 'wt', { w: 100 }));
  const cog = gear(B, 960, 400, 16);
  const clock = named('clock', T(B, 850, 580, '', 'big'));
  const call = bubble(B, 850, 356, 'Flat white for Sam!');
  const cup = packet(M, '☕ flat white', 'g', { w: 150 });
  const done = stamp(M, 1390, 280, 'Done', 'g');
  const yay = burst(M, 1390, 360, { seed: 7 });
  // when each ticket leaves for the barista
  const leave = [0.4, 1.8, 3.6];
  const flowB = stream(M, toB, 'g', 2, 1.4), flowY = stream(M, toYou, 'g', 3, 1.4);
  return (t, rt) => {
    pop(screen, t, 0.1); pop(barista, t, 0.2); pop(you, t, 0.3);
    op(toB, 1); op(toYou, t < 9.0 ? 0.25 : 1);
    tk.forEach((p, i) => {
      // up one slot (120 px) each time a ticket ahead leaves, then off to the barista
      let n = 0;
      for (let j = 0; j < i; j++) n += ease(k(t, leave[j] + 0.3, leave[j] + 1.0));
      if (t < leave[i]) { tr(p, slots[0] - 120 * (i - n), 435); op(p, 1); bob(p._in, rt, 2.5, 2.2, i); }
      else { const g = ease(k(t, leave[i], leave[i] + 0.9)); tr(p, lerp(slots[0], 760, g), 435); op(p, 1 - k(t, leave[i] + 0.6, leave[i] + 0.9)); }
    });
    // making yours
    spin(cog, rt); op(cog, t > 4.0 && t < 7.4 ? 1 : 0);
    countUp(clock, t, 4.0, 7.2, 0, 60, v => `${Math.round(v)} s`); op(clock, t < 3.8 ? 0 : 1 - k(t, 9.0, 9.4));
    flash(barista, t, 4.0, 3.2);
    // called out
    call.go(t, 7.4, 9.2, rt);
    // carried over and picked up
    const c = ease(k(t, 9.0, 10.0));
    tr(cup, lerp(1000, 1390, c), lerp(435, 560, c)); op(cup, t < 9.0 ? 0 : 1);
    squash(cup._in, t, 10.0);
    if (t > 10.4) bob(cup._in, rt, 2.5);
    flowB(t < 11.2 ? -1 : rt, 1e9); flowY(t < 11.2 ? -1 : rt, 1e9);   // and the next orders keep coming
    done.go(t, 10.0);
    yay.go(t, 10.0);
    flash(screen, t, 11.6, 1.0); flash(barista, t, 12.4, 1.0);
  };
});
