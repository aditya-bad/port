// selftest fixture: a spotlight typed by hand that cuts through a box
// (check.mjs must warn), the same spotlight by name (it must fit the box),
// and a name nothing has (it must fail, saying which name).
const two = p => {
  const a = named('a', box(p, 100, 350, 300, 150, 'Phone'));
  const s = named('srv', box(p, 820, 300, 320, 200, 'Server'));
  return t => { fade(a, t, 0.1); fade(s, t, 0.2); };
};
scene({ title: 'Typed by hand', dur: 4, steps: [[0, 'The spotlight was typed as numbers, and it stops halfway across the server box.', { spot: [60, 300, 980, 200] }]] }, two);
scene({ title: 'By name', dur: 4, steps: [[0, 'Now the spotlight is the phone and the server, by name, so it fits them both.', { spot: ['a', 'srv'] }]] }, two);
scene({ title: 'A missing name', dur: 4, steps: [[0, 'This one points the camera at something that has no such name.', { focus: ['nope'] }]] }, two);
scene({ title: 'Something leaves the light', dur: 4, steps: [[0, 'The spotlight is on the phone, but the message travels out of it to the server.', { spot: ['a'] }]] }, p => {
  const a = named('a', box(p, 100, 350, 300, 150, 'Phone'));
  const s = box(p, 820, 300, 320, 200, 'Server');
  const ln = line(p, 'M400 420H816', 'b'), m = packet(p, 'message', 'b');
  return t => { fade(a, t, 0.1); fade(s, t, 0.2); reveal(ln, t, 0.3, 0.6); travel(m, ln, t, 0.6, 3.6, true); };
});
