// Shared setup for the browser specs: the seeded ids, the base URL, a
// browser, and a signed-in context.
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

// run.sh gives each server its own ids file (E2E_IDS); by hand it's ./.ids.json.
const meta = JSON.parse(readFileSync(process.env.E2E_IDS || new URL('./.ids.json', import.meta.url), 'utf8'));
export const IDS = meta.ids;
export const PASSWORD = meta.password;
export const BASE = `http://127.0.0.1:${meta.port}`;

export async function browser() {
  const opts = process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {};
  return chromium.launch(opts);
}

// A context with its own page-error log; signedIn logs in through the API.
// blockSW: once the app's service worker (static/sw.js, a pass-through
// for push) controls the page, its requests no longer reach ctx.route —
// block it in a test that mocks a response across reloads.
export async function context(b, { width = 1440, height = 900, signedIn = true, blockSW = false } = {}) {
  const ctx = await b.newContext({ viewport: { width, height }, ...(blockSW ? { serviceWorkers: 'block' } : {}) });
  ctx.errors = [];
  ctx.on('page', p => p.on('pageerror', e => ctx.errors.push(`${p.url()}: ${e.message}`)));
  if (signedIn) {
    // one retry: under the parallel shards a kept-alive connection is now
    // and then reset (ECONNRESET) before the server sees the request
    const login = () => ctx.request.post(`${BASE}/auth/login`, { data: { username: 'admin', password: PASSWORD } });
    const r = await login().catch(e => { if (/ECONNRESET|socket hang up/.test(String(e))) return login(); throw e; });
    if (!r.ok()) throw new Error(`sign-in failed: ${r.status()}`);
  }
  return ctx;
}

export const api = async (ctx, path) => (await ctx.request.get(BASE + path)).json();
export const dep = async (ctx, name) => (await api(ctx, '/deployments')).find(d => d.deployment_name === name);

// Waits for a condition the server reports, up to ms. Generous: the gate
// runs the Python tests at the same time, so the server can be slow.
export async function until(fn, ms = 15000) {
  const end = Date.now() + ms;
  let v;
  while (Date.now() < end) {
    v = await fn();
    if (v) return v;
    await new Promise(r => setTimeout(r, 150));
  }
  return v;
}
