# Linck: the handoff folder

This folder says everything an agent or developer needs to carry on building
Linck without the chat it came from. It covers what the product is, why each
part exists, how it is built today, the target architecture, every decision
with its reasons, what is open, and how the owner likes to work.

Written 23 Oct 2026, at branch `claude/nifty-shop-port-setup-xkdq22`,
commit `d10bf29` and earlier. When the code and this folder disagree, the code
wins; then fix this folder.

## Read in this order

| File | What it answers |
|---|---|
| [01-product.md](01-product.md) | What Linck is, who uses it, the one question it serves, the vision |
| [02-current-system.md](02-current-system.md) | How today's app is built: process, modules, data, UI, tests |
| [03-strategies-and-market.md](03-strategies-and-market.md) | Indian F&O facts the code depends on, and each strategy's rules |
| [04-features.md](04-features.md) | Every user-facing feature, how it behaves and why |
| [reference/linck-how-it-works.html](reference/linck-how-it-works.html) | The platform explained from its promises, with proof and what isn't proven yet (plain text in `reference/text/`) |
| [reference/linck-visual.html](reference/linck-visual.html) | The same, as 16 short animations: open in a browser, no reading needed |
| [05-target-architecture.md](05-target-architecture.md) | The final and intermediate architectures, the migration, and **deploy anywhere**: each part on any cloud or own servers, mixed |
| [reference/explainers/linck-architecture/](reference/explainers/linck-architecture/) | The same as a narrated video (about 50 min, 32 scenes): every service with its components, language and job, every flow, the final folding into the first version, and the migration. `make.sh` builds it; the script is in `reference/text/linck-architecture-video.txt`. Its “3–5 thousand strategies a machine” is a placeholder from an assumed ~1 ms per event: the SDK video's last scene explains how to read it |
| [reference/explainers/running-linck/](reference/explainers/running-linck/) | Running Linck, as a narrated video (about 20 min, 16 scenes): today's one-machine deploy, a release gate by gate and where each thing ships, changing it without stopping trading, a strategy's conversation with the supervisor, Request/Reply, the walk, every failure, a trading day, watching it, recovery and teams, adding brokers. Script in `reference/text/running-linck-video.txt` |
| [reference/explainers/linck-sdk/](reference/explainers/linck-sdk/) | The SDK for developers, as a narrated video (about 15 min, 15 scenes): the one idea, a strategy in Python and Go, ctx, the manifest, what the SDK does, determinism, laptop to live, each language measured, WASM and remote, indicators, the conformance suite, and how many strategies fit on a machine. Script in `reference/text/linck-sdk-video.txt` |
| [reference/linck-first-version.png](reference/linck-first-version.png) | The first version (intermediate architecture) as one picture: the nine deployables, stores, outside systems and every flow, labelled (SVG beside it) |
| [reference/explainers/linck-first-version/](reference/explainers/linck-first-version/) | The first version alone, as a narrated video (about 17 min, 11 scenes): what it is and why first, words, the nine deployables, the stores (self-run or managed), the flows, a live price from Kite to a strategy (one gRPC stream per runner worker; strategies never connect to market-data), different strategies following different instruments (slots only for what is asked for), where each part can run (one home site on any cloud or own servers; the rest anywhere), installing a site and the site check, and the steps P0, I0–I5. Script in `reference/text/linck-first-version-video.txt`. Made after the deploy-anywhere correction; the three videos above still show AWS and are due for the same fix |
| [06-runtime-and-languages.md](06-runtime-and-languages.md) | Running strategies in any language: protocol, walk, crash, latest tick |
| [07-brokers-latency-load.md](07-brokers-latency-load.md) | Brokers without a Go SDK, measured latency, capacity |
| [08-decisions.md](08-decisions.md) | Every decision, with the alternatives turned down and why |
| [09-open-items.md](09-open-items.md) | What's next, what's unverified, known flaky tests |
| [10-working-agreement.md](10-working-agreement.md) | **The owner's rules. Read before touching anything.** |
| [11-history.md](11-history.md) | What was built, in order, Aug to Oct 2026 |
| [12-glossary.md](12-glossary.md) | Terms used everywhere |
| [13-runbook.md](13-runbook.md) | Commands: gate, deploy, scripts, Kite login, migrations |
| [reference/](reference/) | The five architecture docs (HTML and plain text), the design brief, the Linck design spec |

## Linck in ten lines

1. Linck is a self-hosted web app that paper-trades Indian index option
   strategies (NIFTY, BANKNIFTY, SENSEX, BANKEX) on live Zerodha Kite data.
2. One owner runs 10 to 25 deployments at once, many near-identical, to find
   out which strategies deserve real money.
3. Everything built serves two questions: **are the numbers true?** and
   **which strategy is better?**
4. Today it is one Python process (FastAPI + asyncpg + Postgres) in one
   Docker container, reached over Tailscale. The UI is "Linck", vanilla ES
   modules under `static/linck/`; the old "classic" UI remains as a fallback.
5. All trades are paper. Nothing places real orders yet.
6. The trust tools are the core: charges matched to Zerodha, slippage from the
   order book, shadow mode (would Kite have taken this order?), the day check,
   repair, replay, decision logs, self-reporting strategies.
7. P&L is always **cycle** P&L: for intraday the cycle is the day; for
   positional it is entry, through adjustments, to exit.
8. The long-term plan is a platform company: users write strategies with an
   SDK in any language and Linck runs them, through backtest, paper, shadow and
   live, on one engine.
9. Linck Platform is LinckTrade, LinckLens and the Linck SDK; each part can
   be deployed on any cloud or the user's own servers, mixed. No provider is
   chosen. The target architecture is designed and documented; only step P0 of the
   migration is next, and not built yet.
10. The owner deploys. An agent commits and pushes to the branch, never deploys.

## Starting a new session with this folder

Give the agent this folder and say something like:

> Read `live_deploy/linck-context/README.md` and the files it lists, in
> order, before doing anything. Follow `10-working-agreement.md` exactly.
> Then tell me in a few lines what you understand Linck to be and what is
> open, and wait for my task.
