---
name: explainer-video
description: Teach or explain something as a narrated, animated explainer — an SVG animation page with a voice-over and captions (one self-contained HTML file that plays in any browser and in the claude.ai viewer), and MP4 videos when wanted. Use when the user asks for an explanation "as a video", "in the video format", "animated", "narrated", "visually, not text", or names this skill.
---

# Explainer video

You turn a question into a short narrated animation: scenes on a 1600×900
SVG stage, each told in steps; every step has a sentence or two of voice
(generated offline) with captions, and its own motion. The output is one
HTML file (works offline, light and dark, phone and desktop) and, when
wanted, an MP4 per scene plus one of the whole thing.

The owner liked this format for learning. Three things matter most to them:
**it must be clear**, **it must never look paused** (waiting for the
narrator to catch up), and **nothing may jitter**: anything that has
arrived stays put unless it's meant to move. Some playful life (a stamp slamming down, confetti, a
snoring idle process) is welcome where it makes the point land, not
everywhere.

## Workflow

`S` below is this skill's folder (`.claude/skills/explainer-video`), `W` a
project folder in your scratchpad, e.g. `<scratchpad>/explainers/<slug>`.

1. **Get the facts first.** Read the code, docs or data the answer rests
   on. Every number on screen or in the voice comes from a source; say
   which numbers are measured and which are estimates, the way the rest of
   this repo does.
2. **Plan on paper** (in your head is fine): 3–8 scenes, one idea each; per
   scene 3–7 steps; for each step, the sentence and what moves while it is
   said. Pick at most one or two moments of life per scene, tied to meaning
   (see the table below).
3. **Set up** (once per session, about 20 s): `eval "$(bash $S/tools/setup.sh | grep =)"`
   then `export EV_HOME FF`. This gives you `$EV_PY` (a Python with the
   voice) and `$FF` (a full ffmpeg). Nothing goes into the repo.
4. **Start from the example:** `cp -r $S/example $W`, then write
   `$W/scenes.js` and `$W/meta.json` (title, slug, one-line description,
   and `say` pronunciations, e.g. `[["P&L", "P and L"], ["NIFTY", "Nifty"]]`).
5. **Look before you voice:** `node $S/tools/steps.mjs $W/scenes.js > $W/steps.json`,
   `python3 $S/tools/build.py $W --no-voice`, `node $S/tools/check.mjs $W/out/<slug>.html --fast`.
   Open every `$W/check/sheet-NN.png` with Read and fix what you see.
6. **Voice it:** `$EV_PY $S/tools/narr.py $W` (cached by sentence, so it's
   cheap to redo after an edit), `python3 $S/tools/build.py $W`, then
   `node $S/tools/check.mjs $W/out/<slug>.html` (no `--fast`). It must end
   with **0 failure(s)**. Treat each warning as a defect to fix unless you
   can say why it's fine (a moving packet briefly crossing a label is fine).
7. **Video, if asked or clearly useful** (a phone, sharing):
   `node $S/tools/render.mjs $W/out/<slug>.html` then `$EV_PY $S/tools/mux.py $W`
   → `$W/out/<slug>.mp4` and `$W/out/vid/NN-<scene>.mp4`. About 20 frames a
   second per worker, 4 workers: a 10-minute video takes about 5 minutes.
   Pull a few frames (`$FF -ss <sec> -i <mp4> -frames:v 1 x.png`) and look.
8. **Deliver:** SendUserFile the HTML (`display: render`) and any MP4s. Say
   in one line how to use it ("Play with sound", ← → between scenes,
   space pauses). Only copy it into the repo if the user wants it kept
   there.

If you change the engine or the tools, run `bash $S/tools/selftest.sh`.
It builds and voices the example and checks it, proves the checker catches
a scene that stops moving, and renders a video with its voice.

## How a scene is written

