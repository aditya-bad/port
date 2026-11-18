// Explainer Studio's page: projects, material, the script, the check, the
// render settings and the finished files. Plain JS, no build step.
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const main = $('#main');
let OPT = null;          // voices, resolutions, outputs, defaults, claude settings
let P = null;            // the open project
let watching = null;     // the job being followed
let dirty = false;       // the script editor has unsaved changes

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.json !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.json); }
  if (opts.form) init.body = opts.form;
  let r;
  try { r = await fetch(path, init); } catch { throw new Error('Can’t reach the studio. Is it running?'); }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    let d = data && data.detail;
    if (Array.isArray(d)) d = d.map(x => x.msg).join('; ');
    throw new Error(d || `Request failed (${r.status})`);
  }
  return data;
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 3200);
}
const fail = e => toast(e.message || String(e));
const size = n => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
const ago = t => { const s = Date.now() / 1000 - t; return s < 90 ? 'just now' : s < 3600 * 1.5 ? `${Math.round(s / 60)} min ago` : s < 86400 * 1.5 ? `${Math.round(s / 3600)} h ago` : new Date(t * 1000).toLocaleDateString(); };
const fileUrl = (rel, dl) => `/api/projects/${P.id}/files/${rel.split('/').map(encodeURIComponent).join('/')}${dl ? '?download=1' : ''}`;

// ── routing ───────────────────────────────────────────────────────────
const TABS = [['material', 'Material'], ['script', 'Script'], ['check', 'Voice & check'], ['render', 'Video'], ['deck', 'PowerPoint'], ['outputs', 'Files']];
function route() {
  const m = location.hash.match(/^#\/p\/([a-z0-9]+)(?:\/(\w+))?/);
  if (dirty && !confirm('The script has unsaved changes. Leave without saving?')) return;
  dirty = false;
  if (m) openProject(m[1], m[2] || 'material');
  else home();
}
window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

// ── home ──────────────────────────────────────────────────────────────
async function home() {
  P = null;
  document.title = 'Explainer Studio';
  const list = await api('/api/projects').catch(e => { fail(e); return []; });
  main.innerHTML = `
    <div class="home-grid">
      <section>
        <div class="section-head"><h1>Your explainers</h1><span class="mute">${list.length || 'none yet'}</span></div>
        <div class="projects">${list.length ? list.map(p => `
          <a class="proj" href="#/p/${p.id}">
            <div class="grow"><b>${esc(p.title)}</b><div class="small mute">changed ${ago(p.updated || p.created)}</div></div>
            ${p.has_script ? '<span class="pill">script</span>' : '<span class="pill">no script yet</span>'}
            ${p.outputs ? `<span class="pill on">${p.outputs} file${p.outputs === 1 ? '' : 's'}</span>` : ''}
          </a>`).join('') : '<div class="empty">Nothing here yet. Start one on the right.</div>'}</div>
      </section>
      <section class="card stack">
        <h2>New explainer</h2>
        <p class="mute small">Give it a title and say what it should explain, and to whom. You'll add your material next.</p>
        <form id="new" class="stack">
          <label class="field"><span>Title</span><input name="title" required maxlength="120" placeholder="How our order flow works"></label>
          <label class="field"><span>Brief</span><textarea name="brief" rows="5" maxlength="4000" placeholder="For a new team member: follow one order from the app to the exchange, and what can go wrong on the way."></textarea></label>
          <button class="btn" type="submit">Create</button>
        </form>
      </section>
    </div>`;
  $('#new').addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const p = await api('/api/projects', { method: 'POST', json: { title: f.get('title'), brief: f.get('brief') } });
      location.hash = `#/p/${p.id}/material`;
    } catch (err) { fail(err); }
  });
}

// ── a project ─────────────────────────────────────────────────────────
async function load(id) { P = await api(`/api/projects/${id}`); return P; }

