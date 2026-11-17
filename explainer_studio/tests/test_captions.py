"""Subtitles and chapters, worked by hand."""
from app import captions

NARR = [
    [{"dur": 3.0, "caps": [{"t": 0, "text": "One."}, {"t": 1.5, "text": "Two."}]},
     {"dur": 2.0, "caps": [{"t": 0, "text": "Three."}]}],
    [{"dur": 4.0, "caps": [{"t": 0, "text": "Four, a longer sentence that needs to be wrapped onto two lines."}]}],
]
PLANS = [{"title": "Start", "total": 12.0, "steps": [{"T0": 0.5}, {"T0": 6.0}]},
         {"title": "Next", "total": 15.0, "steps": [{"T0": 1.0}]}]


def test_cues_follow_each_step_and_scene():
    c = captions.cues(NARR, PLANS)
    assert c == [(0.5, 2.0, "One."), (2.0, 3.5, "Two."), (6.0, 8.0, "Three."),
                 (13.0, 17.0, "Four, a longer sentence that needs to be wrapped onto two lines.")]


def test_a_title_card_shifts_everything():
    c = captions.cues(NARR, PLANS, offset=3.0)
    assert c[0] == (3.5, 5.0, "One.") and c[-1][0] == 16.0


def test_srt_and_vtt():
    c = captions.cues(NARR, PLANS)
    s = captions.srt(c)
    assert s.startswith("1\n00:00:00,500 --> 00:00:02,000\nOne.\n\n2\n")
    assert "00:00:13,000 --> 00:00:17,000\nFour, a longer sentence that needs\nto be wrapped onto two lines." in s
    v = captions.vtt(c)
    assert v.startswith("WEBVTT\n\n00:00:00.500 --> 00:00:02.000\nOne.")


def test_cues_never_overlap():
    narr = [[{"dur": 5.0, "caps": [{"t": 0, "text": "A"}]}, {"dur": 1.0, "caps": [{"t": 0, "text": "B"}]}]]
    plans = [{"title": "x", "total": 10, "steps": [{"T0": 0}, {"T0": 3}]}]
    a, b = captions.cues(narr, plans)
    assert a[1] == b[0] == 3


def test_chapters_and_what_youtube_needs():
    text, notes = captions.chapters(PLANS)
    assert text == "0:00 Start\n0:12 Next\n"
    assert any("three or more" in n for n in notes)
    text, _ = captions.chapters(PLANS, intro=3.0)
    assert text.startswith("0:00 Start\n0:15 Next"), "a 3-second intro is folded into the first chapter"
    long = [{"title": f"S{i}", "total": 40} for i in range(4)]
    text, notes = captions.chapters(long, intro=12)
    assert text.splitlines()[:2] == ["0:00 Intro", "0:12 S0"] and notes == []
