// live_deploy — Account view: this app's own login (unrelated to Kite),
// separate from every other view here because it's the one place that
// manages who can reach this app at all, not what it's doing. Three
// tabs: Profile (who am I + change my own password), Users (create new
// accounts — no RBAC yet, so any logged-in user can do this, see
// app/rbac.py), Audit Log (a read view over every state-changing
// request the whole app has handled, see migration 0005 + app/auth.py's
// AuditLogMiddleware).

const Account = {
  _tab: 'profile',
  _me: null,

  async load() {
    const body = document.getElementById('accountBody');
    body.innerHTML = spinnerHtml();
    try {
      this._me = await Api.me();
    } catch (e) {
      this._me = null;
    }
    this._render();
  },

  switchTab(tab) {
    this._tab = tab;
    document.querySelectorAll('#accountTabs button').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    this._render();
  },

  _render() {
    if (this._tab === 'users') this._renderUsers();
    else if (this._tab === 'audit') this._renderAudit();
    else this._renderProfile();
  },

  // ── Profile: who am I + change my own password ─────────────────
  _renderProfile() {
    const body = document.getElementById('accountBody');
    const via = this._me ? this._me.authenticated_via : null;
    const identity = (via === 'session')
      ? `<div class="stat-card" style="max-width:320px;">
           <div class="stat-label">Logged in as</div>
           <div class="stat-value" style="font-size:20px;">${escapeHtml(this._me.username)}</div>
           <div class="stat-sub">
             <div class="row"><span>Role</span><span>${escapeHtml(this._me.role)}</span></div>
             <div class="row"><span>Created</span><span>${fmtDateTime(this._me.created_at)}</span></div>
             <div class="row"><span>Last login</span><span>${fmtDateTime(this._me.last_login_at)}</span></div>
           </div>
         </div>`
      : `<div class="table-note">Authenticated via X-API-Key — no user account to show here.</div>`;

    const changePwForm = (via === 'session') ? `
      <section style="margin-top:26px; max-width:360px;">
        <h2>Change password</h2>
        <div class="field">
          <label>Current password</label>
          <input type="password" id="acctOldPassword" autocomplete="current-password">
        </div>
        <div class="field">
          <label>New password (min 8 characters)</label>
          <input type="password" id="acctNewPassword" autocomplete="new-password">
        </div>
        <div class="field">
          <label>Confirm new password</label>
          <input type="password" id="acctNewPassword2" autocomplete="new-password">
        </div>
        <button class="btn btn-primary btn-sm" onclick="Account.submitChangePassword()">Change password</button>
        <div class="modal-msg" id="acctChangePwMsg" style="margin-top:10px;"></div>
      </section>
    ` : '';

    body.innerHTML = identity + changePwForm;
  },

  async submitChangePassword() {
    const msg = document.getElementById('acctChangePwMsg');
    const oldPassword = document.getElementById('acctOldPassword').value;
    const newPassword = document.getElementById('acctNewPassword').value;
    const confirm = document.getElementById('acctNewPassword2').value;
    if (newPassword !== confirm) {
      msg.innerHTML = '<span style="color:var(--loss)">New password and confirmation don’t match</span>';
      return;
    }
    msg.textContent = '';
    const { ok, data } = await Api.changePassword(oldPassword, newPassword);
    if (!ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : (data.detail || 'Could not change password');
      msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(detail)}</span>`;
      return;
    }
    document.getElementById('acctOldPassword').value = '';
    document.getElementById('acctNewPassword').value = '';
    document.getElementById('acctNewPassword2').value = '';
    msg.innerHTML = '<span style="color:var(--gain)">✓ Password changed</span>';
  },

  // ── Users: list + create ────────────────────────────────────────
  async _renderUsers() {
    const body = document.getElementById('accountBody');
    body.innerHTML = spinnerHtml();
    let users;
    try {
      users = await Api.listUsers();
    } catch (e) {
      body.innerHTML = emptyHtml(`Could not load users: ${escapeHtml(e.message)}`);
      return;
    }
    body.innerHTML = `
      <section style="max-width:360px;">
        <h2>Create user</h2>
        <div class="field">
          <label>Username</label>
          <input type="text" id="acctNewUsername" autocomplete="off">
        </div>
        <div class="field">
          <label>Password (min 8 characters)</label>
          <input type="password" id="acctNewUserPassword" autocomplete="new-password">
        </div>
        <button class="btn btn-primary btn-sm" onclick="Account.submitCreateUser()">Create user</button>
        <div class="modal-msg" id="acctCreateUserMsg" style="margin-top:10px;"></div>
      </section>

      <section style="margin-top:26px;">
        <h2>All users</h2>
        <div class="table-wrap">
          <table><thead><tr><th>Username</th><th>Role</th><th>Status</th><th>Created</th><th>Last login</th></tr></thead>
          <tbody>${users.map(u => `
            <tr>
              <td>${escapeHtml(u.username)}</td>
              <td>${escapeHtml(u.role)}</td>
              <td><span class="tag ${u.is_active ? 'tag-active' : 'tag-paused'}">${u.is_active ? 'active' : 'disabled'}</span></td>
              <td>${fmtDateTime(u.created_at)}</td>
              <td>${fmtDateTime(u.last_login_at)}</td>
            </tr>
          `).join('')}</tbody></table>
        </div>
      </section>
    `;
  },

  async submitCreateUser() {
    const msg = document.getElementById('acctCreateUserMsg');
    const username = document.getElementById('acctNewUsername').value.trim();
    const password = document.getElementById('acctNewUserPassword').value;
    msg.textContent = '';
    const { ok, data } = await Api.createUser(username, password);
    if (!ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : (data.detail || 'Could not create user');
      msg.innerHTML = `<span style="color:var(--loss)">${escapeHtml(detail)}</span>`;
      return;
    }
    // _renderUsers() rebuilds #accountBody from scratch (including a
    // fresh, empty #acctCreateUserMsg) to pick up the new row in the
    // table below — so the success message has to be set AFTER that
    // rebuild finishes, not before, or it gets wiped out the instant
    // _renderUsers() clears the body to show its own loading spinner.
    await this._renderUsers();
    document.getElementById('acctCreateUserMsg').innerHTML =
      `<span style="color:var(--gain)">✓ User '${escapeHtml(data.username)}' created</span>`;
  },

  // ── Audit log: read-only, most recent first ─────────────────────
  _openAuditRows: new Set(),

  async _renderAudit() {
    const body = document.getElementById('accountBody');
    body.innerHTML = spinnerHtml();
    let rows;
    try {
      rows = await Api.getAuditLog(200);
    } catch (e) {
      body.innerHTML = emptyHtml(`Could not load audit log: ${escapeHtml(e.message)}`);
      return;
    }
    this._auditRows = rows;
    this._openAuditRows = new Set();
    if (!rows.length) {
      body.innerHTML = emptyHtml('No state-changing requests recorded yet.');
      return;
    }
    body.innerHTML = `
      <div class="table-note" style="margin-bottom:10px;">
        Every POST/PUT/PATCH/DELETE request the app has handled — passwords and secrets are
        redacted before being stored. Click a row for the full (redacted) request body.
      </div>
      <div class="table-wrap">
        <table><thead><tr><th>Time</th><th>User</th><th>Method</th><th>Path</th><th>Status</th><th>From</th></tr></thead>
        <tbody>${rows.map((r, i) => this._auditRowHtml(r, i)).join('')}</tbody></table>
      </div>
    `;
  },

  _statusTagClass(status) {
    if (status == null) return 'tag-warn';
    if (status >= 200 && status < 300) return 'tag-active';
    if (status === 401 || status === 403) return 'tag-error';
    if (status >= 400) return 'tag-paused';
    return 'tag-info';
  },

  _auditRowHtml(row, i) {
    const open = this._openAuditRows.has(i);
    const hasBody = row.request_body && Object.keys(row.request_body).length > 0;
    return `
      <tr class="trade-row ${open ? 'open' : ''}" ${hasBody ? `onclick="Account.toggleAuditRow(${i})"` : ''}>
        <td>${fmtDateTime(row.occurred_at)}</td>
        <td>${escapeHtml(row.username || '—')}</td>
        <td>${escapeHtml(row.method)}</td>
        <td>${escapeHtml(row.path)}</td>
        <td><span class="tag ${this._statusTagClass(row.status_code)}">${row.status_code ?? '—'}</span></td>
        <td>${escapeHtml(row.remote_addr || '—')}</td>
      </tr>
      ${hasBody ? `<tr class="trade-detail-row" id="audit-detail-${i}" style="display:${open ? 'table-row' : 'none'}">
        <td colspan="6">${renderJsonBlock('request_body', row.request_body)}</td>
      </tr>` : ''}
    `;
  },

  toggleAuditRow(i) {
    const row = document.getElementById(`audit-detail-${i}`);
    const isOpen = this._openAuditRows.has(i);
    if (isOpen) { this._openAuditRows.delete(i); row.style.display = 'none'; }
    else { this._openAuditRows.add(i); row.style.display = 'table-row'; }
  },
};
