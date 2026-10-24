// The Linck architecture, as a narrated explainer.
// Parts: LinckTrade blue, LinckLens purple, Linck Platform orange.
// Language badges: Rust red, Go green, Python yellow.

// The final architecture on one screen: every service and store, named.
const F_PLATFORM = [['edge-gateway', 'Envoy + Go'], ['identity', 'Go'], ['commerce', 'Go'], ['notifications', 'Go']];
const F_LENS = [['product-api', 'Python'], ['live-gateway', 'Go'], ['read-models', 'Go'], ['assistant', 'Python']];
const F_REGION = [['control', 'Go'], ['builder', 'Go'], ['refdata', 'Go'], ['integrity', 'Go'], ['backtest-farm', 'Rust']];
const F_CELL = [['md-ingest', 'Rust'], ['md-sequencer', 'Rust'], ['computed-streams', 'Rust'], ['cache-agent', 'Rust'],
  ['supervisor', 'Rust', 'Iceberg'], ['oms', 'Go', 'Heimdall'], ['broker-gw', 'Go', 'Kraken'], ['sim-venue', 'Rust']];
const F_STORES = [['Postgres', '7 databases'], ['ClickHouse', 'charts, reports'], ['S3 lake', 'Iceberg tables'], ['Redis', 'sessions, limits'],
  ['etcd', 'leases, epochs'], ['Redpanda', 'money events'], ['NATS', 'commands, live'], ['Vault + KMS', 'tokens, keys'], ['Temporal', 'long jobs']];
// where each one sits: [x, y, w]
function finalSpots() {
  const s = {};
  F_PLATFORM.forEach(([n], i) => (s[n] = [56 + i * 177, 228, 169]));
  F_LENS.forEach(([n], i) => (s[n] = [816 + i * 184, 228, 176]));
  F_REGION.forEach(([n], i) => (s[n] = [56 + i * 300, 364, 288]));
  F_CELL.forEach(([n], i) => (s[n] = [72 + (i % 4) * 367, i < 4 ? 492 : 576, 355]));
  return s;
}
function finalMap(p, o = {}) {
  const L = G(p), B = G(p);
  const m = { L, B, nodes: {}, stores: {} };
  m.you = named('you', box(B, 40, 112, 470, 60, 'You: web app · phone · CLI · SDK', { cls: 'out', lcls: 'cl' }));
  m.exch = named('exchanges', box(B, 860, 112, 330, 60, 'Exchanges, via a vendor', { cls: 'out', lcls: 'cl' }));
  m.brokers = named('brokers', box(B, 1220, 112, 340, 60, 'Brokers: Kite, Upstox…', { cls: 'out', lcls: 'cl' }));
  m.platform = named('platform', area(B, 40, 190, 740, 116, 'Linck Platform', 'platform'));
  m.lens = named('lens', area(B, 800, 190, 760, 116, 'LinckLens', 'lens'));
  m.trade = named('trade', area(B, 40, 322, 1520, 382, 'LinckTrade', 'trade'));
  m.cell = named('cell', area(B, 56, 446, 1488, 246, 'One cell', 'cell', 'one per group of accounts: its own cluster and ledger'));
  const sp = finalSpots();
  const add = ([n, lang, nick], part) => (m.nodes[n] = named(n, node(B, ...sp[n], n, lang, part, { nick })));
  F_PLATFORM.forEach(r => add(r, 'platform'));
  F_LENS.forEach(r => add(r, 'lens'));
  F_REGION.forEach(r => add(r, 'trade'));
  F_CELL.forEach(r => add(r, 'trade'));
  m.cellNote = T(B, 76, 672, 'also in each cell: its own etcd (leases), Redpanda (3 brokers) and a Postgres ledger shard', 'bs', 'start'); op(m.cellNote, 0);
  m.region = named('region', bounds(G(B), 56, 364, 1488, 64));
  m.cellnodes = named('cellnodes', bounds(G(B), 72, 492, 1456, 148));
  m.storeLab = T(B, 40, 732, 'Stores: each holds one kind of data; only its owner connects', 'fl', 'start'); op(m.storeLab, 0);
  F_STORES.forEach(([n, sub], i) => (m.stores[n] = named(n, db(B, 40 + i * 170, 746, 160, 100, n, sub))));
  m.storesAll = named('stores', bounds(G(B), 40, 712, 1520, 134));
  m.outside = named('outside', bounds(G(B), 40, 112, 1520, 60));
  m.all = [m.you, m.exch, m.brokers, m.platform, m.lens, m.trade, m.cell, ...Object.values(m.nodes), m.cellNote, m.storeLab, ...Object.values(m.stores)];
  return m;
}
