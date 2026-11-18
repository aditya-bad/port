// A PowerPoint deck from an explainer: a slide per step (or per scene), each
// playing its piece of the animation, with its last frame as the slide's
// picture (what a PDF, a thumbnail or a viewer without video shows).
//   node deck.mjs <spec.json>
// spec: { out, title, author?, titleCard?: png, theme: 'light'|'dark',
//         sound: bool, start: 'auto'|'click', advance: bool, notes: bool,
//         slides: [{ video, poster, dur, caption, notes }] }
//   sound    the clips carry the voice (otherwise they're silent and muted)
//   start    auto: the animation plays when the slide opens; click: on a click
//   advance  go to the next slide by itself when the animation ends
//   caption  editable text over the bottom of the slide ('' for none)
import fs from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
const EV = process.env.EV_HOME || path.join(os.homedir(), '.cache/explainer-video');
const NM = process.env.NODE_MODULES || path.join(EV, 'node/node_modules');
const require = createRequire(path.join(NM, 'x.js'));     // the CommonJS builds: the ES one thinks it's in a browser
const PptxGenJS = require('pptxgenjs'), JSZip = require('jszip');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const W = 13.333, H = 7.5;            // 16:9, the stage's shape
const dark = spec.theme === 'dark';
const PAPER = dark ? '2A2825' : 'F6F4EF', INK = dark ? 'ECEAE4' : '15181B';
const b64 = f => `image/png;base64,${fs.readFileSync(f).toString('base64')}`;

const pres = new PptxGenJS();
pres.layout = 'LAYOUT_WIDE';
pres.title = spec.title;
if (spec.author) pres.author = spec.author;
pres.theme = { headFontFace: 'Calibri', bodyFontFace: 'Calibri' };

const meta = [];        // per slide: { video: bool, dur }
if (spec.titleCard) {
  const s = pres.addSlide();
  s.background = { color: PAPER };
  s.addImage({ path: spec.titleCard, x: 0, y: 0, w: W, h: H, altText: spec.title, objectName: 'Title card' });
  if (spec.notes) s.addNotes(spec.title);
  meta.push({ video: false, dur: 4 });
}
for (const [i, st] of spec.slides.entries()) {
  const s = pres.addSlide();
  s.background = { color: PAPER };
  s.addImage({ path: st.poster, x: 0, y: 0, w: W, h: H, altText: st.caption || `Step ${i + 1}`, objectName: `Step ${i + 1} picture` });
  s.addMedia({ type: 'video', path: st.video, x: 0, y: 0, w: W, h: H, cover: b64(st.poster), objectName: `Step ${i + 1} animation` });
  if (st.caption) {
    s.addText(st.caption, { x: 0.9, y: H - 1.0, w: W - 1.8, h: 0.72, isTextBox: true, objectName: `Step ${i + 1} caption`,
      fontFace: 'Calibri', fontSize: 18, color: INK, align: 'center', valign: 'middle', fit: 'shrink', margin: 6,
      fill: { color: PAPER, transparency: 12 } });
  }
  if (spec.notes && st.notes) s.addNotes(st.notes);
  meta.push({ video: true, dur: st.dur });
}
const buf = await pres.write({ outputType: 'nodebuffer' });

// pptxgenjs writes videos as click-to-play and slides that wait for a click.
// For "start: auto" add PowerPoint's "Start: Automatically" (a timing tree that
// plays the video as the slide begins); for "advance" a transition that moves
// on when the animation has played. Both go after clrMapOvr, before extLst.
const playTiming = (id, mute) => `<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>`
  + `<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>`
  + `<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/><p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>`
  + `<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>`
  + `<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="afterEffect"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>`
  + `<p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr><p:cTn id="6" dur="1" fill="hold"/><p:tgtEl><p:spTgt spid="${id}"/></p:tgtEl></p:cBhvr></p:cmd>`
  + `</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn>`
  + `<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>`
  + `<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>`
  + `<p:video><p:cMediaNode vol="80000"${mute ? ' mute="1"' : ''}><p:cTn id="7" fill="hold" display="0"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst></p:cTn>`
  + `<p:tgtEl><p:spTgt spid="${id}"/></p:tgtEl></p:cMediaNode></p:video>`
  + `</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>`;

const zip = await JSZip.loadAsync(buf);
const num = n => +n.match(/slide(\d+)\.xml$/)[1];
const names = Object.keys(zip.files).filter(n => /^ppt\/slides\/slide\d+\.xml$/.test(n)).sort((a, b) => num(a) - num(b));
let auto = 0, adv = 0;
for (const n of names) {
  const m = meta[num(n) - 1];
  let xml = await zip.file(n).async('string');
  let add = '';
  // after the clip has played (4 s on the title slide); a click still moves on
  // sooner. A clip that waits for a click doesn't move on by itself.
  if (spec.advance && m && !(m.video && spec.start === 'click')) {
    add += `<p:transition spd="med" advTm="${Math.round((m.dur + 0.6) * 1000)}"><p:fade/></p:transition>`;
    adv++;
  }
  const pic = xml.match(/<p:pic>\s*<p:nvPicPr>\s*<p:cNvPr id="(\d+)"[^>]*>[\s\S]*?<a:videoFile/);
  if (pic && spec.start !== 'click' && !xml.includes('<p:timing>')) { add += playTiming(pic[1], !spec.sound); auto++; }
  if (!add) continue;
  xml = xml.includes('<p:extLst>') && xml.lastIndexOf('<p:extLst>') > xml.lastIndexOf('</p:cSld>')
    ? xml.replace(/<p:extLst>(?![\s\S]*<p:extLst>)/, `${add}<p:extLst>`)
    : xml.replace('</p:sld>', `${add}</p:sld>`);
  zip.file(n, xml);
}
// files only, no folder entries: some cloud viewers refuse a package that has them
const flat = new JSZip();
for (const [n, e] of Object.entries(zip.files)) if (!e.dir) flat.file(n, await e.async('nodebuffer'), { createFolders: false });
fs.writeFileSync(spec.out, await flat.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
console.log(`${spec.out}: ${meta.length} slides, ${auto} playing by themselves, ${adv} moving on by themselves`);
