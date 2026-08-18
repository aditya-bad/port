// live_deploy — Strategy Catalog view: every strategy registered in
// app/strategies/, with a count of how many currently-active
// deployments are running it (a cross-reference that only matters once
// several instances of the same strategy exist with different
// configs), plus the Deploy modal.

// CONFIG_ENUM_OPTIONS/CONFIG_TIME_FIELD_RE and the actual per-key
// widget logic now live in api.js as configFieldHtml() -- shared with
// Detail's Edit Config modal (Step 51). See that module's own comment.

const Catalog = {
  _currentDeploy: {},
  _configBase: {},   // the full config object (including null-valued advanced keys), kept in sync as the source of truth across form <-> JSON toggles
  _strategies: [],   // every REGISTERED strategy (enabled or not) -- Browse filters to enabled-only itself, see _renderBrowse
  _activeCounts: {},
  _minimizedDrafts: [],   // see minimizeDeploy/restoreDraft/discardDraft below

  // quiet=true: event-driven background refresh -- see Dashboard.load()'s
  // own comment for why the spinner reset is skipped in that case.
  async load(quiet = false) {
    const el = document.getElementById('catalogList');
    if (!quiet) el.innerHTML = spinnerHtml();

    const [strategies, deployments] = await Promise.all([
      Api.listStrategies(),
      Api.listDeployments(),
    ]);
    this._strategies = strategies;

    // Active-deployment count per strategy_name, derived from the same
    // /deployments list the Deployed Strategies view uses — no
    // separate backend endpoint needed for this, the data's already
    // there.
    this._activeCounts = {};
    deployments.forEach(d => {
      if (d.status === 'active') this._activeCounts[d.strategy_name] = (this._activeCounts[d.strategy_name] || 0) + 1;
    });

    this._renderBrowse();
    markUpdated('catalogUpdatedLabel');
  },

  // ── Browse: cards for DEPLOYABLE strategies only. A strategy an
  // admin has disabled still exists in the registry (its running
  // deployments, if any, are completely unaffected — see the backend's
  // own create_deployment check) but has no business being offered for
  // a NEW deployment, so it's simply not shown here at all rather than
  // shown-but-greyed-out. ─────────────────────────────────────────────
  _renderBrowse() {
    const el = document.getElementById('catalogList');
    const strategies = this._strategies.filter(s => s.enabled !== false);

    if (!this._strategies.length) {
      el.innerHTML = emptyHtml('No strategies registered yet — nothing to deploy until one is added to app/strategies/.');
      return;
    }
    if (!strategies.length) {
      el.innerHTML = emptyHtml('Every registered strategy is currently disabled — check Admin Options.');
      return;
    }

    el.innerHTML = strategies.map(s => {
      const count = this._activeCounts[s.name] || 0;
      return `
        <div class="card">
          <div class="card-row">
            <div>
              <div class="card-title">
                ${escapeHtml(s.name)}
                ${count > 0 ? `<span class="tag tag-active">${count} active</span>` : ''}
              </div>
              <div class="card-sub">${escapeHtml(s.description || 'no description')}</div>
            </div>
            <div class="card-actions">
              <button class="btn btn-primary btn-sm" onclick='Catalog.openDeployModal(${JSON.stringify(s.name)}, ${JSON.stringify(s.default_config)})'>Deploy</button>
            </div>
          </div>
        </div>
      `;
    }).join('');
  },

  // ── Admin Options: EVERY registered strategy, enabled or not, with a
  // per-row toggle. Disabling here only affects NEW deployments (the
  // Browse tab, and the backend's own create_deployment check) —
  // deployments already running under a strategy stay completely
  // unaffected, same as pausing/stopping is a separate, per-deployment
  // action from this. Admin Options itself (enable/disable, Clear All)
  // moved to Account -> Admin, Step 69 -- see account.js's own
  // _renderAdmin/toggleStrategyEnabled/openClearAllModal. ─────────────

  openDeployModal(strategyName, defaultConfig) {
    this._currentDeploy = { strategyName, defaultConfig };
    document.getElementById('deployModalTitle').textContent = `Deploy: ${strategyName}`;
    document.getElementById('deployName').value = '';
    document.getElementById('deployCapital').value = '100000';
    document.getElementById('deployNotes').value = '';
    document.getElementById('deployMsg').textContent = '';

    // Always reopen in simple-form mode, regardless of how the LAST
    // modal session was left — a fresh deploy for a different strategy
    // shouldn't inherit "Advanced" just because the previous one did.
    document.getElementById('deployAdvancedToggle').checked = false;
    // removeProperty, not "= 'block'" -- an inline style beats any
    // stylesheet rule regardless of specificity, which silently broke
    // #deployConfigFields's own CSS grid layout on wider screens (it
    // computed to display:grid in the stylesheet, block from here won
    // anyway). Clearing the inline override lets the actual CSS
    // (grid above the mobile breakpoint, block below it) decide.
    document.getElementById('deployConfigFields').style.removeProperty('display');
    document.getElementById('deployConfig').style.display = 'none';
    this._renderConfigFields(defaultConfig || {});
    this._loadPresets(strategyName);

    document.getElementById('deployModal').classList.add('open');
  },

  // ── Minimize / restore / discard — "Cancel" clears the form; this
  // instead tucks the whole in-progress deploy (name, mode, capital,
  // notes, config in whichever mode — simple fields or raw JSON — it
  // was left in) into a dock the user can come back to, same idea as
  // Gmail's minimized compose window or Jira's minimized issue panes.
  // A stack, not a single slot: several drafts can queue up here at
  // once, each independently restorable or discardable. In-memory only
  // (survives navigating between views, since the dock/modal live
  // outside the router's view containers — see index.html's own
  // comment — but NOT a full page reload; there's no localStorage
  // backing this, unlike e.g. the dark-mode toggle). ──────────────────

  minimizeDeploy() {
    const advancedOn = document.getElementById('deployAdvancedToggle').checked;
    const rawJson = document.getElementById('deployConfig').value;
    // Capture configValues from whichever view is CURRENTLY authoritative
    // -- the raw JSON if Advanced is on (best-effort parsed, so the
    // hidden simple-form fields restore reasonably close to it even if
    // toggleAdvanced was never used to formally sync them), the simple
    // fields otherwise.
    let configValues;
    if (advancedOn) {
      try { configValues = JSON.parse(rawJson || '{}'); }
      catch (e) { configValues = this._configBase; }
    } else {
      configValues = this._readConfigFromFields();
    }
    this._minimizedDrafts.push({
      id: `draft_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      strategyName: this._currentDeploy.strategyName,
      defaultConfig: this._currentDeploy.defaultConfig,
      deployName: document.getElementById('deployName').value,
      mode: document.getElementById('deployMode').value,
      capital: document.getElementById('deployCapital').value,
      notes: document.getElementById('deployNotes').value,
      advancedOn, rawJson, configValues,
    });
    document.getElementById('deployModal').classList.remove('open');
    this._renderMinimizedDock();
  },

  restoreDraft(id) {
    const idx = this._minimizedDrafts.findIndex(d => d.id === id);
    if (idx === -1) return;
    const draft = this._minimizedDrafts[idx];
    this._minimizedDrafts.splice(idx, 1);
    this._renderMinimizedDock();

    this._currentDeploy = { strategyName: draft.strategyName, defaultConfig: draft.defaultConfig };
    document.getElementById('deployModalTitle').textContent = `Deploy: ${draft.strategyName}`;
    document.getElementById('deployName').value = draft.deployName;
    document.getElementById('deployMode').value = draft.mode;
    document.getElementById('deployCapital').value = draft.capital;
    document.getElementById('deployNotes').value = draft.notes;
    document.getElementById('deployMsg').textContent = '';

    document.getElementById('deployAdvancedToggle').checked = draft.advancedOn;
    // Fields are rebuilt from configValues either way, so _configBase
    // (and the hidden form underneath, if we're restoring straight into
    // Advanced) stay consistent for a later toggle back — same
    // "removeProperty, not '= block'" reasoning as openDeployModal.
    this._renderConfigFields(draft.configValues || draft.defaultConfig || {});
    if (draft.advancedOn) {
      document.getElementById('deployConfigFields').style.display = 'none';
      document.getElementById('deployConfig').style.display = 'block';
      document.getElementById('deployConfig').value = draft.rawJson;
    } else {
      document.getElementById('deployConfigFields').style.removeProperty('display');
      document.getElementById('deployConfig').style.display = 'none';
    }
    this._loadPresets(draft.strategyName);

    document.getElementById('deployModal').classList.add('open');
  },

  discardDraft(id, event) {
    if (event) event.stopPropagation();   // don't also trigger the chip's own restore click
    const idx = this._minimizedDrafts.findIndex(d => d.id === id);
    if (idx === -1) return;
    const draft = this._minimizedDrafts[idx];
    const label = draft.deployName || draft.strategyName;
    if (!confirm(`Discard the minimized draft "${label}"? This can't be undone.`)) return;
    this._minimizedDrafts.splice(idx, 1);
    this._renderMinimizedDock();
  },

  _renderMinimizedDock() {
    const dock = document.getElementById('minimizedDock');
    dock.classList.toggle('has-drafts', this._minimizedDrafts.length > 0);
    dock.innerHTML = this._minimizedDrafts.map(d => `
      <div class="minimized-chip" onclick="Catalog.restoreDraft('${d.id}')" title="Click to resume this deploy">
        <div style="min-width:0;">
          <div class="minimized-chip-label">📝 ${escapeHtml(d.deployName || '(unnamed)')}</div>
          <div class="minimized-chip-sub">${escapeHtml(d.strategyName)}</div>
        </div>
        <button class="minimized-chip-close" onclick="Catalog.discardDraft('${d.id}', event)" aria-label="Discard draft">✕</button>
      </div>
    `).join('');
  },

  // ── Config presets: save/load/delete a named snapshot of the config
  // fields for THIS strategy, so redeploying with the same values
  // doesn't mean retyping a dozen-plus fields every time. Scoped to
  // strategyName, never to deployment_name/mode/initial_capital — those
  // are per-deployment metadata, not part of what a preset remembers. ──
  _presets: [],

  async _loadPresets(strategyName) {
    const row = document.getElementById('deployPresetRow');
    const select = document.getElementById('deployPresetSelect');
    try {
      this._presets = await Api.listPresets(strategyName);
    } catch (e) {
      this._presets = [];
    }
    select.innerHTML = '<option value="">— none —</option>' +
      this._presets.map(p => `<option value="${p.id}">${escapeHtml(p.preset_name)}</option>`).join('');
    row.style.display = 'block';
    document.getElementById('deployPresetDeleteBtn').style.display = 'none';
  },

  loadPreset() {
    const id = document.getElementById('deployPresetSelect').value;
    document.getElementById('deployPresetDeleteBtn').style.display = id ? 'block' : 'none';
    if (!id) return;
    const preset = this._presets.find(p => p.id === id);
    if (!preset) return;
    // Loading a preset re-renders the SIMPLE form from its config — if
    // Advanced (raw JSON) was open, drop back to the form view so the
    // loaded values are actually visible, not silently sitting in a
    // hidden textarea nobody's looking at.
    document.getElementById('deployAdvancedToggle').checked = false;
    document.getElementById('deployConfigFields').style.removeProperty('display');
    document.getElementById('deployConfig').style.display = 'none';
    this._renderConfigFields(preset.config);
  },

  async saveAsPreset() {
    const name = prompt('Name this preset (for this strategy only):');
    if (!name || !name.trim()) return;
    const config = this._readConfigFromFields();
    const { ok, data } = await Api.createPreset(this._currentDeploy.strategyName, name.trim(), config);
    if (!ok) {
      alert(data.detail || 'Could not save preset');
      return;
    }
    await this._loadPresets(this._currentDeploy.strategyName);
    document.getElementById('deployPresetSelect').value = data.id;
    document.getElementById('deployPresetDeleteBtn').style.display = 'block';
  },

  async deleteSelectedPreset() {
    const select = document.getElementById('deployPresetSelect');
    const id = select.value;
    if (!id) return;
    const preset = this._presets.find(p => p.id === id);
    if (!confirm(`Delete the preset "${preset ? preset.preset_name : id}"? This can't be undone.`)) return;
    const { ok, data } = await Api.deletePreset(this._currentDeploy.strategyName, id);
    if (!ok) {
      alert(data.detail || 'Could not delete preset');
      return;
    }
    await this._loadPresets(this._currentDeploy.strategyName);
  },

  // ── Structured-form <-> raw-JSON config editing ──────────────────
  // One <div class="field"> per config key, widget chosen from the
  // key's own value (boolean -> dropdown, array -> comma-separated
  // token list, known enum strings -> dropdown, "HH:MM"-shaped
  // strings -> a time picker, everything else -> a plain box) — built
  // straight from the strategy's own registered default_config, so
  // this never drifts out of sync with what a strategy actually
  // accepts the way a hand-maintained parallel schema could.

  _renderConfigFields(config) {
    this._configBase = { ...config };
    document.getElementById('deployConfigFields').innerHTML = configFieldsContainerHtml(config, 'cfgField_');
  },

  // Reads the CURRENT form back into a config object. Starts from
  // _configBase (not an empty {}) so advanced/null-valued keys the
  // simple form never showed (capital_per_trade, seed_candles, ...)
  // still round-trip into the deploy payload untouched, rather than
  // silently disappearing because the box editor never displayed them.
  _readConfigFromFields() {
    return readConfigFromFields('deployConfigFields', this._configBase);
  },

  // The toggle itself: a genuine mode switch, not a read-only mirror —
  // switching TO Advanced seeds the JSON textarea from whatever's
  // CURRENTLY in the boxes (not the original defaults), so nothing
  // typed gets lost; switching back parses the JSON and re-renders the
  // boxes from it, best-effort, staying on the JSON view if it doesn't
  // parse rather than silently discarding an in-progress edit.
  toggleAdvanced() {
    const on = document.getElementById('deployAdvancedToggle').checked;
    const fieldsEl = document.getElementById('deployConfigFields');
    const jsonEl = document.getElementById('deployConfig');
    if (on) {
      const config = Catalog._readConfigFromFields();
      jsonEl.value = JSON.stringify(config, null, 2);
      fieldsEl.style.display = 'none';
      jsonEl.style.display = 'block';
    } else {
      let parsed;
      try {
        parsed = JSON.parse(jsonEl.value || '{}');
      } catch (e) {
        document.getElementById('deployAdvancedToggle').checked = true;
        alert(`Invalid JSON — fix it first, or it can't be converted back to fields:\n${e.message}`);
        return;
      }
      Catalog._renderConfigFields(parsed);
      fieldsEl.style.removeProperty('display');   // see openDeployModal's own comment on why not '= block'
      jsonEl.style.display = 'none';
    }
  },
};

function closeDeployModal() {
  document.getElementById('deployModal').classList.remove('open');
}

async function submitDeploy() {
  const msg = document.getElementById('deployMsg');
  const advancedOn = document.getElementById('deployAdvancedToggle').checked;
  let config;
  if (advancedOn) {
    try {
      config = JSON.parse(document.getElementById('deployConfig').value || '{}');
    } catch (e) {
      msg.innerHTML = `<span style="color:var(--loss)">Invalid config JSON: ${e.message}</span>`;
      return;
    }
  } else {
    config = Catalog._readConfigFromFields();
  }
  const body = {
    deployment_name: document.getElementById('deployName').value.trim(),
    strategy_name: Catalog._currentDeploy.strategyName,
    mode: document.getElementById('deployMode').value,
    initial_capital: Number(document.getElementById('deployCapital').value),
    config,
    notes: document.getElementById('deployNotes').value,
  };
  if (!body.deployment_name) {
    msg.innerHTML = '<span style="color:var(--loss)">Deployment name is required</span>';
    return;
  }
  msg.innerHTML = '<span class="spinner"></span> Deploying…';
  const { ok, data } = await Api.createDeployment(body);
  if (!ok) {
    msg.innerHTML = `<span style="color:var(--loss)">${data.detail || 'Failed'}</span>`;
    return;
  }
  msg.innerHTML = `<span style="color:var(--gain)">✓ Deployed "${escapeHtml(data.deployment_name)}"</span>`;
  setTimeout(() => { closeDeployModal(); Catalog.load(); }, 800);
}

// ── Clear All Deployments — destructive, irreversible. Gated behind
// re-entering the app password AND typing the confirmation phrase,
// both checked server-side (see app/routers/deployments.py's
// clear_all_deployments) — this client-side check is a fast fail for
// an obviously-wrong attempt, not the actual security boundary. ──────
function closeClearAllModal() {
  document.getElementById('clearAllModal').classList.remove('open');
}

async function submitClearAll() {
  const msg = document.getElementById('clearAllMsg');
  const password = document.getElementById('clearAllPassword').value;
  const confirmText = document.getElementById('clearAllConfirm').value.trim();
  if (!password) {
    msg.innerHTML = '<span style="color:var(--loss)">Password is required</span>';
    return;
  }
  if (confirmText !== 'DELETE ALL') {
    msg.innerHTML = '<span style="color:var(--loss)">Type DELETE ALL exactly to confirm</span>';
    return;
  }
  msg.innerHTML = '<span class="spinner"></span> Clearing…';
  const { ok, data } = await Api.clearAllDeployments(password, confirmText);
  if (!ok) {
    msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(data.detail || 'Failed')}</span>`;
    return;
  }
  msg.innerHTML = `<span style="color:var(--gain)">✓ Cleared ${data.deleted} deployment(s)</span>`;
  // Now triggered from Account -> Admin (Step 69), not Catalog -- also
  // refresh Account itself so its active-deployment counts (shown next
  // to each strategy row) drop to zero immediately, same "don't wait
  // for the next reload" reasoning as the other two refreshes here.
  setTimeout(() => { closeClearAllModal(); Catalog.load(); Deployments.load(); Account.load(); }, 900);
}