async function openProject(id, tab) {
  try { await load(id); } catch (e) { main.innerHTML = `<div class="empty">${esc(e.message)}. <a href="#/">Back to your explainers</a></div>`; return; }
  document.title = `${P.title} · Explainer Studio`;
  const counts = { material: P.uploads.length, outputs: P.outputs.length };
  main.innerHTML = `
    <div class="phead">
      <div class="grow stack" style="gap:4px">
        <a href="#/" class="small mute">← Your explainers</a>
        <input class="title-in" id="title" value="${esc(P.title)}" aria-label="Title" maxlength="120">
      </div>
      <button class="btn danger small" id="del" type="button">Delete</button>
    </div>
    <nav class="tabs" aria-label="Steps">${TABS.map(([k, l]) => `<a href="#/p/${P.id}/${k}" class="${k === tab ? 'on' : ''}">${l}${counts[k] ? `<span class="n">${counts[k]}</span>` : ''}</a>`).join('')}</nav>
    <div id="tab"></div>`;
  $('#title').addEventListener('change', async e => {
    try { await api(`/api/projects/${P.id}`, { method: 'PATCH', json: { title: e.target.value } }); toast('Title saved'); } catch (err) { fail(err); }
  });
  $('#del').addEventListener('click', async () => {
    if (!confirm(`Delete “${P.title}” and everything made from it? This can't be undone.`)) return;
    try { await api(`/api/projects/${P.id}`, { method: 'DELETE' }); location.hash = '#/'; } catch (err) { fail(err); }
  });
  ({ material, script, check, render, deck, outputs }[tab] || material)($('#tab'));
  const live = (P.jobs || []).find(j => j.state === 'queued' || j.state === 'running');
  if (live && (!watching || watching.id !== live.id)) watch(live.id);
}
const lastJob = kind => (P.jobs || []).find(j => j.kind === kind && (j.state === 'done' || j.state === 'failed'));

// Material: the brief, uploads, or a script of your own
function material(el) {
  el.innerHTML = `
    <div class="cols">
      <section class="stack">
        <h2>What it should explain</h2>
        <label class="field"><span>Brief</span><textarea id="brief" rows="8" maxlength="4000">${esc(P.brief)}</textarea>
          <small class="mute">Who it's for, what they should come away knowing, what to leave out.</small></label>
        <label class="field"><span>One-line description</span><input id="desc" maxlength="300" value="${esc(P.description || '')}" placeholder="Shown on the player's start screen">
        </label>
      </section>
      <section class="stack">
        <h2>Material</h2>
        <label class="drop" id="drop" tabindex="0">
          <input type="file" id="files" multiple hidden>
          <b>Drop files here, or click to choose</b><br>
          <span class="small">Documents, code, notes, CSV or JSON data, PDFs, images. Claude reads them to write the script.</span>
        </label>
        <div class="files" id="uploads"></div>
        <div class="card stack">
          <h3>Already have a script?</h3>
          <p class="small mute">Upload a <code>scenes.js</code> (and its <code>meta.json</code> if you have one), or a .zip with them. It replaces the current script.</p>
          <label class="btn ghost small" style="justify-self:start">Upload a script<input type="file" id="import" multiple accept=".js,.json,.zip" hidden></label>
        </div>
      </section>
    </div>`;
  const list = () => {
    $('#uploads', el).innerHTML = P.uploads.length ? P.uploads.map(u => `
      <div class="file"><span class="name grow">${esc(u.name)}</span><span class="small mute">${size(u.size)}</span>
        <button class="btn ghost small" data-del="${esc(u.name)}" type="button" aria-label="Remove ${esc(u.name)}">Remove</button></div>`).join('')
      : '<p class="small mute">No material yet. You can also skip this and write the script yourself.</p>';
  };
  list();
  const save = patch => api(`/api/projects/${P.id}`, { method: 'PATCH', json: patch }).then(d => { P = { ...P, ...d }; toast('Saved'); }).catch(fail);
  $('#brief', el).addEventListener('change', e => save({ brief: e.target.value }));
  $('#desc', el).addEventListener('change', e => save({ description: e.target.value }));
  const send = async files => {
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    try { const r = await api(`/api/projects/${P.id}/uploads`, { method: 'POST', form: fd }); P.uploads = r.uploads; list(); toast(`Added ${r.saved.length} file${r.saved.length === 1 ? '' : 's'}`); }
    catch (e) { fail(e); }
  };
  const drop = $('#drop', el);
  $('#files', el).addEventListener('change', e => { send([...e.target.files]); e.target.value = ''; });
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#files', el).click(); } });
  ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => send([...e.dataTransfer.files]));
  el.addEventListener('click', async e => {
    const b = e.target.closest('[data-del]');
    if (!b) return;
    try { P.uploads = (await api(`/api/projects/${P.id}/uploads/${encodeURIComponent(b.dataset.del)}`, { method: 'DELETE' })).uploads; list(); } catch (err) { fail(err); }
  });
  $('#import', el).addEventListener('change', async e => {
    const fd = new FormData();
    for (const f of e.target.files) fd.append('files', f);
    e.target.value = '';
    try { await api(`/api/projects/${P.id}/import`, { method: 'POST', form: fd }); toast('Script imported'); location.hash = `#/p/${P.id}/script`; } catch (err) { fail(err); }
  });
}

