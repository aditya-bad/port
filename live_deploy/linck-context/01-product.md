# 01 · The product

## What Linck is today

A self-hosted web app (plus an installable PWA) for running and watching
automated option-selling strategies on Indian index derivatives through
Zerodha Kite. It **paper-trades**: fills are simulated at the live last-traded
price; no real order is ever sent.

- A **strategy** is a Python class with a fixed set of rules (about 10 types,
  see `03-strategies-and-market.md`).
- A **deployment** is one running copy of a strategy, with its own name,
  capital, settings (config JSON) and state. Several deployments of the same
  strategy run side by side with small config differences, as A/B tests.
- The owner is one trader, occasionally with one or two trusted logins. They
  know trading terms but don't want to decode ratios; they want plain-word
  verdicts ("7.51 excellent", "In loss", "Would be rejected: lot size").

### How the owner uses it

- **Market hours (09:15–15:30 IST), mostly on a phone, many short checks.**
  Is anything broken? What's my P&L? Pause or close something fast.
- **Evenings, on a laptop.** Which deployments are good or bad, why a trade
  went wrong, whether to change settings, start new deployments, review the
  week.

### The one question

Linck exists to answer: **which of these strategies deserves real money?**
That splits into:

- **Are the numbers true?** Charges, slippage, shadow mode, the day check,
  repair, self-reporting strategies, stale-price guards, crash recovery.
- **Which is better?** Leaderboard, Compare (including combined groups),
  Strategy page, Geek mode, Conditions, Volatility, weekly review.

A feature that serves neither question is usually rejected. When the owner
was offered generic feature lists ("Telegram bot", "kill switch", "tax
export") they rejected all of them except **shadow mode**, because it directly
answers "would live trading have matched paper?".

## The repo

`aditya-bad/port` holds three things:

| Folder | What | Status |
|---|---|---|
| repo root (`fetch_ohlcv.py`, `backtest.py`, `analytics.py`, `generic/`) | "NiftyShop": a Nifty 50 equity backtest (buy below a moving average, average down, exit at a target) with point-in-time index membership | Done, independent, not touched since Aug |
| `tg_int_st_pp/` | Nifty 5-minute data and validation of the Pivot + SuperTrend(7,3) signal | Done; the signal was ported into live_deploy |
| `live_deploy/` | **Linck**: the paper-trading platform | The active project |

`live_deploy` shares no code or data with the other two.

## The vision (from `reference/linck-platform.html`)

Linck as a company: an algo-trading platform where people write strategies
and Linck runs them, the way cloud agent platforms run other people's agents.

> Build Linck as the platform where Indian F&O traders write a strategy once
> and run it on the same engine through backtest, paper, shadow and live, and
> where every rupee it reports has been checked against what the market would
> really have given.

The **trust layer is the moat**: no other platform proves paper matches live
or lists every place a strategy's day went wrong.

**Linck Platform is three products together: LinckTrade (trading and the
money), LinckLens (what people see) and the Linck SDK (what strategy
developers use).** Whoever runs it chooses where each part runs: any cloud,
their own servers, or a mix, and it must work the same in every mix (owner,
25 Oct). See "Deploy anywhere" in `05-target-architecture.md`.

Four ways in, each built on the one before:

1. **Linck App** (first): today's Linck, for many retail option traders.
2. **Linck Runtime** (second): "write your strategy, we run it". SDK, CLI,
   backtest farm, certification, paper → shadow → live.
3. **Linck for Brokers** (third): white-label for a broker's customers.
4. **Marketplace** (last): only once the regulatory path for selling
   strategies (research-analyst registration, white/black-box rules) exists.

Starting plan prices are hypotheses (Free; Trader ₹999–1,999/mo; Builder
₹2,999–4,999/mo + usage; Desk; Broker; Creator revenue share).

Rules of the Indian market that shape everything (each needs a lawyer before
launch): SEBI's 2025 retail-algo framework (orders via broker, static IPs,
exchange algo IDs), white-box vs black-box algos, an order-rate threshold
(~10 orders/s per client) above which algos must be registered, market data
licensing (one user's Kite feed can't be redistributed; a shared feed needs a
licensed vendor), broker limits (Kite: 3 WebSockets × 3,000 instruments per
API key), long record retention, DPDP Act, data in India.

Roadmap stages: 0 "Linck for one" (now) → 1 intermediate platform, 50–300
invited users (months 1–6) → 2 public SDK/API, paid plans (6–15) → 3
enterprise, cells, partner brokers, marketplace (15–30).

What we would **not** do: chase HFT or co-location; become a broker; open a
marketplace early; fight no-code builders head-on; rewrite strategies in Rust
"for speed"; build the final architecture first; let the UI or a strategy
enforce risk.
