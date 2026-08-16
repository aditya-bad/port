// live_deploy — Strategy Catalog view: every strategy registered in
// app/strategies/, with a count of how many currently-active
// deployments are running it (a cross-reference that only matters once
// several instances of the same strategy exist with different
// configs), plus the Deploy modal.

// String-valued config keys with a real, fixed set of valid values —
// verified directly against each strategy's own source (pivot_type/
// atr_smoothing's docstrings in pivot_supertrend.py, expiry_selector's
// accepted selectors in options/resolver.py's _resolve_expiry, and
// convergence_mode's own `if ... not in (...)` validation in
// strangle_monthly_v2.py) — not guessed. Any OTHER string field (symbol,
// options_underlying, instrument, or a key none of today's strategies
// use yet) falls back to a plain text box instead of a dropdown.
const CONFIG_ENUM_OPTIONS = {
  pivot_type: ['classic', 'fibonacci', 'camarilla', 'woodie'],
  atr_smoothing: ['wilder', 'sma', 'ema'],
  expiry_selector: ['THIS_WEEK', 'NEXT_WEEK', 'THIS_MONTH', 'NEXT_MONTH'],
  convergence_mode: ['fixed_stop', 'trailing_stop', 'active_management'],
};
const CONFIG_TIME_FIELD_RE = /^\d{2}:\d{2}$/;