// Script: Claude, the editor, a quick preview
async function script(el) {
  const s = await api(`/api/projects/${P.id}/script`);
  const has = !!s.text.trim(), claude = OPT.claude.has_key;
  const prev = lastJob('preview') || lastJob('check');
  const previewRel = prev && prev.state === 'done' && prev.result.preview;
  el.innerHTML = `
    <div class="stack">
      <section class="card claude stack">
        <div class="row"><h2 class="grow">Write it with Claude</h2>${claude ? `<span class="pill">${esc(OPT.claude.model)}</span>` : ''}</div>
        ${claude ? `
          <p class="small mute">${has ? 'Say what to change and Claude revises the script; or redraft it from your material.' : 'Claude reads your brief and material and writes the scenes, then checks them and fixes what it finds.'} You'll see the cost when it's done.</p>
          <textarea id="instr" rows="3" maxlength="4000" placeholder="${has ? 'e.g. Make scene 2 slower and add a step on retries' : 'Anything else Claude should know (optional)'}"></textarea>
          <div class="row">
            ${has ? '<button class="btn" id="revise" type="button">Revise</button><button class="btn ghost" id="redraft" type="button">Redraft from the material</button>'
                  : '<button class="btn" id="redraft" type="button">Write the script</button>'}
          </div>` : `
          <p class="small mute">Add a Claude API key in <a href="#" id="go-settings">Settings</a> to have Claude write and revise scripts. Or write one below, start from the example, or upload your own on the Material tab.</p>`}
      </section>
      <div class="cols">
        <section class="stack">
          <div class="row"><h2 class="grow">scenes.js</h2>
            <span class="small mute" id="saved-state"></span>
            ${has ? '' : '<button class="btn ghost small" id="example" type="button">Start from the example</button>'}
            <button class="btn small" id="save" type="button">Save</button></div>
          <textarea class="editor" id="editor" spellcheck="false" aria-label="scenes.js">${esc(s.text)}</textarea>
          <p class="small mute">Each <code>scene({...})</code> has steps: <code>[seconds, 'what is said', {focus, spot}]</code>, and a drawing. Ctrl+S saves.</p>
        </section>
        <section class="stack">
          <div class="row"><h2 class="grow">Preview</h2><button class="btn ghost small" id="preview" type="button" ${has ? '' : 'disabled'}>Build preview (captions only)</button></div>
          ${previewRel ? `<div class="frame-wrap"><iframe src="${fileUrl(previewRel)}?t=${Date.now()}" title="Preview"></iframe></div>
            <a class="small" href="${fileUrl(previewRel)}" target="_blank" rel="noopener">Open full size</a>`
            : '<div class="empty">Build a preview to watch it here, without the voice: quick, for checking the pictures and timing.</div>'}
          ${s.outline ? `<div class="card outline"><h3>Outline</h3>${s.outline.map((sc, i) => `
            <div><b>${i + 1}. ${esc(sc.title)}</b><ol>${sc.steps.map(st => `<li><span class="t">${st.at}s</span>${esc(st.text)}</li>`).join('')}</ol></div>`).join('')}</div>` : ''}
        </section>
      </div>
    </div>`;
  const ed = $('#editor', el), st = $('#saved-state', el);
  ed.addEventListener('input', () => { dirty = true; st.textContent = 'unsaved changes'; });
  ed.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveScript(); }
    if (e.key === 'Tab') { e.preventDefault(); const a = ed.selectionStart; ed.setRangeText('  ', a, ed.selectionEnd, 'end'); dirty = true; }
  });
  async function saveScript() {
    try { await api(`/api/projects/${P.id}/script`, { method: 'PUT', json: { text: ed.value } }); dirty = false; st.textContent = 'saved'; return true; } catch (e) { fail(e); return false; }
  }
  $('#save', el).addEventListener('click', saveScript);
  $('#example', el)?.addEventListener('click', async () => { await api(`/api/projects/${P.id}/script/example`, { method: 'POST' }); dirty = false; script(el); });
  $('#preview', el).addEventListener('click', async () => { if (dirty && !(await saveScript())) return; run('preview'); });
  $('#go-settings', el)?.addEventListener('click', e => { e.preventDefault(); openSettings(); });
  const ask = async (instruction) => {
    if (dirty && !(await saveScript())) return;
    run('draft', { instruction });
  };
  $('#revise', el)?.addEventListener('click', () => {
    const t = $('#instr', el).value.trim();
    if (!t) { toast('Say what to change first'); $('#instr', el).focus(); return; }
    ask(t);
  });
  $('#redraft', el)?.addEventListener('click', () => {
    if (has && !confirm('Write a new script from the material? It replaces the current one.')) return;
    api(`/api/projects/${P.id}/script`, { method: 'PUT', json: { text: has ? '' : ed.value } }).then(() => ask($('#instr', el).value.trim()));
  });
}

