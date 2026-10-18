"""Shared checks for strategy simulations."""

from app.strategies.registry import list_strategies


def cfg(strategy: str, **over) -> dict:
    """The strategy's own registered default config, with overrides."""
    base = next(s for s in list_strategies() if s["name"] == strategy)["default_config"]
    return {**base, **over}


async def assert_book_consistent(pool, dep_id):
    """The fill engine's invariants, from the database alone:
    cash = capital + sells - buys; open qty = net of fills; a closed
    position's realized P&L = (sell avg - buy avg) * qty."""
    dep = await pool.fetchrow("SELECT * FROM deployments WHERE id = $1", dep_id)
    lots = await pool.fetch("SELECT * FROM position_lots WHERE deployment_id = $1", dep_id)
    flow = sum(float(l["qty"]) * float(l["price"]) * (1 if l["action"] == "sell" else -1) for l in lots)
    assert abs(float(dep["initial_capital"]) + flow - float(dep["current_cash"])) < 0.05, \
        f"cash {dep['current_cash']} != capital {dep['initial_capital']} + fills {flow:.2f}"
    for p in await pool.fetch("SELECT * FROM positions WHERE deployment_id = $1", dep_id):
        pl = [l for l in lots if l["position_id"] == p["id"]]
        opening = "sell" if p["side"] == "short" else "buy"
        opened = sum(float(l["qty"]) for l in pl if l["action"] == opening)
        closed = sum(float(l["qty"]) for l in pl if l["action"] != opening)
        if p["status"] == "open":
            assert closed == 0 and abs(float(p["qty"]) - opened) < 1e-6, f"{p['symbol']}: open qty {p['qty']} vs fills {opened}"
        else:
            assert abs(closed - opened) < 1e-6, f"{p['symbol']}: opened {opened} closed {closed}"
            avg_in = sum(float(l["qty"]) * float(l["price"]) for l in pl if l["action"] == opening) / opened
            avg_out = sum(float(l["qty"]) * float(l["price"]) for l in pl if l["action"] != opening) / closed
            pnl = (avg_out - avg_in) * opened if p["side"] == "long" else (avg_in - avg_out) * opened
            assert abs(pnl - float(p["realized_pnl"])) < 0.05, f"{p['symbol']}: realized {p['realized_pnl']} vs {pnl:.2f}"
