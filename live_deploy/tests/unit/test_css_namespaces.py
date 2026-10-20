"""Every stylesheet loads on every page, so a class Geek mode styles must
not also be styled by another page's stylesheet: the strategy page's
picker chips once shared `.st-chip` with Geek's rule-state chips, and
whichever rule came last restyled the other."""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "static/linck/css"


def _leading_classes(path: Path) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    out = set()
    for sel in re.findall(r"([^{}]+)\{", css):
        for part in sel.split(","):
            m = re.match(r"\s*\.([a-zA-Z][\w-]*)", part)
            if m:
                out.add(m.group(1))
    return out


def test_geek_classes_are_its_own():
    geek = _leading_classes(CSS / "geek.css")
    assert geek, "geek.css has rules"
    for other in CSS.glob("*.css"):
        if other.name == "geek.css":
            continue
        shared = geek & _leading_classes(other)
        assert not shared, f"{other.name} also styles {sorted(shared)}"
