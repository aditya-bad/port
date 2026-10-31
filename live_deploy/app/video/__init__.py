"""
Videos: a replay told as a narrated, animated explainer, made on the
server and kept as an MP4 (the replay page's Make video).

  story.py   the replay in scenes and spoken words (pure)
  speech.py  how a caption is said aloud (₹, times, decimals, names)
  voice.py   the narration, spoken offline (Kokoro), one clip per step
  render.py  the stage page recorded frame by frame, with its voice
  jobs.py    the queue: one video at a time, at the lowest priority,
             only with memory to spare; picked up again after a restart
"""
