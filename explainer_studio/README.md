# Explainer Studio

Turn your material into a narrated, animated explainer: scenes drawn as
moving diagrams, a natural voice reading the narration, and captions. You
get an HTML player, MP4s, a YouTube-ready file with subtitles, chapters and
a thumbnail, and, made separately, a PowerPoint whose slides play the
animation.

Everything runs on your machine in one Docker container. The voice
(Kokoro) runs offline. Claude is optional: it can write the script from
your material, but you can also write or upload the script yourself.

## Start it

```bash
cd explainer_studio
docker compose up -d --build        # the first build downloads a browser and the voice model: a 2–3 GB image
```

Open **http://localhost:47391**. Projects are kept in `./data`.

Port 47391 is unusual on purpose, so it won't clash with your other services.
To change it: `STUDIO_PORT=48000 docker compose up -d`.

There's no sign-in, so it listens only on this machine by default. To use it from
somewhere else:

- **SSH tunnel:** `ssh -L 47391:localhost:47391 you@server`, then open
  http://localhost:47391.
- **Tailscale:** `tailscale serve --bg 47391` on the server.
- **Open to your network:** `STUDIO_BIND=0.0.0.0 docker compose up -d`.
  Anyone who can reach the port can use it, so only do this on a network you trust.

## Settings (environment variables, for docker compose)

| Variable | Default | What it does |
|---|---|---|
| `STUDIO_PORT` | `47391` | The port on your machine |
| `STUDIO_BIND` | `127.0.0.1` | Who can reach it (`0.0.0.0`: everyone on the network) |
| `STUDIO_CPUS` | `2` | CPU cores the container may use. Rendering takes all it's given |
| `STUDIO_MEMORY` | `4g` | Memory limit |
| `STUDIO_WORKERS` | `2` | Scenes recorded at the same time |
| `ANTHROPIC_API_KEY` | none | For "Write it with Claude". It can also be set in the app's Settings |

## Using it

1. **New explainer:** give it a title and a brief: who it's for and what they
   should come away knowing.
2. **Material:** upload documents, code, notes, CSV or JSON data, PDFs or
   images. Or upload a finished `scenes.js` (with its `meta.json`, or a .zip
   of both) if you wrote one yourself.
