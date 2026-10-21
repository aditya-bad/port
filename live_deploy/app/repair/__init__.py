"""
live_deploy — repairing a stretch of the session the app missed (a Kite
tick stall, an outage): the strategies' own code is run again over that
stretch on 1-minute candles, and what it would have done is shown, chosen
where the candles can't tell, and applied one deployment at a time.

  market.py  the replayed market: 1-minute candles as ticks, the clock,
             Kite and the dispatcher as the strategies see them
  engine.py  the replay itself, its plan and P&L, apply and undo

Driven by custom_scripts/repair_stall.py (an interactive CLI).
"""
