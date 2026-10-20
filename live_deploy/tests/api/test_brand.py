"""The Linck mark everywhere it appears: icons, header, sign-in, boot
screen, loaders. brand/README.md lists the places; brand/render.mjs
renders the PNGs from brand/linck-mark.svg."""
import re
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
MARK = (ROOT / "brand" / "linck-mark.svg").read_text().strip()
GLYPH = re.search(r'<g id="glyph">.*</g>', MARK).group(0)
INK = (0x1D, 0x1B, 0x16)


def png(path):
    """(width, height, pixel(x, y) -> (r, g, b, a)) for 8-bit RGB/RGBA PNGs."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    pos, idat = 8, b""
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype in (2, 6), (path, depth, ctype)
            bpp = 4 if ctype == 6 else 3
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride, rows, prev = zlib.decompress(idat), w * bpp, [], bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1: line[i] = (line[i] + a) & 255
            elif f == 2: line[i] = (line[i] + b) & 255
            elif f == 3: line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line); prev = line
    px = lambda x, y: tuple(rows[y][x * bpp:x * bpp + bpp]) + (() if bpp == 4 else (255,))
    return w, h, px


def near(rgb, want, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(rgb, want))


# ── The mark itself ────────────────────────────────────────────────────

def test_served_mark_is_the_brand_source():
    assert (STATIC / "icons" / "linck-mark.svg").read_text().strip() == MARK


def test_mark_has_the_parts_the_renderer_and_boot_screen_use():
    for part in ['id="tile"', 'id="glyph"', 'class="prompt"', 'class="cursor"', 'class="candle"', "#efd36b"]:
        assert part in MARK, part


def test_paper_backup_is_kept_but_used_nowhere():
    backup = ROOT / "brand" / "linck-mark-paper.svg"
    assert backup.exists() and 'id="glyph"' in backup.read_text()
    assert not (STATIC / "icons" / "linck-mark-paper.svg").exists()
    for base in [STATIC, ROOT / "app", ROOT / "android"]:
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in {".html", ".js", ".css", ".json", ".py", ".xml", ".kt"}:
                assert "mark-paper" not in f.read_text(errors="ignore"), f


def test_mark_is_public(app_client):
    r = app_client.get("/icons/linck-mark.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]


# ── Icons ──────────────────────────────────────────────────────────────

ICONS = [  # path, size, corner: 'clear' (rounded/circle), 'ink' (full bleed)
    ("static/icons/linck-32.png", 32, "clear"),
    ("static/icons/linck-180.png", 180, "ink"),
    ("static/icons/linck-192.png", 192, "clear"),
    ("static/icons/linck-512.png", 512, "clear"),
    ("static/icons/linck-maskable-512.png", 512, "ink"),
] + [(f"android/app/src/main/res/mipmap-{d}/{n}.png", px, "clear")
     for d, px in [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)]
     for n in ("ic_launcher", "ic_launcher_round")]


@pytest.mark.parametrize("path,size,corner", ICONS)
def test_icon_is_the_ink_mark_at_its_size(path, size, corner):
    w, h, px = png(ROOT / path)
    assert (w, h) == (size, size)
    c = px(0, 0)
    if corner == "ink":
        assert c[3] == 255 and near(c[:3], INK), c
    else:
        assert c[3] == 0, c
    # inside the tile: ink, the yellow cursor and the paper candle body all present
    assert near(px(size // 2, int(size * .2))[:3], INK)
    cols = {px(x, y)[:3] for x in range(0, size, max(1, size // 64)) for y in range(0, size, max(1, size // 64))}
    assert any(near(p, (0xEF, 0xD3, 0x6B), 20) for p in cols), "yellow cursor"
    assert any(near(p, (0xF6, 0xF4, 0xEE), 12) for p in cols), "paper prompt / candle"


def test_maskable_icon_keeps_the_glyph_inside_the_safe_zone():
    w, _, px = png(STATIC / "icons" / "linck-maskable-512.png")
    r = w * 0.4
    for x in range(0, w, 4):
        for y in range(0, w, 4):
            if (x - w / 2) ** 2 + (y - w / 2) ** 2 > r * r:
                assert near(px(x, y)[:3], INK, 10), (x, y, px(x, y))


def test_notification_badge_is_a_white_silhouette():
    w, h, px = png(STATIC / "icons" / "linck-badge-96.png")
    assert (w, h) == (96, 96) and px(0, 0)[3] == 0
    solid = [px(x, y) for x in range(w) for y in range(h) if px(x, y)[3] == 255]
    assert solid and all(p[:3] == (255, 255, 255) for p in solid)


def test_manifest_and_pages_point_at_the_new_icons(app_client):
    m = app_client.get("/manifest.json").json()
    assert {i["src"] for i in m["icons"]} == {"/icons/linck-192.png", "/icons/linck-512.png", "/icons/linck-maskable-512.png"}
    for page in ["linck/index.html", "linck/login/index.html"]:
        t = (STATIC / page).read_text()
        assert 'href="/icons/linck-mark.svg"' in t and "/icons/linck-32.png" in t and "/icons/linck-180.png" in t, page


# ── Where the mark is drawn ────────────────────────────────────────────

def test_boot_screen_draws_the_current_mark():
    t = (STATIC / "linck" / "index.html").read_text()
    boot = re.search(r'<div class="boot" id="boot".*?</div>', t, re.S).group(0)
    assert GLYPH in boot, "paste brand/linck-mark.svg into #boot in static/linck/index.html"


def test_header_sign_in_and_geek_bar_show_the_mark():
    assert 'class="mark" src="/icons/linck-mark.svg"' in (STATIC / "linck/js/app.js").read_text()
    assert 'class="mark" src="/icons/linck-mark.svg"' in (STATIC / "linck/login/index.html").read_text()
    assert (STATIC / "linck/js/pages/geek/index.js").read_text().count('src="/icons/linck-mark.svg"') == 3


def test_loaders_are_the_blinking_cursor_not_a_spinning_ring():
    css = "".join((STATIC / "linck" / p).read_text() for p in ["css/components.css", "css/shell.css", "css/pages.css", "login/login.css"])
    for sel in [".spinner", ".spin", ".auth-spin"]:
        rule = re.search(re.escape(sel) + r"[^{]*\{[^}]*\}", css)
        assert rule, sel
    assert "animation: spin" not in css
    assert "animation: blink" in css
