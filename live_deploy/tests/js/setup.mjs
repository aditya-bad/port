// Minimal browser globals so the Linck modules import under Node.
const store = new Map();
globalThis.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
  clear: () => store.clear(),
};
globalThis.location = { hash: '', pathname: '/linck/', search: '' };
globalThis.history = { replaceState() {} };
globalThis.window = globalThis;
globalThis.HashChangeEvent = class extends Event {};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
