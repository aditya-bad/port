"""custom_scripts/export_vol_history.py: the history the TimesFM
volatility experiment reads, asked of Kite in pieces it accepts."""

import csv
import importlib.util
import io
import os
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "export_vol_history", os.path.join(HERE, "..", "..", "custom_scripts", "export_vol_history.py"))
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)


def test_pieces_cover_the_range_back_to_back_within_kites_limit():
    start, end = date(2019, 1, 1), date(2026, 11, 4)
    for interval, span in X.SPAN.items():
        got = X.pieces(start, end, span)
        assert got[0][0] == start and got[-1][1] == end
        for (a, b), (c, _) in zip(got, got[1:]):
            assert c == b + timedelta(days=1), "no gap, no overlap"
        assert all((b - a).days + 1 <= span.days for a, b in got)
    assert X.SPAN["day"].days <= 2000 and X.SPAN["5minute"].days <= 100, "inside Kite's limits per request"


def test_every_index_daily_and_5_minute_and_vix_daily_only():
    asked = []

    def historical(token, frm, to, interval):
        asked.append((token, interval, frm, to))
        return [{"date": datetime(2024, 1, 2, 9, 15), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    buf = io.StringIO()
    n = X.export(historical, csv.writer(buf), date(2024, 1, 1), date(2024, 6, 30))
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert n == len(rows) == len(asked)
    kinds = {(r[0], r[1]) for r in rows}
    assert kinds == {("NIFTY 50", "day"), ("NIFTY 50", "5minute"), ("NIFTY BANK", "day"), ("NIFTY BANK", "5minute"),
                     ("SENSEX", "day"), ("SENSEX", "5minute"), ("INDIA VIX", "day")}
    assert rows[0][2] == "2024-01-02T09:15:00" and rows[0][3:] == ["1.0", "2.0", "0.5", "1.5"]
    five = [a for a in asked if a[0] == 256265 and a[1] == "5minute"]
    assert len(five) == 2 and five[0][3].time() == datetime.max.time().replace(microsecond=0), "each piece runs to its last day's end"


def test_daily_only_asks_for_no_5_minute_candles():
    asked = []
    X.export(lambda t, a, b, i: asked.append(i) or [], csv.writer(io.StringIO()), date(2024, 1, 1), date(2024, 3, 1),
             intraday=False)
    assert set(asked) == {"day"}
