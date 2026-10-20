# Linck brand

The mark is a terminal prompt that has printed a candle: a strategy is code
that runs on its own, and what it prints is the market. Ink tile, paper
prompt and candle, the yellow cursor.

| File | Use |
| --- | --- |
| `linck-mark.svg` | **The mark.** Source for everything below. |
| `linck-mark-paper.svg` | The same mark on paper. Used in the page on the Night theme only; never an app icon. |
| `render.mjs` | Renders every icon from `linck-mark.svg`. |

## Where the mark appears

| Place | File |
| --- | --- |
| Header, sign-in, Geek mode bar | `static/icons/linck-mark.svg`; on Night, `static/icons/linck-mark-paper.svg` (both copied from here by `render.mjs`; each page renders both and CSS shows one) |
| Browser tab | `linck-mark.svg`, `linck-32.png` |
| Installed app (Chrome "Install app", Android home screen) | `linck-192.png`, `linck-512.png`, `linck-maskable-512.png` via `static/manifest.json` |
| iOS home screen | `linck-180.png` |
| Notifications | `linck-192.png` (icon), `linck-badge-96.png` (status-bar badge, white on transparent) |
| Android app launcher | `android/app/src/main/res/mipmap-*/ic_launcher*.png` |
| Loading | The boot screen in `static/linck/index.html` (inline copy of the mark, recoloured to the paper mark on Night in `shell.css`: the cursor blinks, the candle prints) and the `.spinner` / `.spin` loaders (prompt + blinking cursor) |

The classic view keeps its own old icons.

## Changing the mark

1. Edit `linck-mark.svg`. Keep the `id="tile"` rect and the `id="glyph"` group;
   the renderer and the boot screen rely on them.
2. `cd tests/e2e && npm ci` once, then `node brand/render.mjs` from the repo root.
3. Paste the new SVG into the `#boot` block in `static/linck/index.html`
   (`tests/api/test_brand.py` fails until it matches).
4. Make the same shape change in `linck-mark-paper.svg` (`test_brand.py` checks the two match).
