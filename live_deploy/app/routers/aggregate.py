"""
live_deploy — cross-deployment aggregate views.

Every other router in this package is scoped to a single deployment_id
(a URL of the shape /deployments/{deployment_id}/...). These two
endpoints are the deliberate exception: they exist specifically for the
Dashboard's consolidated views, which need data spanning EVERY
deployment at once. Aggregating server-side here, rather than having
the frontend fetch every deployment's own positions/trades and merge
them client-side, avoids N+1 requests and stays correct as the number
of deployments grows — the whole reason this router exists rather than
just leaving it to the frontend.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from ..db import queries
from ..deployments.schemas import (
    AggregatePositionOut, PnlDigestRow, PnlReportOut, PortfolioSnapshotOut, RecentTradeOut,
    StrategyLeaderboardRow,
)

router = APIRouter(tags=["aggregate"])

_IST = ZoneInfo("Asia/Kolkata")


def period_bounds(period: str, offset: int, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Pure-Python period math for the Reports page — the [start, end)
    window (as UTC-aware datetimes, ready to bind straight into a
    `closed_at >= $1 AND closed_at < $2` query) for the period `offset`
    periods before the CURRENT one, computed in this app's own
    timezone (Asia/Kolkata, matching the ticker clock and
    queries.list_pnl_digest's bucketing) so "today"/"this week"/
    "this month" mean what a user actually watching IST markets would
    expect, not whatever the server's own UTC day boundary happens to
    be. offset=0 is the period containing `now`; offset=1 is the one
    immediately before it (used for the "vs previous period" delta);
    larger offsets step further back via prev/next navigation.

    Week is Monday-start (ISO), matching Postgres's own
    date_trunc('week', ...) convention used elsewhere in this file, so
    the Reports page's single-period view and the digest's multi-period
    trend table never disagree about where a week boundary falls.
    """
    if period not in ("day", "week", "month"):
        raise ValueError(f"period must be 'day', 'week', or 'month', got {period!r}")
    now = now or datetime.now(timezone.utc)
    now_ist = now.astimezone(_IST)

    if period == "day":
        today = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=offset)
        end = start + timedelta(days=1)
        label = start.strftime("%d %b %Y")
    elif period == "week":
        monday = (now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
                  - timedelta(days=now_ist.weekday()))
        start = monday - timedelta(weeks=offset)
        end = start + timedelta(weeks=1)
        label = f"Week of {start.strftime('%d %b %Y')}"
    else:   # month
        first_of_this_month = now_ist.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        y, m = first_of_this_month.year, first_of_this_month.month - offset
        while m < 1:
            m += 12
            y -= 1
        start = first_of_this_month.replace(year=y, month=m)
        end = start.replace(year=y + 1, month=1) if m == 12 else start.replace(month=m + 1)
        label = start.strftime("%b %Y")

    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


def year_bounds(year: int) -> tuple[datetime, datetime]:
    """[Jan 1 00:00 IST, Jan 1 00:00 IST next year) for the given
    calendar year, as UTC-aware datetimes ready to bind into a
    `closed_at >= $1 AND closed_at < $2` query — the Calendar heatmap's
    year-picker (Step 74): "show me all of 2025" needs the SAME IST
    calendar-day convention queries.list_pnl_digest's own bucketing
    already uses, not the server's UTC day boundary, or Dec 31 IST's
    late-evening trades would land in the wrong year's grid."""
    start = datetime(year, 1, 1, tzinfo=_IST)
    end = datetime(year + 1, 1, 1, tzinfo=_IST)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def fetch_positions_open(pool, dispatcher) -> list[dict]:
    """status="open" is the only value the frontend ever actually
    requests (Dashboard.load() calls Api.getAllPositions('open')) --
    pulled out on its own so app.state.cache's background loop can call
    it directly. See app/cache.py for why this is cached at all."""
    rows = await queries.list_all_positions(pool, status="open")
    out = []
    for r in rows:
        d = dict(r)
        price = dispatcher.last_prices.get(int(d["instrument_token"]))
        d["current_price"] = price
        if price is not None:
            qty, avg = float(d["qty"]), float(d["avg_entry_price"])
            d["unrealized_pnl"] = (price - avg) * qty if d["side"] == "long" \
                else (avg - price) * qty
        out.append(d)
    return out


