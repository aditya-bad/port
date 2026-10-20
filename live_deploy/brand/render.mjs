// Renders every Linck icon from brand/linck-mark.svg.
//   node brand/render.mjs     (needs tests/e2e's node_modules: cd tests/e2e && npm ci)
// Writes static/icons/ (web app, install, notifications) and the Android
// launcher icons. Commit the results; nothing renders them at runtime.
import { chromium } from '../tests/e2e/node_modules/playwright/index.mjs';
import { readFileSync, writeFileSync, copyFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const mark = readFileSync(`${root}brand/linck-mark.svg`, 'utf8').trim();
const inner = mark.replace(/^<svg[^>]*>/, '').replace(/<\/svg>$/, '');
const glyph = inner.match(/<g id="glyph">[\s\S]*<\/g>/)[0];
const INK = '#1d1b16';

// shape: 'square' (full bleed), 'rounded', 'circle'; scale shrinks the
// glyph about the centre (maskable and round icons need a safe zone).
const icon = ({ shape = 'rounded', scale = 1, badge = false }) => {
  const clip = shape === 'circle' ? '<circle cx="50" cy="50" r="50"/>'
    : shape === 'rounded' ? '<rect width="100" height="100" rx="20"/>' : '<rect width="100" height="100"/>';
  const g = badge ? glyph.replace(/#f6f4ee|#efd36b/g, '#fff').replace(/ stroke-opacity="[^"]*"/, '') : glyph;
  const t = `translate(${50 - 50 * scale} ${50 - 50 * scale}) scale(${scale})`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><clipPath id="c">${clip}</clipPath></defs>
    <g clip-path="url(#c)">${badge ? '' : `<rect width="100" height="100" fill="${INK}"/>`}<g transform="${t}">${g}</g></g></svg>`;
};

const out = [
  ['static/icons/linck-32.png', 32, {}],
  ['static/icons/linck-180.png', 180, { shape: 'square' }],
  ['static/icons/linck-192.png', 192, {}],
  ['static/icons/linck-512.png', 512, {}],
  ['static/icons/linck-maskable-512.png', 512, { shape: 'square', scale: 0.8 }],
  ['static/icons/linck-badge-96.png', 96, { shape: 'square', badge: true }],
  ...[['mdpi', 48], ['hdpi', 72], ['xhdpi', 96], ['xxhdpi', 144], ['xxxhdpi', 192]].flatMap(([d, px]) => [
    [`android/app/src/main/res/mipmap-${d}/ic_launcher.png`, px, {}],
    [`android/app/src/main/res/mipmap-${d}/ic_launcher_round.png`, px, { shape: 'circle', scale: 0.86 }],
  ]),
];

copyFileSync(`${root}brand/linck-mark.svg`, `${root}static/icons/linck-mark.svg`);
const browser = await chromium.launch();
for (const [path, px, opt] of out) {
  const page = await browser.newPage({ viewport: { width: px, height: px } });
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:${px}px;height:${px}px}</style>${icon(opt)}`);
  writeFileSync(`${root}${path}`, await page.screenshot({ omitBackground: true }));
  await page.close();
  console.log(px.toString().padStart(4), path);
}
await browser.close();
