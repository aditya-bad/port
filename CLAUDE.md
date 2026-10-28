# Working notes for this repository

## Priorities (from the owner)

- **The strangle (`live_deploy/app/strategies/strangle_monthly_v2.py`) has
  the highest priority of all strategies.** Its problems are fixed before
  any other strategy's, and before other features.
- Every strategy must survive a crash or restart, and an in-process
  rebuild (re-sync from the book, automatic restart), with the same state
  and the same trades as if it had never stopped.
- When the owner asks a question, answer it; don't act on it until asked.
