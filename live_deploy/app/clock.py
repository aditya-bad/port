"""The one place strategies read the time of day from, so a test can
drive a strategy through simulated sessions (tests/sim.py replaces
now_ist). Production: the real clock, in IST."""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Timezone-aware current time in IST."""
    return datetime.now(IST)
