# Working notes for this repository

## Priorities (from the owner)

- **The strangle (`live_deploy/app/strategies/strangle_monthly_v2.py`) has
  the highest priority of all strategies.** Its problems are fixed before
  any other strategy's, and before other features.
- Every strategy must survive a crash or restart, and an in-process
  rebuild (re-sync from the book, automatic restart), with the same state
  and the same trades as if it had never stopped.
- When the owner asks a question, answer it; don't act on it until asked.

## Waiting for the owner (don't start until they bring it up)

### The 28 Oct 2026 repairs ("the 28th Oct repair fix")

The day check flagged five deployments for 28 Oct. Looked into with the
owner on 29 Oct; nothing repaired, dismissed or fixed yet.

| Deployment | Verdict | Evidence |
|---|---|---|
| DTT BankNifty Strangle | False flag: ignore | App held 09:15 by the stale-price guard (50800PE not traded yet), quotes refused the 50% roll at 09:23:16, rolled at 09:26. Replay rolled the same contracts at 09:15. About ₹6 apart, same end positions |
| ST_PV_NIFTY | Real: repair | App traded with pivots from 26 Oct (S1 22,509.30) instead of 27 Oct. No entry at 09:20 (close 22,574.75); entered 10:30 at 22,507. Replay used 27 Oct's levels and entered 09:20. App ₹2,873 worse |
| ST_PV_SENSEX | Real: repair | Same: 26 Oct pivots (S1 72,878.63); 09:20 close 72,914.52 didn't break it; entered 09:30. App ₹1,269 worse |
| Straddle Nifty Advanced | False flag: ignore | App's minute marks at 14:53: 22600CE 250.00, 22600PE 127.00 (0.508, trigger is ≤ 0.5). Replay crossed only by pairing the minute's CE high with its PE low |
| Straddle Nifty Breakeven Adjustments | False flag: ignore | Same marks, same minute |

Fixes agreed as needed, in this order (strangle first):

1. Day check, strangle: the strategy logs when every leg has first traded
   (the stale-price hold ends); the replay holds until then, as it already
   honours logged quote refusals.
2. pivot_supertrend_options (and siblings): before trading, check pivots
   are from the previous trading day; if not, re-read from Kite; if that
   fails, block entries and alert. Today the day rollover keeps old
   pivots silently when it has no high/low of its own. Why they weren't
   rolled on 27→28 Oct is unknown: that container's logs were lost when it
   was recreated at 21:33 IST on 28 Oct.
3. Day check: a trade that only fires by pairing two legs' extremes from
   one minute, where the app's own per-minute marks never crossed, is
   "ambiguous", not "mismatch".

Also seen: the BankNifty Strangle's "checkpoint target blocked since 12:16"
on 28 Oct: the profit target refused by Kite's quotes for over two hours.

### Audit of the other strategies

Every strategy except the strangle, for state set after a fill's metadata
is written or kept only in memory, so it's lost on a restart or rebuild
(the strangle's convergence bug, fixed in 6b2de96). The owner will say
when to run it.
