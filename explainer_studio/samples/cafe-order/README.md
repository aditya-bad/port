# Sample: How a café gets your coffee to you

A ready project for trying Explainer Studio: three scenes, about 1 minute 27
seconds with the voice. It shows a café order queueing, the minute it takes to
make a drink, and the hand-off. It passes the studio's check with no warnings.

| File | What it's for |
|---|---|
| `brief.txt` | What to paste as the brief when you make the project |
| `cafe-notes.md` | The material: upload it on the Material tab (Claude reads it if you draft with Claude) |
| `scenes.js` | The finished script |
| `meta.json` | Its title, description and voice |

## Try it

1. Start the studio and open http://localhost:47391.
2. **New explainer:** title `How a café gets your coffee to you`; paste `brief.txt` as the brief.
3. **Material:** upload `cafe-notes.md`. Then press "Upload a script" and pick `scenes.js`
   and `meta.json` together (or a .zip of the two).
   - To test drafting instead, skip the script upload, add your Claude key in Settings, and press
     "Write it with Claude" on the Script tab. Claude writes its own version from the brief and notes.
4. **Script:** "Build preview" plays it without the voice (seconds).
5. **Voice & check:** speaks it and plays it through. It should end with 0 warnings and 0 failures.
6. **Video:** pick what you want (e.g. MP4 + YouTube + subtitles), voice on or off, and press
   "Make the video". At 720p/25 it takes about a minute and a half on 4 cores; 1080p takes longer.
7. **PowerPoint:** pick the options and press "Make the PowerPoint". With a slide per step and a
   title slide it makes 16 slides.
8. **Files:** download what was made.

## What to look for

- PowerPoint: open it in PowerPoint and start the slide show (F5). Each slide's animation plays
  as the slide opens. With "Move on by itself" the deck runs through on its own, and with "Sound"
  you hear each sentence. The step's sentence is the caption and is in the speaker notes.
- Try "On a click" and "None" for captions, or "Scene" for a slide per scene, and make it again.
- The times in scene 2 are typical estimates, and the narration says so.
