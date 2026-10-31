"""
How a caption is said aloud. The captions are written for reading
(₹4,388, 11:42, India VIX 13.2); the voice (Kokoro, via espeak's
phonemes) reads some of that badly: '₹' not at all, '13.2.' at a
sentence's end as 'thirteen. two', 'VIX' as 'V I X', a lakh grouping
'1,23,456' as digits. spoken() turns a caption into what the voice
should read, keeping its words.
"""

from __future__ import annotations

import re

NAMES = [("BANKNIFTY", "Bank Nifty"), ("FINNIFTY", "Fin Nifty"), ("MIDCPNIFTY", "Midcap Nifty"),
         ("NIFTY", "Nifty"), ("SENSEX", "Sensex"), ("BANKEX", "Bankex"), ("VIX", "Vix"), ("P&L", "P and L"),
         ("ATM", "at the money"), ("≥", "at least"), ("≤", "at most"), ("→", "to"), ("·", ","), ("×", "times")]


def rupees_said(n: float) -> str:
    """4388 -> '4,388 rupees'; 123456 -> '1 lakh 23 thousand 456 rupees';
    119.5 -> '119 rupees 50 paise'."""
    n = abs(float(n))
    whole, paise = int(n), int(round((n - int(n)) * 100))
    if paise == 100:
        whole, paise = whole + 1, 0
    if whole >= 10_000_000:
        cr, rest = divmod(whole, 10_000_000)
        lk = rest // 100_000
        s = f"{cr} crore" + (f" {lk} lakh" if lk else "")
    elif whole >= 100_000:
        lk, rest = divmod(whole, 100_000)
        th, r = divmod(rest, 1000)
        s = f"{lk} lakh" + (f" {th} thousand" if th else "") + (f" {r}" if r else "")
    else:
        s = f"{whole:,}"
    s += " rupee" if whole == 1 and not paise else " rupees"
    if paise and whole < 100_000:
        s += f" {paise} paise"
    return s


_RS = re.compile(r"₹\s?(\d+(?:,\d+)*(?:\.\d+)?)")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_DEC = re.compile(r"(\d)\.(\d)")


def _time(m: re.Match) -> str:
    h, mm = int(m.group(1)), m.group(2)
    return f"{h} o'clock" if mm == "00" else f"{h} {mm}" if mm[0] != "0" else f"{h} oh {mm[1]}"


def spoken(text: str) -> str:
    s = _RS.sub(lambda m: rupees_said(float(m.group(1).replace(",", ""))), text)
    s = _TIME.sub(_time, s)
    s = _DEC.sub(r"\1 point \2", s)
    for a, b in NAMES:
        s = s.replace(a, b)
    s = re.sub(r"(\d+)%", r"\1 percent", s)
    s = s.replace("—", ", ").replace("–", ", ")
    return " ".join(s.split())
