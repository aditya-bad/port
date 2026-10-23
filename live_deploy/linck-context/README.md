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
| [05-target-architecture.md](05-target-architecture.md) | The final and intermediate architectures, and the migration |
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
9. The target architecture is designed and documented; only step P0 of the
   migration is next, and not built yet.
10. The owner deploys. An agent commits and pushes to the branch, never deploys.

## Starting a new session with this folder

Give the agent this folder and say something like:

> Read `live_deploy/linck-context/README.md` and the files it lists, in
> order, before doing anything. Follow `10-working-agreement.md` exactly.
> Then tell me in a few lines what you understand Linck to be and what is
> open, and wait for my task.
