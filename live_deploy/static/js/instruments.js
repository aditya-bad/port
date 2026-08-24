// live_deploy — Instrument Browser view: search Kite's instrument
// master by symbol/name and subscribe/unsubscribe, instead of needing
// to already know a raw instrument_token. Talks to GET
// /instruments/search (new) plus the ALREADY-EXISTING POST /instruments
// / DELETE /instruments/{token} — this view is a real front-end for
// those, not a new subscription mechanism of its own.
//
// Deliberately separate from Dashboard's own small "Subscribed
// Instruments" widget (still there, unchanged) — that one is a glance-
// only summary; this page is where you actually go find and manage
// what's subscribed.

const Instruments = {
  _searchTimer: null,
  _subscribedTokens: new Set(),

  async load() {
    document.getElementById('instSearchResults').innerHTML = '';
    document.getElementById('instSearchInput').value = '';
    await this.loadSubscribed();
  },

  async loadSubscribed() {
    const el = document.getElementById('instSubscribedList');
    el.innerHTML = spinnerHtml();
    let data;
    try {
      data = await Api.listInstruments();
    } catch (e) {
      el.innerHTML = emptyHtml(`Could not load subscribed instruments — ${escapeHtml(e.message || String(e))}`);
      return;
    }
    this._subscribedTokens = new Set(data.subscribed.map(i => i.instrument_token));
    if (!data.subscribed.length) {
      el.innerHTML = emptyHtml('Nothing subscribed yet.');
      return;
    }
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr><th>Symbol</th><th>Token</th><th>Source</th><th></th></tr></thead>
      <tbody>${data.subscribed.map(i => `<tr>
        <td>${escapeHtml(i.symbol)}</td>
        <td>${i.instrument_token}</td>
        <td>${i.static ? 'tokens.json' : 'dynamic'}</td>
        <td>${i.static
          ? '<span style="color:var(--parchment)">permanent</span>'
          : `<button class="btn btn-danger btn-sm" onclick="Instruments.unsubscribe(${i.instrument_token})">Unsubscribe</button>`}
        </td>
      </tr>`).join('')}</tbody></table>
      </div>
    `;
  },

  // Debounced — fires ~350ms after the user stops typing, not on every
  // single keystroke, so a full search word doesn't trigger a search
  // request per character.
  onSearchInput() {
    clearTimeout(this._searchTimer);
    const q = document.getElementById('instSearchInput').value.trim();
    const el = document.getElementById('instSearchResults');
    if (q.length < 2) {
      el.innerHTML = q.length === 0 ? '' : emptyHtml('Keep typing — at least 2 characters');
      return;
    }
    this._searchTimer = setTimeout(() => this.runSearch(q), 350);
  },

  async runSearch(q) {
    const el = document.getElementById('instSearchResults');
    el.innerHTML = spinnerHtml('Searching…');
    let data;
    try {
      data = await Api.searchInstruments(q);
    } catch (e) {
      el.innerHTML = emptyHtml(`Search failed — ${escapeHtml(e.message || String(e))}`);
      return;
    }
    // The input may have changed again while this request was in
    // flight — drop a stale response rather than overwrite newer results.
    if (document.getElementById('instSearchInput').value.trim() !== q) return;

    if (!data.results.length) {
      el.innerHTML = emptyHtml(`No instruments matching "${escapeHtml(q)}"`);
      return;
    }
    el.innerHTML = `
      <div class="table-wrap">
      <table><thead><tr>
        <th>Symbol</th><th>Name</th><th>Exchange</th><th>Type</th>
        <th>Strike</th><th>Expiry</th><th>Token</th><th></th>
      </tr></thead>
      <tbody>${data.results.map(r => this._resultRowHtml(r)).join('')}</tbody></table>
      </div>
      <div class="table-note">${data.results.length} result(s)${data.results.length >= 30 ? ' (capped — refine your search)' : ''}</div>
    `;
    this._enhanceResults();
    UIKit.enhanceTablesSoon();
  },

  // Instant client-side facets over the already-returned rows -- the
  // backend search itself stays exactly as-is.
  _enhanceResults() {
    const root = document.getElementById('instSearchResults');
    const table = root?.querySelector('table');
    if (!root || !table || root.querySelector('.ux-instrument-filters')) return;
    const rows = [...(table.tBodies?.[0]?.rows || [])];
    if (!rows.length) return;
    const exchanges = [...new Set(rows.map(r => (r.cells[2]?.textContent || '').split('·')[0].trim()).filter(Boolean))].sort();
    const types = [...new Set(rows.map(r => (r.cells[3]?.textContent || '').trim()).filter(Boolean))].sort();
    const bar = document.createElement('div');
    bar.className = 'ux-instrument-filters';
    bar.innerHTML = `<select id="uxInstExchange"><option value="">All exchanges</option>${exchanges.map(x => `<option>${escapeHtml(x)}</option>`).join('')}</select><select id="uxInstType"><option value="">All types</option>${types.map(x => `<option>${escapeHtml(x)}</option>`).join('')}</select><label class="checkbox-row" style="margin:0;"><input type="checkbox" id="uxInstAvailable" style="width:auto;"> Available only</label><span class="card-sub" id="uxInstCount"></span>`;
    table.closest('.table-wrap')?.insertAdjacentElement('beforebegin', bar);
    const apply = () => {
      const ex = document.getElementById('uxInstExchange')?.value || '';
      const type = document.getElementById('uxInstType')?.value || '';
      const available = document.getElementById('uxInstAvailable')?.checked;
      let shown = 0;
      rows.forEach(r => {
        const rowEx = (r.cells[2]?.textContent || '').split('·')[0].trim();
        const rowType = (r.cells[3]?.textContent || '').trim();
        const subscribed = /subscribed/i.test(r.cells[r.cells.length - 1]?.textContent || '');
        const show = (!ex || rowEx === ex) && (!type || rowType === type) && (!available || !subscribed);
        r.style.display = show ? '' : 'none'; if (show) shown++;
      });
      const count = document.getElementById('uxInstCount'); if (count) count.textContent = `${shown} shown`;
    };
    bar.querySelectorAll('select,input').forEach(el => el.addEventListener('change', apply));
    apply();
  },

  _resultRowHtml(r) {
    const subscribed = this._subscribedTokens.has(r.instrument_token);
    return `<tr>
      <td>${escapeHtml(r.tradingsymbol)}</td>
      <td>${escapeHtml(r.name)}</td>
      <td>${escapeHtml(r.exchange)}<span style="color:var(--parchment)"> · ${escapeHtml(r.segment)}</span></td>
      <td>${escapeHtml(r.instrument_type)}</td>
      <td>${r.strike != null ? fmtNum(r.strike, 2) : '—'}</td>
      <td>${r.expiry || '—'}</td>
      <td>${r.instrument_token}</td>
      <td>${subscribed
        ? '<span style="color:var(--gain)">✓ subscribed</span>'
        : `<button class="btn btn-primary btn-sm" onclick="Instruments.subscribe(${r.instrument_token}, '${escapeHtml(r.tradingsymbol).replace(/'/g, "\\'")}')">Subscribe</button>`}
      </td>
    </tr>`;
  },

  async subscribe(token, symbol) {
    const { ok, data } = await Api.addInstrument(token, symbol);
    if (!ok) { alert(data.detail || 'Could not subscribe'); return; }
    await this.loadSubscribed();
    // Re-render whatever search results are currently showing so the
    // row we just subscribed flips to "✓ subscribed" without a fresh
    // search — cheap, and the query is still sitting in the input.
    const q = document.getElementById('instSearchInput').value.trim();
    if (q.length >= 2) await this.runSearch(q);
  },

  async unsubscribe(token) {
    await Api.removeInstrument(token);
    await this.loadSubscribed();
    const q = document.getElementById('instSearchInput').value.trim();
    if (q.length >= 2) await this.runSearch(q);
  },
};
