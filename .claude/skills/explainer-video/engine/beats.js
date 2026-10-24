// Steps timed from their words, so the clock rarely has to hold: each step
// gets about 0.2 s of animation per spoken word (the voice takes about
// 0.4 s a word), and at least `min` seconds.
//   const S = beats([
//     ['Here is the whole system.', { focus: 'all' }],
//     ['The order desk checks every order.', { spot: ['desk'] }, 3],   // at least 3 s of motion
//   ]);
//   scene({ title: '…', dur: S.dur, steps: S.steps }, p => { … pop(desk, t, S.at[1]) … S.to[1] … });
// S.at[i] is when step i's motion starts, S.to[i] when it ends.
function beats(list, per = 0.2) {
  const steps = [], at = [], to = [];
  const r2 = x => Math.round(x * 100) / 100;
  let t = 0;
  for (const [text, o = {}, min = 1.2] of list) {
    steps.push([r2(t), text, o]);
    at.push(r2(t));
    t += Math.max(min, text.split(/\s+/).length * per);
    to.push(r2(t));
  }
  return { steps, at, to, dur: r2(t) };
}