```js
scene({
  title: 'A price, from exchange to strategy',
  dur: 15,                                    // seconds of animation as you draw it
  steps: [                                    // [scene time the motion starts, what is said, options]
    [0,   'Follow one price on its way to a strategy.', { focus: 'all' }],
    [2.0, 'It arrives over a WebSocket, and the feed handler cleans it.', { spot: ['exchange', 'feed'] }],
    [4.2, 'The tick cache keeps one slot per instrument.', { focus: ['cache'] }],
  ],
}, p => {
  const L = G(p), B = G(p), M = G(p);        // lines under boxes under moving things
  const ex = named('exchange', box(B, 40, 300, 200, 110, 'Exchange', { cls: 'out' }));
  named('feed', box(B, 290, 300, 200, 110, 'Feed handler'));
  named('cache', box(B, 790, 230, 300, 330, 'Tick cache', { top: true, cls: 'frame' }));
  const pth = line(L, 'M240 355H286', 'b');
  const tick = packet(M, '25,186.10', 'b');
  const flow = stream(M, pth, 'b', 3, 0.9);
  return (t, rt) => {                         // called every frame
    pop(ex, t, 0.2);
    reveal(pth, t, 1.2, 1.5);
    travel(tick, pth, t, 2.0, 2.7);
    flow(rt, 2.5);                            // rt: keeps flowing while the voice talks
  };
});
```

- **Stage:** 1600×900. The title sits at the top (y < 100); keep content
  in y 110–860 and x 40–1560. Labels are 18–22 px, subs 13 px mono; big
  numbers use `big`/`huge`. Colours are `ink b g pu wt r` (and `y` for
  fills); lines take an arrowhead in their colour.
- **`t` is the story; `rt` is life.** Everything that tells the story runs
  on `t`. Anything that should keep moving while the narrator speaks
  (streams, bobbing, pulses, snores, sweat) runs on `rt`.
- **Pacing, the "never paused" rule.** The clock stretches each step to
  the length of its voice. The animation runs at up to 0.6× the speed you
  drew it, and never slower than 0.35×; if the voice outlasts that, the
  animation finishes early and holds. `check.mjs` prints, per step,
  animation vs voice and any hold (warns over 1.5 s), and **fails a scene
  where nothing moves for 2 s** (warns over 1.2 s). To fix one: draw more
  motion for that sentence (something that means something: a gear turning
  while a server works, a stream of prices, a bubble), split the step, or
  say less. Aim for animation ≈ 0.5–1× the voice.
- **Still means still.** Never move the whole picture to look busy (no
  creeping zoom, no idle sway of everything): a fraction of a pixel a
  frame on every edge and letter reads as shaking. The camera moves only
  to frame something new and then holds; `check.mjs` fails a camera that
  keeps moving. Motion is for particular things, and only while it means
  something (a waiting message bobs, a busy server sweats).
- **One event, its own sentence.** If something must land on a particular
  phrase (a stamp, a crash, a fill), give that phrase its own step whose
  `at` is the event. Inside a step, motion is spread over the whole voice,
  not timed to its second sentence.
- **Point at things, by name.** In the build, name what you'll point at:
  `const srv = named('server', box(…))`. Then `focus: ['server']` (or
  `['server', 'busy']`) eases the camera to frame them (one smooth move
  over about a second, then still) and keeps it until another step changes
  it (`'all'` to pull back); `spot: ['phone', 'server']` dims everything
  else while that step is spoken. The frame is measured from the named
  things themselves, so it always fits them. Raw `[x, y, w, h]` still
  works, but a hand-typed rectangle is how a spotlight ends up cutting a
  box in half. Light everything the sentence is about, including where
  anything moves while it's said: `check.mjs` warns when the spotlight's
  edge cuts a box or a moving packet. Use these to say "look here" when
  the stage is busy, not on every step.
- **Words.** Plain, short sentences, from the listener's side ("the order
  goes to the broker", not "the OMS dispatches"). The caption is exactly
  what's said; pronunciations go in `meta.say` or a step's `{ say: '…' }`.
  Say what's measured and what's estimated.

## Drawing helpers (engine/engine.js)

