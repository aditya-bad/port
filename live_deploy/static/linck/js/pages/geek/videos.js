// Videos on the replay page: the replay told as a narrated, animated MP4,
// made on the server (app/video/), one at a time, at the lowest priority.
// The panel makes one of the episodes the replay shows (a cycle still
// running is told up to now), shows each one's progress with the time
// left, and plays, downloads, shares or deletes the finished ones. It
// polls while one is being made; leaving the page doesn't stop it.

import { api } from '../../core/api.js';
import { html, mount } from '../../core/dom.js';
import { dStamp } from '../../core/format.js';
import { openDialog } from '../../ui/dialog.js';
import { say, fail } from '../../ui/toast.js';
import { working, mmss, eta } from '../../data/videos.js';

const STATUS = { queued: 'Waiting its turn', voicing: 'Recording the voice', drawing: 'Drawing the frames', done: 'Ready', failed: 'Failed' };
const mb = b => (b == null ? '' : `${(b / 1e6).toFixed(1)} MB`);

// The panel lives in its own box, which the replay page puts back into
// its slot (attach) each time it repaints, so a video's progress and a
// playing video survive the page's repaints.
export function videosPanel(depId, current) {
  const host = document.createElement('div');
  host.className = 'vid-host';
  const S = { info: null, list: [], playing: null, voice: null, busy: false, open: false, timer: null, alive: true };

  async function load() {
    try {
      const [info, list] = await Promise.all([S.info ? S.info : api.videoInfo(), api.videos(depId)]);
      if (!S.alive) return;
      S.info = info; S.list = list;
      if (!S.voice) S.voice = info.default_voice;
      if (!S.playing) { const done = list.find(v => v.ready); if (done && S.open) S.playing = done.id; }
    } catch (e) { if (S.alive) S.error = e.message || String(e); }
    paint();
    clearTimeout(S.timer);
    if (S.alive && S.list.some(working)) S.timer = setTimeout(load, 3000);
  }

  function row(v) {
    const live = v.live ? html`<span class="rp-chip sm" style="--k:var(--r)" title="The cycle was still running: the video tells it up to then">as of ${dStamp(v.as_of)}</span>` : '';
    const p = Math.round((v.progress || 0) * 100);
    const status = working(v)
      ? html`<span class="small">${v.status === 'queued' && v.ahead ? `${STATUS.queued} · ${v.ahead} ahead` : STATUS[v.status]}</span>
          <span class="vid-bar" role="progressbar" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100"><i style="width:${Math.max(2, p)}%"></i></span>
          <span class="tiny mute mono">${p}%${eta(v) ? ` · ${eta(v)}` : ''}</span>`
      : v.status === 'failed' ? html`<span class="small bad">Failed</span>` : html`<span class="small">${mmss(v.seconds)} · ${mb(v.bytes)}</span>`;
    return html`<div class="vid-row ${S.playing === v.id ? 'on' : ''}" data-vid="${v.id}">
      <div class="col" style="gap:2px;min-width:0"><b class="small">${v.title}</b>
        <span class="tiny mute">${dStamp(v.created_at)} ${live}</span>
        ${v.note ? html`<span class="tiny ${v.status === 'failed' ? 'bad' : 'mute'}" data-vid-note>${v.note}</span>` : ''}</div>
      <div class="vid-status">${status}</div>
      <div class="row" style="gap:6px;justify-content:flex-end">
        ${v.ready ? html`<button type="button" class="btn btn-xs" data-act="vWatch" data-id="${v.id}">${S.playing === v.id ? 'Hide' : '▶ Watch'}</button>
          <a class="btn btn-xs" href="/v2/videos/${v.id}/file?download=1" download data-vid-dl>Download</a>
          ${typeof navigator !== 'undefined' && navigator.share ? html`<button type="button" class="btn btn-xs" data-act="vShare" data-id="${v.id}">Share</button>` : ''}` : ''}
        <button type="button" class="btn btn-xs btn-quiet" data-act="vDelete" data-id="${v.id}" aria-label="${working(v) ? 'Stop and delete' : 'Delete'}">${working(v) ? 'Stop' : 'Delete'}</button>
      </div></div>`;
  }

  function player(v) {
    return html`<div class="vid-player" data-vid-player="${v.id}">
      <video controls preload="metadata" playsinline src="/v2/videos/${v.id}/file"></video>
      ${v.chapters && v.chapters.length ? html`<div class="vid-chaps" role="group" aria-label="Chapters">${v.chapters.map(c => html`<button type="button" class="btn btn-xs btn-quiet" data-act="vSeek" data-t="${c.t}"><span class="mono tiny mute">${mmss(c.t)}</span> ${c.title}</button>`)}</div>` : ''}
    </div>`;
  }

  function paint() {
    if (!S.alive) return;
    const sel = current();
    if (!S.open && !S.list.length) { mount(host, ''); return; }
    const info = S.info || {};
    const playing = S.list.find(v => v.id === S.playing && v.ready);
    const busy = S.list.some(v => working(v) && v.ep_from === sel.from && v.ep_to === sel.to);
    mount(host, html`<div class="gk-panel rp-videos" data-videos>
      <h3>Videos <span class="s">the replay told by a narrator: what it did, why, and with what numbers</span></h3>
      ${info.can_make === false ? html`<p class="gk-note" data-vid-cant>${info.why}</p>` : html`<div class="vid-make row">
        <span class="small">Make a video of <b>${sel.label}</b>${sel.open ? html` <span class="tiny mute">· still running, told up to now</span>` : ''}</span>
        <label class="small">Voice <select data-vid-voice>${(info.voices || []).map(x => html`<option value="${x.id}" ${x.id === S.voice ? 'selected' : ''}>${x.label}</option>`)}</select></label>
        <button type="button" class="btn btn-sm btn-primary" data-act="vMake" ${S.busy || busy ? 'disabled' : ''}>${busy ? 'Being made…' : S.busy ? 'Starting…' : '🎬 Make video'}</button>
        <span class="tiny mute">Made on the server one at a time, at low priority: it can take a while, and keeps going if you leave this page.</span>
      </div>`}
      ${S.error ? html`<p class="gk-note bad">${S.error}</p>` : ''}
      ${playing ? player(playing) : ''}
      ${S.list.length ? html`<div class="vid-list">${S.list.map(row)}</div>` : ''}
    </div>`);
  }

  const handlers = {
    vMake: async () => {
      const sel = current();
      S.busy = true; S.error = ''; paint();
      try {
        await api.makeVideo(depId, { from: sel.from, to: sel.to, voice: S.voice });
        say('Video queued: it’s being made on the server.');
      } catch (e) { fail(e, 'Couldn’t start the video'); }
      S.busy = false;
      await load();
    },
    vWatch: d => { S.playing = S.playing === d.id ? null : d.id; paint(); },
    vSeek: d => { const v = host.querySelector('video'); if (v) { v.currentTime = +d.t; v.play().catch(() => {}); } },
    vShare: async d => {
      const v = S.list.find(x => x.id === d.id);
      try {
        const blob = await (await fetch(`/v2/videos/${d.id}/file`, { credentials: 'same-origin' })).blob();
        const file = new File([blob], `${v.title.replace(/[^A-Za-z0-9]+/g, '-')}.mp4`, { type: 'video/mp4' });
        if (navigator.canShare && navigator.canShare({ files: [file] })) await navigator.share({ files: [file], title: v.title });
        else await navigator.share({ title: v.title, url: `${location.origin}/v2/videos/${d.id}/file` });
      } catch (e) { if (e && e.name !== 'AbortError') fail(e, 'Couldn’t share it'); }
    },
    vDelete: d => {
      const v = S.list.find(x => x.id === d.id);
      openDialog({
        kicker: 'CONFIRM', title: working(v) ? 'Stop making this video?' : 'Delete this video?',
        lines: [working(v) ? 'It stops now, and what’s been made so far is thrown away.' : `${v.title}, ${mmss(v.seconds)}. The file is deleted from the server.`],
        okLabel: working(v) ? 'Stop and delete' : 'Delete',
        onOk: async () => { await api.deleteVideo(d.id); if (S.playing === d.id) S.playing = null; await load(); return true; },
      });
    },
  };
  const onClick = e => {
    const b = e.target.closest('[data-act]');
    if (!b || !host.contains(b) || !handlers[b.dataset.act]) return;
    e.preventDefault();
    handlers[b.dataset.act](b.dataset, b);
  };
  const onChange = e => { if (e.target.matches('[data-vid-voice]')) S.voice = e.target.value; };
  host.addEventListener('click', onClick);
  host.addEventListener('change', onChange);
  load();
  return {
    attach(slot) { if (slot && host.parentNode !== slot) slot.appendChild(host); },
    open() { S.open = true; if (!S.playing) { const done = S.list.find(v => v.ready); if (done) S.playing = done.id; } paint(); host.scrollIntoView({ behavior: 'smooth', block: 'start' }); },
    repaint: paint,
    stop() { S.alive = false; clearTimeout(S.timer); host.removeEventListener('click', onClick); host.removeEventListener('change', onChange); },
  };
}
