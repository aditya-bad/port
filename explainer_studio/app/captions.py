"""Subtitles and chapters from what was spoken and when (pure functions).

`narr` is the kit's narr/narr.json: per scene, per step, {dur, caps: [{t,
text}]}, a caption's t counted from its step's voice starting. `plans` is
each scene's plan from the built page: {title, total, steps: [{T0, ...}]},
a step's voice starting at T0 into its scene. Scenes play one after the
other; `offset` shifts everything (a title card before them).
"""

from __future__ import annotations


def cues(narr: list, plans: list, offset: float = 0.0) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    at = offset
    for scene, plan in zip(narr, plans):
        for step, sp in zip(scene, plan["steps"]):
            caps = step.get("caps") or []
            t0 = at + float(sp["T0"])
            for k, c in enumerate(caps):
                start = t0 + float(c["t"])
                end = t0 + float(caps[k + 1]["t"]) if k + 1 < len(caps) else t0 + float(step["dur"])
                out.append((round(start, 3), round(max(end, start + 0.4), 3), c["text"]))
        at += float(plan["total"])
    # never overlapping: a cue ends where the next begins
    for i in range(len(out) - 1):
        s, e, txt = out[i]
        if e > out[i + 1][0]:
            out[i] = (s, out[i + 1][0], txt)
    return out


def _ts(t: float, sep: str) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def wrap(text: str, width: int = 42) -> str:
    """At most two lines, split near the middle on a space."""
    if len(text) <= width:
        return text
    mid = len(text) // 2
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1:]


def srt(c: list) -> str:
    return "".join(f"{i}\n{_ts(s, ',')} --> {_ts(e, ',')}\n{wrap(t)}\n\n" for i, (s, e, t) in enumerate(c, 1))


def vtt(c: list) -> str:
    return "WEBVTT\n\n" + "".join(f"{_ts(s, '.')} --> {_ts(e, '.')}\n{wrap(t)}\n\n" for s, e, t in c)


def chapters(plans: list, intro: float = 0.0) -> tuple[str, list[str]]:
    """YouTube's chapter list for the description, and what keeps YouTube
    from showing it (it wants the first at 0:00, three or more, each at
    least 10 seconds)."""
    rows, at = [], 0.0
    if intro:
        rows.append((0.0, "Intro"))
        at = intro
    for p in plans:
        rows.append((at, p.get("title") or "Scene"))
        at += float(p["total"])
    if intro and len(rows) > 1 and rows[1][0] - rows[0][0] < 10:
        rows.pop(0)                 # a short intro is folded into the first chapter
        rows[0] = (0.0, rows[0][1])

    def stamp(t: float) -> str:
        t = int(t)
        h, r = divmod(t, 3600)
        return f"{h}:{r // 60:02d}:{r % 60:02d}" if h else f"{r // 60}:{r % 60:02d}"
    text = "".join(f"{stamp(t)} {title}\n" for t, title in rows)
    notes = []
    if len(rows) < 3:
        notes.append("YouTube shows chapters only with three or more")
    ends = [t for t, _ in rows[1:]] + [at]
    short = [title for (t, title), e in zip(rows, ends) if e - t < 10]
    if short:
        notes.append("YouTube wants each chapter 10 seconds or longer: " + ", ".join(short))
    return text, notes