3. **Script:**
   - **Write it with Claude.** Claude reads the brief and the material and writes the
     scenes. The studio then checks them and Claude fixes what the check finds, for a
     few rounds. To revise, describe the change ("slow down scene 2, add a step on
     retries"). The cost of each run is shown when it finishes.
   - **Or write it yourself**, or start from the bundled example. **Build preview**
     plays it without the voice, which is quick for checking pictures and timing.
4. **Voice & check:** speaks the narration and plays every scene through. It looks
   for scenes that stall, labels that collide, and steps with no caption. It shows
   a frame from the middle of every step; look at them before rendering.
5. **Video:** pick the voice and speed, pronunciations (`P&L` → `P and L`),
   voice in the video or silent, size (720p, 1080p, 4K, or vertical 9:16 for
   Shorts), frame rate, light or dark, captions in the picture or not, title
   cards, and the files you want:

   | File | What it is |
   |---|---|
   | HTML player | One file that plays offline in any browser, with voice and captions |
   | MP4 | The whole explainer with its voice |
   | One MP4 per scene | Each scene on its own |
   | YouTube-ready MP4 | H.264 High + AAC 48 kHz stereo, your size and frame rate, optional title cards, fast start |
   | Subtitles (.srt / .vtt) | From the exact narration. The YouTube file gets its own `-youtube.srt`, shifted by the title card |
   | Chapters | `0:00 Scene title` lines to paste into the description. The page says if YouTube won't show them (it needs three or more, each 10 s or longer) |
   | Thumbnail | 1280×720 JPEG from the first scene |

6. **PowerPoint:** a .pptx made on its own, with its own settings. Making it
   keeps the videos already made, and making a video keeps the PowerPoint.

   | Setting | Choices |
   |---|---|
   | A slide for each | **Step** (one sentence and its piece of the animation) or **scene** (the whole scene on one slide) |
   | Captions | **As text** (an editable text box at the bottom), **in the picture**, or **none**. With a slide per scene, "as text" goes in the picture, since the sentences change while it plays |
   | Sound | The voice plays with each slide's animation, or silent (to talk over) |
   | Animation starts | **When the slide opens**, or **on a click** |
   | Move on by itself | The next slide follows when the animation ends; a click still moves on sooner. Only when the animation starts by itself |
   | Speaker notes | What the narrator says, in each slide's notes |
   | Title slide | The title and description first |
   | Size, frame rate, look | 720p or 1080p, 25 or 30 fps, light or dark |

   The animations are video clips, exactly as in the MP4, not PowerPoint's own
   animation effects, so they can't be edited inside PowerPoint. Each slide's
   picture under the clip is its last frame, which is what a PDF export shows.
   It plays in PowerPoint 2016 or later (Windows and Mac) and Keynote. Google
   Slides drops the videos and shows only the pictures.
7. **Files:** watch the result, copy the chapters, and download the files one by one
   or as a zip.

**For YouTube:** upload the `-youtube.mp4`, add `-youtube.srt` under
Subtitles, paste the chapters into the description, and set the thumbnail.

### How long a render takes

The studio records every frame in a real browser, then encodes. Measured on a
4-core machine, the 1-minute example took about a minute at 720p/25, voice
included. Expect more at 1080p. 4K takes about four times as long as 1080p, and
60 fps about twice as long as 30. Only one job runs at a time; the
others wait their turn.

## Writing a script by hand

A script is a `scenes.js` file. Each `scene({...})` has a title, a length, and
its **steps**: `[seconds, 'what is said', { focus, spot }]`. It also has a
function that draws on a 1600×900 stage and moves things over time.
`kit/WRITING.md` is the full guide: layout, every drawing helper, the effects,
and pacing rules. `kit/example/scenes.js` is a complete two-scene example.

## Without Docker (development)

```bash
bash scripts/setup-local.sh     # Python env, voice model, Playwright Chromium (needs python3, node, ffmpeg)
bash scripts/run-local.sh       # http://127.0.0.1:47391
~/.cache/explainer-video/venv/bin/python -m pytest tests   # the full suite, including a real render (about 2 min)
```

## What's inside

```
app/        the web app (FastAPI): projects, uploads, the job queue, the pipeline, Claude drafting
ui/         the page (plain HTML, CSS and JS, no build step)
kit/        the explainer engine and its tools:
  engine/   the player: drawing helpers, effects, the clock, camera, captions (JS/CSS)
  tools/    steps.mjs (read a script), narr.py (the voice), build.py (one HTML file),
            check.mjs (play it through, find problems), render.mjs (record frames),
            mux.py (voice under picture), plans.mjs and card.mjs (timings and title cards),
            deck.mjs (the PowerPoint)
  WRITING.md  how to write a script; also what Claude is given
samples/    cafe-order: a ready project to try the studio with (see its README)
tests/      API, subtitles and chapters, Claude drafting (with a stand-in API), a full render and a PowerPoint
```

## Good to know

- **Single user.** There's no sign-in. Keep it on localhost, behind an SSH
  tunnel, or on Tailscale.
- **A script is code.** `scenes.js` runs in the container's headless browser
  while building and recording. Only load scripts you trust.
- **Claude:** the default model is Claude Opus 5.5; it can be changed in
  Settings. The key stays on the server (`data/settings.json`, readable only by
  its owner) and is never sent to the page. Requests use refusal fallbacks
  (`fallbacks: "default"`), so a declined request is retried on another model
  automatically.
- **Voices** are English (US and UK), from Kokoro v1.0 (Apache-2.0). The
  typefaces are Funnel Display, Funnel Sans and Martian Mono (SIL Open Font License).
