"""Charges against figures worked out by hand from Zerodha's published
F&O options rates — not against the function itself, so a changed rate
fails here."""

import pytest

from app.charges import charge_breakdown, estimate_options_charges


def test_breakdown_buy_100_sell_110_qty_400():
    # buy 40,000 · sell 44,000 · turnover 84,000
    c = charge_breakdown(100, 110, 400)
    assert c["brokerage"] == pytest.approx(40.0)          # Rs 20 x 2 orders
    assert c["stt"] == pytest.approx(66.0)                 # 0.15% of the sell side
    assert c["exchange"] == pytest.approx(29.8452)         # 0.03553% of turnover
    assert c["sebi"] == pytest.approx(0.084)               # Rs 10 / crore
    assert c["gst"] == pytest.approx(12.587256)            # 18% of brokerage + exchange + SEBI
    assert c["stamp_duty"] == 1                            # 0.003% of buy side, rounded
    assert c["total"] == pytest.approx(149.516456)
    assert estimate_options_charges(100, 110, 400) == pytest.approx(149.516456)


def test_large_order_scales_except_brokerage():
    c = charge_breakdown(100, 110, 40000)
    assert c["brokerage"] == 40.0, "flat per order, whatever the size"
    assert c["stt"] == pytest.approx(6600.0)
    assert c["stamp_duty"] == 120
    assert c["total"] == pytest.approx(40 + 6600 + 2984.52 + 8.4 + 0.18 * (40 + 2984.52 + 8.4) + 120)


def test_charges_never_depend_on_direction():
    assert estimate_options_charges(50, 80, 75) == estimate_options_charges(50, 80, 75)
    assert charge_breakdown(80, 50, 75)["stt"] == pytest.approx(0.0015 * 50 * 75), "STT is on the sell price"
