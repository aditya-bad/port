import './setup.mjs';
for (const m of ['core/format.js','core/basis.js','core/router.js','core/store.js','data/metrics.js','actions.js','data/book.js','data/alerts.js','core/prefs.js']) {
  try { await import(`../../static/linck/js/${m}`); console.log('ok', m); } catch (e) { console.log('FAIL', m, e.message); }
}