// Voice & check: speak it, check every scene, look at the frames
function check(el) {
  const j = lastJob('check');
  const r = j && j.result;
  el.innerHTML = `
    <div class="stack">
      <div class="row"><div class="grow stack" style="gap:4px"><h2>Voice & check</h2>
        <p class="small mute">Speaks the narration, builds the player and plays every scene through, looking for scenes that stall, labels that collide and steps with no caption. Look at the frames before rendering.</p></div>
        <button class="btn" id="go" type="button" ${P.has_script ? '' : 'disabled'}>${j ? 'Check again' : 'Speak and check'}</button></div>
      ${!P.has_script ? '<div class="empty">Write or upload a script first.</div>' : !j ? '<div class="empty">Not checked yet.</div>' : `
        <div class="row"><span class="pill ${r && r.ok ? 'good' : 'bad'}">${r && r.ok ? 'Passed' : 'Problems found'}</span><span class="small mute">${ago(j.finished)}</span></div>
        ${j.error && !(r && r.summary) ? `<p class="err">${esc(j.error)}</p>` : ''}
        ${r && r.summary && r.summary.length ? `<pre class="findings">${esc(r.summary.join('\n'))}</pre>` : ''}
        ${r && r.preview ? `<div class="cols"><div class="frame-wrap"><iframe src="${fileUrl(r.preview)}?t=${Date.now()}" title="Player with voice"></iframe></div>
          <div class="stack"><h3>With the voice</h3><p class="small mute">The player as it will ship: press play and listen through. ← and → move between scenes.</p>
          <a class="btn ghost small" style="justify-self:start" href="${fileUrl(r.preview)}" target="_blank" rel="noopener">Open full size</a></div></div>` : ''}
        ${r && r.frames && r.frames.length ? `<h3>Every step, mid-way</h3><div class="frames">${r.frames.map(f => `<a href="${fileUrl(f)}" target="_blank" rel="noopener"><img loading="lazy" src="${fileUrl(f)}" alt="${esc(f)}"></a>`).join('')}</div>` : ''}`}
    </div>`;
  $('#go', el).addEventListener('click', () => run('check'));
}