`named(name, el)` (returns el) something `focus`/`spot` can point at · `G(p, cls)` group · `E(tag, attrs, p)` any element · `T(p, x, y, text, cls, anchor)`
text (classes: `ttl bl bs cl mono lab hop step lane lanes big huge warnl okl gs sl sv`)
· `box(p, x, y, w, h, label, { sub, cls, top, lcls, tag })` with `.X .Y .W .H .cx .cy .R .B`
(cls: `frame key band chip out store file proc art bad btn`) · `line(p, d, color, { dash, arrow })`
· `packet(p, label, color, { w })` (look in `._in`) · `dot` · `check(p, x, y, ok)` · `boom` ·
`gear`/`spin` · `diamond` (a snapshot) ·
timing: `fade(el, t, a, d, out)` `reveal(path, t, a, b)` `travel(el, path, t, a, b, keep)`
`chain(el, paths, t, t0, seg)` `stream(p, path, color, n, period)` → `(time, from, to)`
`flash(el, t, a, d)` (yellow highlight) `txt(el, s)` `quake(t, a, d, amp)` (shake the picture)
· `k(t, a, b)` 0→1 · `ke` eased · `lerp` · `fmt` (Indian digit grouping).

## Life (engine/life.js), and when it fits

| Effect | Call | Use it for |
|---|---|---|
| Pop in with overshoot | `pop(el, t, a, d, out)` | things arriving (instead of a plain fade) |
| Land and squash | `squash(pk._in, t, a)` | a packet reaching its target |
| Rubber stamp + shake | `s = stamp(p, x, y, 'Refused', 'r'); s.go(t, a)` | refused, duplicate, approved (`'g'`) |
| Confetti | `c = burst(p, x, y, { seed }); c.go(t, a)` | a success, a fill, a milestone |
| Smoke puff | `f = puff(p, x, y); f.go(t, a)` + `quake` | a crash, something stopping |
| Speech / thought bubble | `b = bubble(p, x, y, 'busy…', { think, below }); b.go(t, a, out, rt)` | what a box is "thinking": busy, hello, is this new? |
| Snoring | `z = zzz(p, x, y); z.go(rt, on)` | idle, asleep, waiting |
| Sweat drops / wobble | `sweat(p, x, y).go(rt, on)`, `wobble(el, rt, deg)` | overloaded, struggling |
| Sparkles | `s = sparkle(p, x, y, { r }); s.go(t, a)` | checks passing, "measured" |
| Ghost | `g = ghost(p, x, y); fade(...); g.go(rt)` | a zombie process, something that should be gone |
| Rocket | `r = rocket(p); r.go(path, t, a, b, rt)` | a deploy, a launch |
| Bob | `bob(el, rt, amp)` | something waiting its turn |
| Counter / typing | `countUp(el, t, a, b, from, to, fmt)`, `typeOn(el, t, a, b, s, rt)` | numbers that matter, a command being typed |

Taste: at most one or two per scene, each tied to what's being said. A
stamp on every refusal is fun once and noise the third time. Effects are
seeded and driven by `t`/`rt`, never the wall clock, so videos render the
same every time. Don't use CSS animations: they run on the wall clock and
break frame-exact recording.

## Files

- `engine/engine.js` the helpers, the clock (steps, slowing, holds), the
  camera, spotlight, captions, start screen (sound needs one click),
  keyboard, and `__plan`/`__seek` for recording. `engine/life.js` the
  effects. `engine/base.css`, `tokens.css` (light/dark colours), `fonts.css`
  (Funnel Display/Sans, Martian Mono, inlined).
- `tools/setup.sh` venv + voice model (Kokoro `af_heart`, from GitHub
  releases; Hugging Face is blocked here) + Playwright, in `~/.cache/explainer-video`.
- `tools/steps.mjs` reads the narration out of `scenes.js`. `tools/narr.py`
  speaks it. `tools/build.py` makes the page. `tools/check.mjs` checks it
  and draws contact sheets. `tools/render.mjs` + `tools/mux.py` make the
  videos. `tools/selftest.sh` tests all of it.
- `example/` a two-scene project using the camera, the spotlight and the
  effects: the template to copy.

The first explainer made this way, before this engine existed, is
`live_deploy/docs/linck-visual.html`: 16 scenes on the platform (its
script is in `live_deploy/linck-context/reference/text/linck-visual.txt`).
It's a good reference for layouts of systems, timelines and order books.
It uses the older clock, which holds still while the voice catches up.
