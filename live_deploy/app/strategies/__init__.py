"""
Placeholder for live strategy modules — not implemented yet.

When strategies are added here, each one will typically:

  1. Call `await app.state.broadcaster.subscribe()` to get its own
     asyncio.Queue of tick batches — fed from the SAME single upstream
     Kite connection every other consumer (including external /ws/ticks
     clients) shares. Subscribing here never opens an extra Kite session.
  2. Run its own decision loop against that tick stream, independently
     of any other subscriber.
  3. Call `await app.state.broadcaster.unsubscribe(queue)` on shutdown.

Nothing is implemented in this package yet.
"""