@router.get("/positions", response_model=list[AggregatePositionOut])
async def list_all_positions(request: Request, status: str | None = "open"):
    """
    Every position across every deployment (default: open only — pass
    status=closed or status= (empty) for everything), each annotated
    with which deployment/strategy it belongs to. Mark-to-market pricing
    uses the same dispatcher.last_prices cache and the same formula as
    the per-deployment /deployments/{id}/positions endpoint — a
    deployment's own positions tab and this aggregate table are
    computed identically, so they can never silently disagree.
    """
    # status="open" (the default, and the only value the frontend ever
    # sends) is the cached hot path -- anything else falls through to a
    # live query.
    if status == "open":
        return await request.app.state.cache.get("positions_open")
    pool = request.app.state.db_pool
    rows = await queries.list_all_positions(pool, status=status)
    dispatcher = request.app.state.dispatcher

    out = []
    for r in rows:
        d = dict(r)
        if d["status"] == "open":
            price = dispatcher.last_prices.get(int(d["instrument_token"]))
            d["current_price"] = price
            if price is not None:
                qty, avg = float(d["qty"]), float(d["avg_entry_price"])
                d["unrealized_pnl"] = (price - avg) * qty if d["side"] == "long" \
                    else (avg - price) * qty
        out.append(d)
    return out


async def fetch_trades_recent(pool) -> list[dict]:
    """limit=20 is the only value the frontend ever actually requests
    (Dashboard.load() calls Api.getRecentTrades(20)) -- pulled out on
    its own so app.state.cache's background loop can call it directly."""
    rows = await queries.list_recent_trades(pool, limit=20)
    return [dict(r) for r in rows]


@router.get("/trades/recent", response_model=list[RecentTradeOut])
async def recent_trades(request: Request, limit: int = 20):
    """Latest fills across every deployment, newest first — the
    Dashboard's "what just happened" activity feed."""
    if limit == 20:
        return await request.app.state.cache.get("trades_recent")
    pool = request.app.state.db_pool
    rows = await queries.list_recent_trades(pool, limit=limit)
    return [dict(r) for r in rows]


async def fetch_portfolio_equity_curve(pool) -> list[dict]:
    """limit=1000 is the only value the frontend ever actually requests
    (Portfolio.load() calls Api.getPortfolioEquityCurve() with no args)
    -- pulled out on its own so app.state.cache's background loop can
    call it directly, same pattern as fetch_positions_open/
    fetch_trades_recent above."""
    rows = await queries.list_portfolio_equity_curve(pool, limit=1000)
    return [dict(r) for r in rows]


@router.get("/portfolio/equity-curve", response_model=list[PortfolioSnapshotOut])
async def portfolio_equity_curve(request: Request, limit: int = 1000):
    """The Portfolio view's combined equity curve — one point per IST
    calendar day (Step 96), every deployment's OWN last snapshot that
    day summed together. See queries.list_portfolio_equity_curve for
    the day-bucketing/summing and exactly what "combined" means once
    deployments start/stop/pause at different times."""
    if limit == 1000:
        return await request.app.state.cache.get("portfolio_equity_curve")
    pool = request.app.state.db_pool
    rows = await queries.list_portfolio_equity_curve(pool, limit=limit)
    return [dict(r) for r in rows]