const Catalog = {
  _currentDeploy: {},
  _configBase: {},   // the full config object (including null-valued advanced keys), kept in sync as the source of truth across form <-> JSON toggles
  _strategies: [],   // every REGISTERED strategy (enabled or not) — the Admin tab's own source of truth
  _activeCounts: {},
  _tab: 'browse',    // 'browse' | 'admin'

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

    this._render();
    markUpdated('catalogUpdatedLabel');
  },

  switchTab(tab) {
    this._tab = tab;
    document.querySelectorAll('#catalogTabs button').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    this._render();
  },

  _render() {
    if (this._tab === 'admin') this._renderAdmin();
    else this._renderBrowse();
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
  // action from this. ─────────────────────────────────────────────────
  _renderAdmin() {
    const el = document.getElementById('catalogList');
    if (!this._strategies.length) {
      el.innerHTML = emptyHtml('No strategies registered yet.');
      return;
    }
    // Cards, not a table — a strategy's description is long free text
    // (strangle_monthly_v2's runs 400+ characters), which a dense
    // multi-column table fights: a description column narrow enough to
    // leave room for Status/Action ends up wrapping one word per line.
    // Browse already solves exactly this with .card, so Admin reuses
    // the identical treatment instead of a second, worse solution.
    el.innerHTML = this._strategies.map(s => {
      const enabled = s.enabled !== false;
      const count = this._activeCounts[s.name] || 0;
      return `
        <div class="card">
          <div class="card-row">
            <div>
              <div class="card-title">
                ${escapeHtml(s.name)}
                <span class="tag ${enabled ? 'tag-active' : 'tag-stopped'}">${enabled ? 'enabled' : 'disabled'}</span>
                ${count > 0 ? `<span class="tag tag-active">${count} active</span>` : ''}
              </div>
              <div class="card-sub">${escapeHtml(s.description || 'no description')}</div>
            </div>
            <div class="card-actions">
              <button class="btn btn-sm ${enabled ? 'btn-danger' : 'btn-primary'}"
                onclick="Catalog.toggleStrategyEnabled('${escapeHtml(s.name)}', ${!enabled})">
                ${enabled ? 'Disable' : 'Enable'}
              </button>
            </div>
          </div>
        </div>
      `;
    }).join('') +
      `<div class="table-note">Disabling a strategy only hides it from Browse and blocks NEW deployments — anything already deployed keeps running untouched.</div>` +
      `<div class="card" style="border-top-color:var(--loss); margin-top:22px; max-width:520px;">
        <div class="card-row">
          <div>
            <div class="card-title" style="color:var(--loss);">Danger zone</div>
            <div class="card-sub">Permanently delete every deployment and everything under it. Cannot be undone.</div>
          </div>
          <div class="card-actions">
            <button class="btn btn-danger btn-sm" onclick="Catalog.openClearAllModal()">Clear all deployments</button>
          </div>
        </div>
      </div>`;
  },

  async openClearAllModal() {
    const deployments = await Api.listDeployments();
    const count = deployments.length;
    document.getElementById('clearAllCount').textContent =
      count === 0 ? 'zero deployments (nothing to clear)' : `all ${count} deployment${count === 1 ? '' : 's'}`;
    document.getElementById('clearAllPassword').value = '';
    document.getElementById('clearAllConfirm').value = '';
    document.getElementById('clearAllMsg').textContent = '';
    document.getElementById('clearAllModal').classList.add('open');
  },

  async toggleStrategyEnabled(name, newEnabled) {
    const { ok, data } = await Api.setStrategyEnabled(name, newEnabled);
    if (!ok) { alert(data.detail || 'Could not update strategy'); return; }
    const s = this._strategies.find(x => x.name === name);
    if (s) s.enabled = newEnabled;
    this._render();
  },

  openDeployModal(strategyName, defaultConfig) {
    this._currentDeploy = { strategyName, defaultConfig };
    document.getElementById('deployModalTitle').textContent = `Deploy: ${strategyName}`;
    document.getElementById('deployName').value = '';
    document.getElementById('deployCapital').value = '100000';
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
    const container = document.getElementById('deployConfigFields');
    const simpleKeys = Object.keys(config).filter(k => config[k] !== null && config[k] !== undefined);
    const advancedOnlyKeys = Object.keys(config).filter(k => config[k] === null || config[k] === undefined);

    if (!simpleKeys.length && !advancedOnlyKeys.length) {
      container.innerHTML = emptyHtml('This strategy has no default config fields — use Advanced to add any.');
      return;
    }

    container.innerHTML = simpleKeys.map(k => Catalog._configFieldHtml(k, config[k])).join('')
      + (advancedOnlyKeys.length
          ? `<div class="table-note">${advancedOnlyKeys.map(k => `<code>${escapeHtml(k)}</code>`).join(', ')} ` +
            `left at ${advancedOnlyKeys.length > 1 ? 'their' : 'its'} default (null) — switch to Advanced to set ` +
            `${advancedOnlyKeys.length > 1 ? 'them' : 'it'}.</div>`
          : '');
  },

  _configFieldHtml(key, value) {
    const label = escapeHtml(key.replace(/_/g, ' '));
    const id = `cfgField_${key}`;

    if (CONFIG_ENUM_OPTIONS[key]) {
      const opts = CONFIG_ENUM_OPTIONS[key]
        .map(o => `<option value="${o}" ${o === value ? 'selected' : ''}>${o}</option>`).join('');
      return `<div class="field"><label for="${id}">${label}</label>` +
        `<select id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="string">${opts}</select></div>`;
    }
    if (typeof value === 'boolean') {
      return `<div class="field"><label for="${id}">${label}</label>` +
        `<select id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="boolean">` +
        `<option value="true" ${value === true ? 'selected' : ''}>true</option>` +
        `<option value="false" ${value === false ? 'selected' : ''}>false</option>` +
        `</select></div>`;
    }
    if (Array.isArray(value)) {
      // instrument_tokens is the one array-shaped field across every
      // strategy's default_config today — a comma-separated list of
      // numbers in, a real array of numbers back out on submit.
      return `<div class="field"><label for="${id}">${label} (comma-separated)</label>` +
        `<input type="text" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="number-array" ` +
        `value="${escapeHtml(value.join(', '))}" placeholder="256265, 260105"></div>`;
    }
    if (typeof value === 'number') {
      const step = Number.isInteger(value) ? '1' : 'any';
      return `<div class="field"><label for="${id}">${label}</label>` +
        `<input type="number" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="number" step="${step}" value="${value}"></div>`;
    }
    if (typeof value === 'string' && CONFIG_TIME_FIELD_RE.test(value)) {
      return `<div class="field"><label for="${id}">${label} (HH:MM)</label>` +
        `<input type="time" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="string" value="${value}"></div>`;
    }
    // Plain string fallback — symbol, options_underlying, instrument,
    // or any key none of today's strategies use yet.
    return `<div class="field"><label for="${id}">${label}</label>` +
      `<input type="text" id="${id}" data-cfg-key="${escapeHtml(key)}" data-cfg-type="string" value="${escapeHtml(String(value))}"></div>`;
  },

  // Reads the CURRENT form back into a config object. Starts from
  // _configBase (not an empty {}) so advanced/null-valued keys the
  // simple form never showed (capital_per_trade, seed_candles, ...)
  // still round-trip into the deploy payload untouched, rather than
  // silently disappearing because the box editor never displayed them.
  _readConfigFromFields() {
    const config = { ...this._configBase };
    document.querySelectorAll('#deployConfigFields [data-cfg-key]').forEach(el => {
      const key = el.dataset.cfgKey;
      const type = el.dataset.cfgType;
      const raw = el.value;
      if (type === 'boolean') config[key] = raw === 'true';
      else if (type === 'number') config[key] = raw === '' ? null : Number(raw);
      else if (type === 'number-array') {
        config[key] = raw.split(',').map(s => s.trim()).filter(s => s !== '').map(Number);
      } else config[key] = raw;
    });
    return config;
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
  setTimeout(() => { closeClearAllModal(); Catalog.load(); Deployments.load(); }, 900);
}
