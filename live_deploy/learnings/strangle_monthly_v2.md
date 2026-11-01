# Learnings: the monthly strangle (strangle_monthly_v2)

What the strangle's own trades suggest it should do differently. These are
probable learnings, not rules: each says what was seen and what would
confirm it. None of them is in the code until the owner says so.

## 1. After a cycle that opened and closed the same day, wait for the next day

*Noted 1 Nov 2026, from the owner. Probable, not confirmed.*

**The idea.** If a cycle is entered and exited on the same day, don't
start the next cycle that day: enter it the next trading day. A cycle that
ran longer (more than 2 days) can be followed the same day as it is today.

**What was seen.** Cycle 16 (the owner's reading; cycles before it not
looked at yet). The cycle before it was entered and exited on the same
day, at the checkpoint, about ₹750 up. The strangle entered cycle 16 later
that same day, and cycle 16 went badly wrong: the only cycle that has.

**Why it happens today.** When a cycle reaches its checkpoint (0.5% of
capital, `checkpoint_profit_pct_of_capital`), the strangle closes every
leg and opens a new strangle on the same tick, any time after
`entry_time` (10:00). Nothing in it looks at how long the cycle that just
ended lasted. A cycle that hits its target within hours means the market
moved a lot that day, and the new strangle is sold into that same move.

**What would confirm it.** One cycle isn't enough. Go through every
earlier cycle of every strangle deployment and compare:
- cycles entered on the day an intraday cycle exited (the case above);
- cycles entered on the day a longer cycle exited;
- cycles entered on a later day.

Count each group, compare their P&L (mean and worst), and look at whether
the same-day entries after an intraday exit lose more often. If they do,
the next step is to decide what "next day" means (the next day at
`entry_time`, or at 09:15) and whether 2 days is the right line.

**Still open.**
- The earlier cycles haven't been looked at: the cycle history is on the
  server, not here.
- Whether "more than 2 days" or "more than the same day" is the line.
  As said, a cycle of 1 or 2 days falls in neither case.
- Whether waiting costs more than it saves: a night with no position
  misses that night's time decay.
