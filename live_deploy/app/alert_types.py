"""The kinds of alert a deployment raises, and which you can turn off.

Each deployment keeps the kinds it doesn't want (deployments.alerts_off,
migration 0046), set on its Settings tab or for every deployment of a
strategy on the strategy's page. An alert turned off isn't raised at all:
not on the Desk, not as a toast, not pushed to a phone. What it watches
still happens and is still kept (the trade, the issue on the Repair page,
the finding on the Performance tab); only the alert is gone.

The critical ones can't be turned off: a deployment that isn't running,
has errored, has stopped acting on prices, keeps raising errors, reports
a critical problem itself, or holds legs past the day's end. Missing one
of those can lose money unwatched.

The browser has the same list (static/linck/js/data/alert_types.js);
tests/test_alert_types.py keeps the two the same.
"""

from __future__ import annotations

from typing import Iterable, Optional

# key: (label, what it is)
TYPES: dict[str, tuple[str, str]] = {
    "trades": ("Entries and exits", "A toast and a push each time it enters or exits"),
    "paused": ("Paused with open legs", "On the Desk while it's paused holding positions"),
    "streak": ("Losing streak", "On the Desk after several losing trades in a row"),
    "win_rate": ("Winning less often", "Its recent win rate well under its usual one"),
    "loss_size": ("Losing more per loss", "Its recent losses well bigger than usual"),
    "worst_loss": ("Worst trade yet", "A trade lost more than any before it"),
    "slippage": ("Paying more in slippage", "Slippage per trade doubled"),
    "drawdown": ("Deepest drawdown yet", "Further below its best than it has ever been"),
    "fewer_trades": ("Trading less than usual", "Far fewer trades than usual in the last 10 market days"),
    "vix": ("VIX spike", "VIX up 10% or more in a day while it holds sold premium"),
    "shadow": ("Kite would have refused", "Shadow mode: an order the real Kite would have rejected"),
    "stale_legs": ("Legs not traded today", "Open legs with no trade today, after 09:45"),
    "strategy_warning": ("Strategy warnings", "Non-critical problems the strategy reports about itself"),
}

ALWAYS_ON: tuple[tuple[str, str], ...] = (
    ("Isn't running", "Marked active, but nothing runs its strategy"),
    ("Errored", "Its strategy stopped with an error"),
    ("Not acting on prices", "Running, prices flow, but it hasn't acted on one in 3 minutes"),
    ("Strategy errors", "Its strategy raised errors in the last 5 minutes"),
    ("Critical problems it reports", "A failed fill, legs it lost track of, an entry it couldn't make"),
    ("Day's-end rules", "An intraday one holding legs after its cut-off, a leg past expiry"),
)


def clean(keys: Optional[Iterable]) -> list[str]:
    """Known kinds only, each once, in the catalogue's order."""
    want = {k for k in (keys or []) if isinstance(k, str)}
    return [k for k in TYPES if k in want]


def unknown(keys: Iterable) -> list[str]:
    return [k for k in keys if k not in TYPES]


def of_problem(kind: str, severity: str) -> Optional[str]:
    """The kind an operational issue (app/ops_health.py) alerts as, or None
    for one that always alerts."""
    if kind == "stale_legs":
        return "stale_legs"
    if kind.startswith("strategy:") and severity != "critical":
        return "strategy_warning"
    return None


def is_on(alerts_off: Optional[Iterable], kind: Optional[str]) -> bool:
    return kind is None or kind not in set(alerts_off or ())
