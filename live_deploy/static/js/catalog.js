// live_deploy — Strategy Catalog view: every strategy registered in
// app/strategies/, with a count of how many currently-active
// deployments are running it (a cross-reference that only matters once
// several instances of the same strategy exist with different
// configs), plus the Deploy modal.

const Catalog = {
  _currentDeploy: {},

  async load() {
    const el = document.getElementById('catalogList');
    el.innerHTML = spinnerHtml();

    const [strategies, deployments] = await Promise.all([
      Api.listStrategies(),
      Api.listDeployments(),
    ]);

    if (!strategies.length) {
      el.innerHTML = emptyHtml('No strategies registered yet — nothing to deploy until one is added to app/strategies/.');
      return;
    }

    // Active-deployment count per strategy_name, derived from the same
    // /deployments list the Deployed Strategies view uses — no
    // separate backend endpoint needed for this, the data's already
    // there.
    const activeCounts = {};
    deployments.forEach(d => {
      if (d.status === 'active') activeCounts[d.strategy_name] = (activeCounts[d.strategy_name] || 0) + 1;
    });

    el.innerHTML = strategies.map(s => {
      const count = activeCounts[s.name] || 0;
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

  openDeployModal(strategyName, defaultConfig) {
    this._currentDeploy = { strategyName, defaultConfig };
    document.getElementById('deployModalTitle').textContent = `Deploy: ${strategyName}`;
    document.getElementById('deployName').value = '';
    document.getElementById('deployCapital').value = '100000';
    document.getElementById('deployConfig').value = JSON.stringify(defaultConfig || {}, null, 2);
    document.getElementById('deployMsg').textContent = '';
    document.getElementById('deployModal').classList.add('open');
  },
};

function closeDeployModal() {
  document.getElementById('deployModal').classList.remove('open');
}

async function submitDeploy() {
  const msg = document.getElementById('deployMsg');
  let config;
  try {
    config = JSON.parse(document.getElementById('deployConfig').value || '{}');
  } catch (e) {
    msg.innerHTML = `<span style="color:var(--loss)">Invalid config JSON: ${e.message}</span>`;
    return;
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