// Video and PowerPoint: two forms over the same project settings. The voice
// card is on both (one voice for the project); everything else is each one's own.
const OUT_INFO = {
  html: ['HTML player', 'One file that plays offline in any browser, with voice and captions'],
  mp4: ['MP4', 'The whole explainer'],
  scenes: ['One MP4 per scene', 'Each scene on its own'],
  youtube: ['YouTube-ready MP4', 'H.264 + AAC 48 kHz, your size and frame rate, title cards, its own .srt'],
  srt: ['Subtitles (.srt)', 'For YouTube, players and editors'],
  vtt: ['Subtitles (.vtt)', 'For web players'],
  chapters: ['Chapters', 'Timestamps to paste into the video description'],
  thumbnail: ['Thumbnail', '1280×720 JPEG from the first scene'],
};
const seg = (name, items, cur) => `<div class="seg" role="radiogroup">${items.map(([v, l]) => `<label><input type="radio" name="${name}" value="${v}" ${String(v) === String(cur) ? 'checked' : ''}>${l}</label>`).join('')}</div>`;
const tick = (name, on, title, sub) => `<label class="opt"><input type="checkbox" name="${name}" ${on ? 'checked' : ''}><div><b>${title}</b><span>${sub}</span></div></label>`;
const voiceCard = s => `
      <section class="card stack">
        <div class="row"><h2 class="grow">Voice</h2><span class="small mute">Shared by the video and the PowerPoint</span></div>
        <div class="grid2">
          <label class="field"><span>Voice</span><select name="voice">${OPT.voices.map(v => `<option value="${v.id}" ${v.id === s.voice ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select></label>
          <label class="field"><span>Speed <b id="spd">${s.speed.toFixed(2)}×</b></span><input type="range" name="speed" min="0.7" max="1.3" step="0.05" value="${s.speed}"></label>
        </div>
        <div class="field"><span>Pronunciations</span>
          <small class="mute">How to say a spelling: “P&amp;L” → “P and L”. Captions keep the spelling.</small>
          <div id="say" class="stack" style="gap:6px"></div>
          <button class="btn ghost small" type="button" id="add-say" style="justify-self:start">Add one</button></div>
      </section>`;

// One settings form: draws it, saves each change, and on submit saves and runs the job.
function settingsForm(el, body, collect, submit) {
  const s = P.settings;
  el.innerHTML = `<form id="rs" class="stack">${voiceCard(s)}${body}</form>`;
  const form = $('#rs', el);
  let say = s.say.map(x => [...x]);
  const drawSay = () => {
    $('#say', el).innerHTML = say.map(([a, b], i) => `<div class="say"><input data-say="${i}" data-k="0" value="${esc(a)}" placeholder="Spelling" aria-label="Spelling"><input data-say="${i}" data-k="1" value="${esc(b)}" placeholder="Say it as" aria-label="Say it as"><button class="btn ghost small" type="button" data-unsay="${i}" aria-label="Remove">×</button></div>`).join('')
      || '<p class="small mute">None.</p>';
  };
  drawSay();
  const all = () => { const f = new FormData(form); return { voice: f.get('voice'), speed: +f.get('speed'), say: say.filter(([a]) => a.trim()), ...collect(f) }; };
  let t;
  const persist = () => { clearTimeout(t); t = setTimeout(async () => {
    try { P = { ...P, ...(await api(`/api/projects/${P.id}`, { method: 'PATCH', json: { settings: all() } })) }; } catch (e) { fail(e); }
  }, 350); };
  form.addEventListener('input', e => {
    if (e.target.dataset.say !== undefined) say[+e.target.dataset.say][+e.target.dataset.k] = e.target.value;
    if (e.target.name === 'speed') $('#spd', el).textContent = `${(+e.target.value).toFixed(2)}×`;
    persist();
  });
  form.addEventListener('change', persist);
  $('#add-say', el).addEventListener('click', () => { say.push(['', '']); drawSay(); });
  el.addEventListener('click', e => { const b = e.target.closest('[data-unsay]'); if (b) { say.splice(+b.dataset.unsay, 1); drawSay(); persist(); } });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const st = all();
    clearTimeout(t);
    try { await api(`/api/projects/${P.id}`, { method: 'PATCH', json: { settings: st } }); submit(st); } catch (err) { fail(err); }
  });
  return form;
}

function render(el) {
  const s = P.settings;
  settingsForm(el, `
      <section class="card stack">
        <h2>Sound</h2>
        <div class="grid2">
          ${tick('sound', s.sound, 'Voice in the video', 'Off: the MP4s are silent (a silent track, so players and YouTube accept them). The HTML player always has the voice')}
        </div>
      </section>
      <section class="card stack">
        <h2>Picture</h2>
        <div class="grid2">
          <div class="field"><span>Size</span>${seg('resolution', [['720p', '720p'], ['1080p', '1080p'], ['4k', '4K'], ['vertical', 'Vertical 9:16']], s.resolution)}
            <small class="mute">Vertical (for Shorts) applies to the YouTube file; the other files stay 16:9 at 1080p. 4K takes about four times as long.</small></div>
          <div class="field"><span>Frame rate</span>${seg('fps', [[25, '25'], [30, '30'], [60, '60']], s.fps)}</div>
          <div class="field"><span>Look</span>${seg('theme', [['light', 'Light'], ['dark', 'Dark']], s.theme)}</div>
          ${tick('burn_captions', s.burn_captions, 'Captions in the picture', 'Off: a clean picture, with the captions only in the subtitle files')}
          ${tick('intro', s.intro, 'Title card at the start', '3 seconds: the title and description (YouTube file)')}
          <div class="stack" style="gap:6px">${tick('outro', s.outro, 'Card at the end', '3 seconds (YouTube file)')}
            <input name="outro_text" maxlength="120" value="${esc(s.outro_text)}" aria-label="End card text"></div>
        </div>
      </section>
      <section class="card stack">
        <div class="row"><h2 class="grow">Files to make</h2><span class="small mute">Remembered for next time</span></div>
        <div class="opts">${OPT.outputs.map(o => `<label class="opt"><input type="checkbox" name="outputs" value="${o}" ${s.outputs.includes(o) ? 'checked' : ''}><div><b>${OUT_INFO[o][0]}</b><span>${OUT_INFO[o][1]}</span></div></label>`).join('')}</div>
      </section>
      <div class="row end"><span class="small mute grow">${P.has_script ? 'Speaks, builds, records every frame and encodes: a few minutes for a few minutes of video. A PowerPoint already made is kept.' : 'Write or upload a script first.'}</span>
        <button class="btn" id="go" type="submit" ${P.has_script ? '' : 'disabled'}>Make the video</button></div>`,
  f => ({ sound: !!f.get('sound'), resolution: f.get('resolution'), fps: +f.get('fps'), theme: f.get('theme'),
    burn_captions: !!f.get('burn_captions'), intro: !!f.get('intro'), outro: !!f.get('outro'), outro_text: f.get('outro_text') || '',
    outputs: f.getAll('outputs') }),
  st => { if (!st.outputs.length) { toast('Pick at least one file to make'); return; } run('render', { outputs: st.outputs }); });
}

function deck(el) {
  const d = P.settings.deck;
  const form = settingsForm(el, `
      <section class="card stack">
        <h2>Slides</h2>
        <div class="grid2">
          <div class="field"><span>Slides</span>${seg('d_per', [['step', 'A slide per step'], ['scene', 'A slide per scene'], ['whole', 'One continuous video']], d.per)}
            <small class="mute">Per step: one sentence and its animation on each slide. Per scene: each scene plays through on its own slide. One continuous video: the whole explainer on one slide, playing like the MP4.</small></div>
          <div class="field"><span>Captions</span>${seg('d_captions', [['text', 'As text'], ['picture', 'In the picture'], ['none', 'None']], d.captions)}
            <small class="mute">As text: an editable text box at the bottom of the slide. With a slide per scene or one continuous video the sentences change while it plays, so “As text” draws them in the picture.</small></div>
          ${tick('d_title_slide', d.title_slide, 'Title slide', 'The title and description first')}
          ${tick('d_notes', d.notes, 'Speaker notes', 'What the narrator says, in each slide’s notes')}
        </div>
      </section>
      <section class="card stack">
        <h2>Playing</h2>
        <div class="grid2">
          ${tick('d_sound', d.sound, 'Sound', 'The voice plays with each slide’s animation. Off: silent, for you to talk over')}
          <div class="field"><span>Animation starts</span>${seg('d_start', [['auto', 'When the slide opens'], ['click', 'On a click']], d.start)}</div>
          ${tick('d_advance', d.advance, 'Move on by itself', 'The next slide follows when the animation ends (a click still moves on sooner). Not with “On a click”')}
        </div>
      </section>
      <section class="card stack">
        <h2>Picture</h2>
        <div class="grid2">
          <div class="field"><span>Size</span>${seg('d_resolution', [['720p', '720p (smaller file)'], ['1080p', '1080p']], d.resolution)}</div>
          <div class="field"><span>Frame rate</span>${seg('d_fps', [[25, '25'], [30, '30']], d.fps)}</div>
          <div class="field"><span>Look</span>${seg('d_theme', [['light', 'Light'], ['dark', 'Dark']], d.theme)}</div>
        </div>
      </section>
      <div class="row end"><span class="small mute grow">${P.has_script ? 'Records the animation for the slides and builds the .pptx. Plays in PowerPoint 2016 or later and Keynote; Google Slides shows the still pictures only. Videos already made are kept.' : 'Write or upload a script first.'}</span>
        <button class="btn" id="go" type="submit" ${P.has_script ? '' : 'disabled'}>Make the PowerPoint</button></div>`,
  f => ({ deck: { per: f.get('d_per'), captions: f.get('d_captions'), title_slide: !!f.get('d_title_slide'), notes: !!f.get('d_notes'),
    sound: !!f.get('d_sound'), start: f.get('d_start'), advance: !!f.get('d_advance'),
    resolution: f.get('d_resolution'), fps: +f.get('d_fps'), theme: f.get('d_theme') } }),
  () => run('deck'));
  // "Move on by itself" doesn't apply when the animation waits for a click: dimmed, but kept as chosen
  const sync = () => { const off = $('[name=d_start]:checked', form).value === 'click'; $('[name=d_advance]', form).closest('.opt').style.opacity = off ? .5 : 1; };
  form.addEventListener('change', sync);
  sync();
}

// Files: watch it, download it
const KIND = n => n.endsWith('.pptx') ? 'PowerPoint' : n.endsWith('-youtube.mp4') ? 'YouTube' : n.endsWith('-youtube.srt') ? 'YT subs' : /-scene-\d\d/.test(n) ? 'Scene' : n.endsWith('-chapters.txt') ? 'Chapters'
  : n.endsWith('-thumbnail.jpg') ? 'Thumbnail' : n.split('.').pop().toUpperCase();
async function outputs(el) {
  const files = P.outputs;
  if (!files.length) { el.innerHTML = '<div class="empty">Nothing made yet. Make a video on the Video tab, or slides on the PowerPoint tab.</div>'; return; }
  const by = n => files.find(f => f.name.endsWith(n));
  const yt = files.find(f => f.name.endsWith('-youtube.mp4')), mp4 = files.find(f => /\.mp4$/.test(f.name) && !/-scene-|-youtube/.test(f.name));
  const vtt = by('.vtt'), chap = by('-chapters.txt'), thumb = by('-thumbnail.jpg');
  const vid = yt || mp4;
  const chapText = chap ? await fetch(fileUrl(`outputs/${chap.name}`)).then(r => r.text()) : '';
  const r = lastJob('render') || lastJob('deck');
  el.innerHTML = `
    <div class="cols">
      <section class="stack">
        ${vid ? `<video controls preload="metadata" ${thumb ? `poster="${fileUrl(`outputs/${thumb.name}`)}"` : ''} src="${fileUrl(`outputs/${vid.name}`)}">
          ${vtt && vid === mp4 ? `<track kind="captions" srclang="en" label="English" src="${fileUrl(`outputs/${vtt.name}`)}">` : ''}</video>` : '<div class="empty">No video made yet.</div>'}
        ${chapText ? `<div class="stack" style="gap:8px"><div class="row"><h3 class="grow">Chapters</h3><button class="btn ghost small" id="copy" type="button">Copy</button></div>
          <pre class="chapters">${esc(chapText)}</pre>${r && r.result && r.result.notes && r.result.notes.length ? `<p class="small mute">${esc(r.result.notes.join('. '))}.</p>` : ''}</div>` : ''}
      </section>
      <section class="stack">
        <div class="row"><h2 class="grow">Files</h2><a class="btn small" href="/api/projects/${P.id}/outputs.zip">Download all</a></div>
        ${r && r.finished ? `<p class="small mute">Rendered ${ago(r.finished)}${r.result && r.result.minutes ? `, ${r.result.minutes} min long` : ''}.</p>` : ''}
        <div class="outs">${files.map(f => `<div class="out"><span class="kind">${esc(KIND(f.name))}</span><span class="name grow">${esc(f.name)}</span>
          <span class="small mute">${size(f.size)}</span><a class="btn ghost small" href="${fileUrl(`outputs/${f.name}`, true)}">Download</a></div>`).join('')}</div>
        <p class="small mute">For YouTube: upload the YouTube MP4, add its <code>-youtube.srt</code> under Subtitles, and paste the chapters into the description.</p>
      </section>
    </div>`;
  $('#copy', el)?.addEventListener('click', () => navigator.clipboard.writeText(chapText).then(() => toast('Chapters copied')).catch(() => toast('Couldn’t copy: select the text instead')));
}

// ── jobs ──────────────────────────────────────────────────────────────
const JOB_NAME = { preview: 'Preview', check: 'Voice & check', render: 'Video', deck: 'PowerPoint', draft: 'Claude' };
async function run(kind, opts = {}) {
  try { const j = await api(`/api/projects/${P.id}/jobs`, { method: 'POST', json: { kind, ...opts } }); watch(j.id); }
  catch (e) { fail(e); }
}
function watch(id) {
  const box = $('#job');
  watching = { id, open: watching && watching.open };
  box.hidden = false;
  const tick = async () => {
    if (!watching || watching.id !== id) return;
    let j;
    try { j = await api(`/api/jobs/${id}`); } catch { setTimeout(tick, 3000); return; }
    const live = j.state === 'queued' || j.state === 'running';
    box.className = `job ${j.state}`;
    box.innerHTML = `
      <div class="row"><b>${JOB_NAME[j.kind]}</b><span class="small mute grow">${esc(j.phase)}</span>
        <button class="btn ghost small" id="log-t" type="button">${watching.open ? 'Hide log' : 'Log'}</button>
        ${live ? '<button class="btn ghost small" id="cancel" type="button">Cancel</button>' : '<button class="btn ghost small" id="close" type="button">Close</button>'}</div>
      <div class="bar"><i style="width:${Math.round((j.state === 'done' ? 1 : j.progress) * 100)}%"></i></div>
      ${j.error ? `<p class="err">${esc(j.error)}</p>` : ''}
      ${j.state === 'done' && j.kind === 'draft' && j.result.cost_usd != null ? `<p class="small">Script written. Claude: about $${j.result.cost_usd.toFixed(2)}${j.result.notes && j.result.notes.length ? `. ${esc(j.result.notes.join(' '))}` : ''}</p>` : ''}
      ${watching.open ? `<pre id="log">${esc(j.log.join('\n'))}</pre>` : ''}`;
    $('#log-t', box).onclick = () => { watching.open = !watching.open; tick(); };
    $('#cancel', box)?.addEventListener('click', () => api(`/api/jobs/${id}/cancel`, { method: 'POST' }).catch(fail));
    $('#close', box)?.addEventListener('click', () => { box.hidden = true; watching = null; });
    const lg = $('#log', box); if (lg) lg.scrollTop = lg.scrollHeight;
    if (live) { setTimeout(tick, 1500); return; }
    if (j.state === 'done' && !watching.open && j.kind !== 'draft') setTimeout(() => { if (watching && watching.id === id && !watching.open) { box.hidden = true; watching = null; } }, 5000);
    if (P && P.id === j.project) {
      const go = { preview: 'script', draft: 'script', check: 'check', render: j.state === 'done' ? 'outputs' : 'render', deck: j.state === 'done' ? 'outputs' : 'deck' }[j.kind];
      toast(j.state === 'done' ? `${JOB_NAME[j.kind]}: done` : `${JOB_NAME[j.kind]}: ${j.state}`);
      dirty = false;
      if (location.hash === `#/p/${P.id}/${go}`) openProject(P.id, go); else location.hash = `#/p/${P.id}/${go}`;
    }
  };
  tick();
}