@router.get("/portfolio/pnl-digest", response_model=list[PnlDigestRow])
async def pnl_digest(request: Request, period: str = "day", limit: int = 30, year: int | None = None):
    """Portfolio-wide realized-P&L digest, one row per calendar
    day/week — see queries.list_pnl_digest for why this is REALIZED
    P&L only, and why it's built from positions.realized_pnl rather
    than diffing deployment_snapshots. Not cached (unlike the other two
    endpoints above): this view isn't auto-refreshed/polled the way
    Dashboard's positions/trades are, and GROUP BY over positions/
    position_lots is cheap at this app's scale — a live query per
    request is simpler and there's no hot path to protect here.

    year (Step 74): the Calendar heatmap's year-picker -- when given,
    `limit` is ignored entirely and every real bucket within that whole
    IST calendar year is returned (see year_bounds/
    queries.list_pnl_digest_for_range), not just "the most recent N".
    """
    if period not in ("day", "week", "month"):
        raise HTTPException(422, detail="period must be 'day', 'week', or 'month'")
    pool = request.app.state.db_pool
    if year is not None:
        start, end = year_bounds(year)
        rows = await queries.list_pnl_digest_for_range(pool, start, end, period=period)
    else:
        rows = await queries.list_pnl_digest(pool, period=period, limit=limit)
    return [dict(r) for r in rows]


@router.get("/portfolio/pnl-report", response_model=PnlReportOut)
async def pnl_report(request: Request, period: str = "day", offset: int = 0):
    """The Reports page's single-period drill-down: one period's own
    realized-P&L summary (with a same-shape previous-period summary
    alongside it, for the "vs previous period" delta the stat cards
    show), plus that period's By Strategy and By Deployment
    breakdowns. See period_bounds() above for what "period N, offset
    O" actually means, and queries.pnl_summary_for_range /
    pnl_by_strategy_for_range / pnl_by_deployment_for_range for the
    underlying queries -- all REALIZED P&L only, same reasoning as
    /portfolio/pnl-digest."""
    if period not in ("day", "week", "month"):
        raise HTTPException(422, detail="period must be 'day', 'week', or 'month'")
    if offset < 0:
        raise HTTPException(422, detail="offset must be >= 0 (0 = current period, 1 = previous, ...)")

    pool = request.app.state.db_pool
    start, end, label = period_bounds(period, offset)
    prev_start, prev_end, _ = period_bounds(period, offset + 1)

    summary = await queries.pnl_summary_for_range(pool, start, end)
    prev_summary = await queries.pnl_summary_for_range(pool, prev_start, prev_end)
    by_strategy = await queries.pnl_by_strategy_for_range(pool, start, end)
    by_deployment = await queries.pnl_by_deployment_for_range(pool, start, end)

    return {
        "period": period, "offset": offset,
        "period_start": start, "period_end": end, "label": label,
        **summary,
        "prev_realized_pnl": prev_summary["realized_pnl"],
        "by_strategy": [dict(r) for r in by_strategy],
        "by_deployment": [dict(r) for r in by_deployment],
    }


async def fetch_strategy_leaderboard(pool) -> list[dict]:
    """No parameters -- this endpoint only ever has one shape (all-time,
    every strategy), unlike pnl-digest/pnl-report which take period/
    offset -- pulled out on its own so app.state.cache's background
    loop can call it directly, same pattern as the other fetch_* helpers
    in this file."""
    rows = await queries.list_strategy_leaderboard(pool)
    return [dict(r) for r in rows]


@router.get("/portfolio/strategy-leaderboard", response_model=list[StrategyLeaderboardRow])
async def strategy_leaderboard(request: Request):
    """All-time realized P&L per strategy, ranked best to worst — the
    Portfolio view's "which strategy has actually made the most money
    since I started" answer, distinct from Reports' period-scoped By
    Strategy breakdown. Cached (unlike pnl-digest/pnl-report): this one
    IS in Portfolio's own auto-refresh cycle (_AUTO_REFRESH_VIEWS polls
    Portfolio every 6s), so a live GROUP BY on every poll would be pure
    waste the same way Portfolio's other sections already avoid via
    app.state.cache."""
    return await request.app.state.cache.get("strategy_leaderboard")
