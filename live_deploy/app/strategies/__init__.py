"""
Placeholder for live strategy modules — not implemented yet.

When strategies are added here, each one implements the
`StrategyBase` interface in `app.deployments.strategy_base`:

    class MyStrategy(StrategyBase):
        async def on_start(self, runner): ...
        async def on_tick(self, runner, tick): ...
        async def on_stop(self, runner): ...

A DeploymentRunner (app.deployments.runner) owns one strategy instance
per deployment — it already subscribes to the shared TickBroadcaster,
filters ticks down to the deployment's configured instrument_tokens,
and calls the strategy's on_tick() for each relevant one. The strategy
never touches the broadcaster, Kite, or the database directly — it
reacts to ticks and calls back into `runner.buy(...)` / `runner.sell(...)`,
which is what actually persists a paper fill (position, lot, cash) to
Postgres.

Nothing is implemented in this package yet — "once infra is ready,
I'll tell you the strategies."
"""