// ── settings ──────────────────────────────────────────────────────────
function openSettings() {
  const d = $('#settings'), f = $('#settings-form'), c = OPT.claude;
  f.model.innerHTML = c.models.map(m => `<option ${m === c.model ? 'selected' : ''}>${m}</option>`).join('');
  f.rounds.value = c.rounds;
  f.key.value = '';
  $('#key-state').textContent = c.key_from_env ? 'Set on the server (ANTHROPIC_API_KEY); that one is used.' : c.has_key ? 'A key is saved. Type a new one to replace it; clear it with a single space.' : 'No key yet.';
  d.showModal();
}
$('#open-settings').addEventListener('click', openSettings);
$('#settings-form').addEventListener('submit', async e => {
  if (e.submitter && e.submitter.value !== 'save') return;
  const f = e.target, body = { model: f.model.value, rounds: +f.rounds.value };
  if (f.key.value) body.anthropic_api_key = f.key.value.trim();
  try { OPT.claude = await api('/api/settings', { method: 'PUT', json: body }); toast('Settings saved'); if (P) route(); } catch (err) { e.preventDefault(); fail(err); }
});

// ── start ─────────────────────────────────────────────────────────────
(async () => {
  try { OPT = await api('/api/options'); } catch (e) { main.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  api('/api/health').then(h => {
    if (h.ok) return;
    const miss = Object.entries(h.parts).filter(([, v]) => !v).map(([k]) => k.replace('_', ' '));
    const el = $('#health'); el.hidden = false; el.textContent = `Missing: ${miss.join(', ')}`;
  }).catch(() => {});
  route();
})();
