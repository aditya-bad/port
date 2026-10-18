"""Slippage estimates against the order book (app/slippage.py)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.slippage import book_fill_price, fill_slippage, projected_slippage, realized_slippage, resolve_book_price

BOOK = {
    "buy": [{"price": 99.0, "quantity": 50}, {"price": 98.0, "quantity": 50}],
    "sell": [{"price": 101.0, "quantity": 50}, {"price": 102.0, "quantity": 0}, {"price": 103.0, "quantity": 50}],
}


def test_sell_walks_the_bids_buy_walks_the_asks():
    assert book_fill_price(BOOK, "sell", 50) == 99.0
    assert book_fill_price(BOOK, "sell", 75) == pytest.approx((50 * 99 + 25 * 98) / 75)
    assert book_fill_price(BOOK, "buy", 75) == pytest.approx((50 * 101 + 25 * 103) / 75), "empty level skipped"


def test_beyond_the_visible_book_fills_at_the_worst_level():
    assert book_fill_price(BOOK, "sell", 200) == pytest.approx((50 * 99 + 150 * 98) / 200)


@pytest.mark.parametrize("depth,qty", [(None, 75), ({}, 75), ({"buy": [], "sell": []}, 75), (BOOK, 0),
                                       ({"buy": [{"price": 0, "quantity": 10}]}, 10)])
def test_no_book_is_none(depth, qty):
    assert book_fill_price(depth, "sell", qty) is None


def test_fill_slippage_sign():
    assert fill_slippage("sell", 100, 99.5, 75) == pytest.approx(37.5)
    assert fill_slippage("buy", 100, 100.4, 75) == pytest.approx(30.0)
    assert fill_slippage("buy", 100, 99.0, 10) == pytest.approx(-10.0), "printed outside the book"
    assert fill_slippage("sell", 100, None, 75) == 0.0


def test_realized_counts_only_closed():
    assert realized_slippage({"status": "closed", "estimated_slippage": 12.5}) == 12.5
    assert realized_slippage({"status": "open", "estimated_slippage": 12.5}) == 0.0
    assert realized_slippage({"status": "closed", "estimated_slippage": None}) == 0.0


class D:
    def __init__(self, prices=None, depths=None):
        self.last_prices = prices or {}
        self.last_depths = depths or {}
        self.access_token = None


def test_projected_slippage_adds_the_exit_side():
    now = datetime.now(timezone.utc)
    pos = {"instrument_token": 7, "side": "short", "qty": 50, "estimated_slippage": 10.0}
    assert projected_slippage(pos, D()) == 0.0, "no live price: not computed"
    assert projected_slippage(pos, D({7: 100.0})) == 10.0, "no book: entry side only"
    # a short exits by buying: lifts the ask at 101 vs LTP 100
    assert projected_slippage(pos, D({7: 100.0}, {7: (BOOK, now)})) == pytest.approx(10.0 + 50.0)
    long_ = dict(pos, side="long")
    assert projected_slippage(long_, D({7: 100.0}, {7: (BOOK, now)})) == pytest.approx(10.0 + 50.0)


def test_resolve_book_price_uses_a_fresh_cached_book():
    now = datetime.now(timezone.utc)
    assert asyncio.run(resolve_book_price(D(depths={7: (BOOK, now)}), 7, "sell", 50)) == 99.0


def test_resolve_book_price_never_raises_without_a_book_or_kite():
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert asyncio.run(resolve_book_price(D(depths={7: (BOOK, stale)}), 7, "sell", 50)) is None
    assert asyncio.run(resolve_book_price(D(), 7, "sell", 50)) is None


def test_resolve_book_price_falls_back_to_one_quote(monkeypatch):
    from app import options

    async def get_quotes(self, toks):
        return {"NFO:X": {"instrument_token": 7, "depth": BOOK}}
    monkeypatch.setattr(options.OptionsResolver, "get_quotes", get_quotes)
    assert asyncio.run(resolve_book_price(D(), 7, "buy", 50)) == 101.0
