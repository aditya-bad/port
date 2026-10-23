// selftest fixture: a scene whose animation ends long before its voice,
// with nothing driven by rt — the "paused, waiting for the narrator" case.
scene({
  title: 'A scene that stops',
  dur: 2,
  steps: [[0, 'This box appears in the first second, and then nothing else happens while this rather long sentence keeps going and going, on and on, for many seconds more than it should.']],
}, p => {
  const b = box(p, 600, 350, 400, 160, 'Just a box');
  return t => fade(b, t, 0.2);
});
