# Reference material

Copies, so this folder stands on its own. The originals live in
`live_deploy/docs/`; if they change, re-copy them here.

| File | What | Written |
|---|---|---|
| `linck-at-scale.html` | First architecture proposal: modular monolith + four services, for thousands of users | 22 Oct, 3dbddbf |
| `linck-platform.html` | Linck as a company: product, business, market rules, final and intermediate architecture, SDK, every failure case, roadmap, risks | 22 Oct, 20b0d38 |
| `linck-architecture.html` | **The technical architecture v2**: components, kept/changed/new, services, protocols, Kubernetes, contracts, tick path, latest-value guarantee, runtime, OMS and ledger, SDK, shipping, end state, migration, decisions | 22 Oct, 119ca8a |
| `linck-brokers-languages-load.html` | Brokers without a Go SDK, many languages on one runner, measured latency and load | 23 Oct, 3fea1a2 |
| `linck-runtime.html` | Strategies in any language: run, walk, recover, latest tick; with the prototype's results | 23 Oct, d10bf29 |
| `linck-how-it-works.html` | **Start here for the platform.** Each promise (latest price, orders once, no two copies, crash, replay, trust, any language, isolation, scale, feed/broker trouble): how it is kept, a worked trade, and the evidence marked running today / prototype / measured / design only, with what isn't proven yet and the test that would prove it | 23 Oct |
| `linck-visual.html` | The same platform as 16 short **narrated** animations (about 15 minutes; voice and captions, or captions only): one picture; why it is split; a price's path; queue vs latest; building code; **every language in one runtime host**; one batch; an order; a crash; the same bug twice; two copies; replay and repair; shadow checks; brokers in any SDK language; scale; laptop to live. ← → move between scenes, space pauses. The script is in `text/linck-visual.txt` | 23 Oct |
| `text/*.txt` | The six text docs, and a scene-by-scene description of the animations, as plain text (diagrams described in brackets), for agents to read or grep cheaply | |
| `design/design-brief.md` | The product requirements the owner gave Claude Design: who the user is, every job the UI must do, hard rules, what "not AI-looking" means | 16 Oct |
| `design/Linck.dc.html`, `design/Linck Design Spec.dc.html`, `design/support.js` | The owner's approved Linck design (prototype and spec) from Claude Design. Open in a browser with internet access: they need `support.js` beside them, which loads React from unpkg, and fonts come from Google Fonts. Mock names in them were replaced with placeholders | 16 Oct |

Later docs supersede earlier ones where they disagree: `linck-at-scale` →
`linck-platform` → `linck-architecture` → `linck-brokers-languages-load` and
`linck-runtime` → `linck-how-it-works` (which pulls the others together from
the user's side). Notably, NATS/Aeron for ticks in the earlier docs were
replaced by conflated gRPC streams, and the "~1 ms per event" sizing was
corrected to a measured 7–47 µs.

The implemented UI follows the design with known changes (overflow fixes,
"things need you" first on phones, no net delta, Close everything moved to
Account). See `../04-features.md`.
