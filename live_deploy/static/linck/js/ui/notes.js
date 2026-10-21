// A "Notes" panel for a page about one day or one trade (a straddle, a
// session, a trade, a cycle's day): its journal notes and a box to add
// one. The box is built once and re-attached after each redraw.

import { html, $ } from '../core/dom.js';
import { journalBox } from './journal.js';

export function notesPanel({ deploymentId, day, positionId, title = 'Notes', sub = '' }) {
  const jb = journalBox({ deploymentId, day, positionId, general: !positionId, compact: true,
    emptyText: positionId ? 'No notes on this trade yet.' : 'No notes on this day yet.',
    placeholder: positionId ? 'What you saw in this trade, or would do differently…' : 'What happened this day…' });
  return {
    html: () => html`<div class="gk-panel"><h3>${title} <span class="s">${sub || (positionId ? 'on this trade' : 'on this day · in the Journal too')}</span></h3><div data-notes-slot></div></div>`,
    attach: el => jb.attach($('[data-notes-slot]', el)),
    destroy: () => jb.destroy(),
  };
}
