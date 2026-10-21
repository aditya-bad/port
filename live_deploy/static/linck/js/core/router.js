// Hash router. Routes are data: add a page by adding a line here and a
// module in js/pages/ exporting { title, render(main, params) → cleanup? }.

export const ROUTES = [
  { path: 'desk', nav: 'desk', load: () => import('../pages/desk.js') },
  { path: 'deployments', nav: 'deployments', load: () => import('../pages/deployments.js') },
  { path: 'd/:id/geek/:view?', nav: 'deployments', geek: true, load: () => import('../pages/geek/index.js') },
  { path: 'd/:id/:tab?', nav: 'deployments', load: () => import('../pages/deployment/index.js') },
  { path: 'leaderboard', nav: 'leaderboard', load: () => import('../pages/leaderboard.js') },
  { path: 'compare', nav: 'compare', load: () => import('../pages/compare.js') },
  { path: 'reports', nav: 'reports', load: () => import('../pages/reports.js') },
  { path: 'strategies', nav: 'strategies', load: () => import('../pages/strategies.js') },
  { path: 's/:name', nav: 'strategies', load: () => import('../pages/strategy.js') },
  { path: 'tools/:tab?', nav: 'tools', load: () => import('../pages/tools.js') },
  { path: 'journal', nav: 'journal', load: () => import('../pages/journal.js') },
  { path: 'account', nav: 'account', load: () => import('../pages/account.js') },
];
export const DEFAULT = 'desk';

function compile(path) {
  const keys = [];
  const re = path.split('/').map(seg => {
    if (!seg.startsWith(':')) return seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const opt = seg.endsWith('?');
    keys.push(seg.slice(1).replace('?', ''));
    return opt ? '?([^/]*)' : '([^/]+)';
  }).join('/').replace(/\/\?\(/g, '/?(');
  return { re: new RegExp(`^${re}/?$`), keys };
}
const compiled = ROUTES.map(r => ({ ...r, ...compile(r.path) }));

export function parse(hash = location.hash) {
  const [p, q = ''] = hash.replace(/^#\/?/, '').split('?');
  const query = Object.fromEntries(new URLSearchParams(q));
  for (const r of compiled) {
    const m = (p || DEFAULT).match(r.re);
    if (m) {
      const params = { ...query };
      r.keys.forEach((k, i) => { if (m[i + 1]) params[k] = decodeURIComponent(m[i + 1]); });
      return { route: r, params };
    }
  }
  return { route: compiled.find(r => r.path === DEFAULT), params: query };
}
export function go(path, { replace = false } = {}) {
  const h = `#/${path.replace(/^#?\/?/, '')}`;
  if (replace) history.replaceState(null, '', h);
  else if (location.hash === h) window.dispatchEvent(new HashChangeEvent('hashchange'));
  else location.hash = h;
  if (replace) window.dispatchEvent(new HashChangeEvent('hashchange'));
}
export const href = path => `#/${path}`;
