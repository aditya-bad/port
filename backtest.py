#!/usr/bin/env python3
"""
NiftyShop Stage 2/3 — Configurable Backtest Engine

Config-driven backtest for the "buy below DMA, average down, exit at target"
strategy across point-in-time Nifty 50 constituents.

Usage:
    python backtest.py                           # default config
    python backtest.py -c custom_config.json     # custom config
    python backtest.py --validate                # check config + data only
"""

import json
import math
import sys
import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


# ── Paths ────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
RUNS_DIR = DATA_DIR / "runs"
PIT_FILE = DATA_DIR / "pit_universe_intervals.json"
FETCH_LOG_FILE = OHLCV_DIR / "_fetch_log.json"

HDFC_MERGER_DATE = date(2023, 7, 13)


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class Lot:
    buy_date: date
    buy_price: float
    qty: int


class Position:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.lots: list[Lot] = []

    @property
    def total_qty(self) -> int:
        return sum(l.qty for l in self.lots)

    @property
    def num_lots(self) -> int:
        return len(self.lots)

    @property
    def avg_cost(self) -> float:
        """Quantity-weighted average cost (weighted by shares, not lot count)."""
        tq = self.total_qty
        return sum(l.qty * l.buy_price for l in self.lots) / tq if tq else 0.0

    @property
    def last_buy_price(self) -> float:
        """Most recent lot's buy price — used as averaging trigger reference."""
        return self.lots[-1].buy_price if self.lots else 0.0

    @property
    def capital_invested(self) -> float:
        """shares × avg_cost — used for exit tie-breaking."""
        return self.total_qty * self.avg_cost

    def add_lot(self, buy_date: date, buy_price: float, qty: int):
        self.lots.append(Lot(buy_date=buy_date, buy_price=buy_price, qty=qty))


# ── PIT Universe ─────────────────────────────────────────────────────

class PITUniverse:
    """Point-in-time constituent membership: half-open [start, end)."""

    def __init__(self, raw: list[dict], excluded: set[str]):
        self._intervals: list[tuple[str, date | None, date | None]] = []
        for r in raw:
            sym = r["symbol"]
            if sym in excluded:
                continue
            s = date.fromisoformat(r["start_date"]) if r["start_date"] else None
            e = date.fromisoformat(r["end_date"]) if r["end_date"] else None
            self._intervals.append((sym, s, e))

    def on(self, day: date) -> set[str]:
        """Return symbols that are Nifty 50 constituents on `day`."""
        out = set()
        for sym, s, e in self._intervals:
            if s and day < s:
                continue
            if e and day >= e:
                continue
            out.add(sym)
        return out


# ── Backtest Engine ──────────────────────────────────────────────────

class BacktestEngine:

    def __init__(self, config: dict):
        self.cfg = config
        self.run_name: str = config["run_name"]

        # ── Unpack config — every value from config, no coded defaults ──
        cap = config["capital"]
        self.initial_capital = float(cap["initial"])
        self.divisor         = cap["divisor"]
        self.max_lots        = cap["max_lots"]
        self.throttle_after  = cap["throttle_after_lots"]
        self.throttle_to     = cap["throttle_to_lots_per_day"]
        self.recalc_anchor   = cap["recalc_anchor"]

        ent = config["entry"]
        self.dma_period      = ent["dma_period"]
        self.min_pct_below   = ent["min_pct_below_dma"]
        self.candidates_n    = ent["candidates_per_day"]
        self.max_new_per_day = ent["max_new_positions_per_day"]

        avg = config["averaging"]
        self.avg_trigger     = avg["trigger_pct_from_last_buy"]
        self.avg_max_buys    = avg["max_buys_per_day"]
        self.avg_max_lots    = avg["max_lots_per_stock"]          # None = uncapped
        self.avg_stop_loss   = avg["stop_loss_pct_from_avg_cost"] # None = off

        ex = config["exit"]
        self.target_pct      = ex["target_pct_above_avg_cost"]
        self.max_sells       = ex["max_sells_per_day"]
        self.tie_break       = ex["tie_break"]

        self.corp  = config["corporate_actions"]
        self.costs = config["costs"]

        # Dates
        self.bt_start = date.fromisoformat(config["backtest_start"])
        self.bt_end   = date.fromisoformat(config["backtest_end"])
        self.is_end   = date.fromisoformat(config["in_sample_end"])

        # ── Mutable state ────────────────────────────────────────────
        self.cash: float     = self.initial_capital
        self.lot_size: float = self.initial_capital / self.divisor
        self.positions: dict[str, Position] = {}
        self.cum_rpnl: float = 0.0   # cumulative realized P&L post-cost
        self.last_recalc     = self.bt_start

        # ── Stats — overall ──────────────────────────────────────────
        self.sells = self.wins = 0
        self.peak  = self.initial_capital
        self.max_dd = 0.0
        self.daily_vals: list[tuple[date, float]] = []

        # ── Stats — IS / OOS splits ──────────────────────────────────
        self.is_sells = self.is_wins = 0
        self.oos_sells = self.oos_wins = 0
        self.is_rpnl = self.oos_rpnl = 0.0
        self.is_peak  = self.initial_capital
        self.is_dd    = 0.0
        self.oos_peak: float | None = None
        self.oos_dd   = 0.0

        # ── Buy-and-hold benchmark ───────────────────────────────────
        self.bnh_val  = self.initial_capital
        self.bnh_prev: dict[str, float] = {}

        # ── Output paths ─────────────────────────────────────────────
        self.run_dir = RUNS_DIR / self.run_name
        self.tlog = self.run_dir / "trade_log.jsonl"
        self.dlog = self.run_dir / "daily_portfolio.jsonl"
        self.slog = self.run_dir / "summary.json"

        # Load data
        self._load()

    # ═════════════════════════════════════════════════════════════════
    # DATA LOADING
    # ═════════════════════════════════════════════════════════════════

    def _load(self):
        # Excluded symbols from fetch log (no_kite_data)
        excluded: set[str] = set()
        if FETCH_LOG_FILE.exists():
            fl = json.loads(FETCH_LOG_FILE.read_text())
            for sym, info in fl.get("symbols", {}).items():
                if info.get("status") == "no_kite_data":
                    excluded.add(sym)
        self._excluded = excluded

        # PIT intervals
        pit_raw = json.loads(PIT_FILE.read_text())
        self.pit = PITUniverse(pit_raw, excluded)

        # OHLCV — one dict per symbol: {date → close}
        self.close: dict[str, dict[date, float]] = {}
        for p in sorted(OHLCV_DIR.glob("*.json")):
            if p.name.startswith("_"):
                continue
            sym = p.stem
            candles = json.loads(p.read_text())
            self.close[sym] = {
                date.fromisoformat(c["date"]): c["close"] for c in candles
            }

        if not self.close:
            sys.exit("No OHLCV files in data/ohlcv/. Run fetch_ohlcv.py first.")

        # Trading calendar — union of all dates in OHLCV within backtest window
        all_dates: set[date] = set()
        for d_map in self.close.values():
            all_dates.update(d_map)
        self.days = sorted(d for d in all_dates
                           if self.bt_start <= d <= self.bt_end)
        if not self.days:
            sys.exit(f"No trading days in [{self.bt_start}, {self.bt_end}].")

        # Precompute SMAs
        self._build_sma()

        print(f"Loaded {len(self.close)} symbols, {len(self.days)} trading days "
              f"({self.days[0]}…{self.days[-1]})")
        if excluded:
            print(f"Excluded (no_kite_data): {sorted(excluded)}")

    def _build_sma(self):
        """Compute rolling close-price SMA for each symbol."""
        p = self.dma_period
        self.sma: dict[str, dict[date, float]] = {}
        for sym, closes in self.close.items():
            buf: list[float] = []
            out: dict[date, float] = {}
            for d in sorted(closes):
                buf.append(closes[d])
                if len(buf) > p:
                    buf.pop(0)
                if len(buf) == p:
                    out[d] = sum(buf) / p
            self.sma[sym] = out

    # ═════════════════════════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════════════════════════

    def _close_px(self, sym: str, day: date) -> float | None:
        return self.close.get(sym, {}).get(day)

    def _sma_val(self, sym: str, day: date) -> float | None:
        return self.sma.get(sym, {}).get(day)

    def _lots_open(self) -> int:
        return sum(p.num_lots for p in self.positions.values())

    def _pf_value(self, day: date) -> float:
        v = self.cash
        for p in self.positions.values():
            px = self._close_px(p.symbol, day)
            v += p.total_qty * (px if px is not None else p.avg_cost)
        return v

    def _log(self, entry: dict):
        with open(self.tlog, "a") as f:
            f.write(json.dumps(entry) + "\n")

    # ═════════════════════════════════════════════════════════════════
    # TRADE EXECUTION
    # ═════════════════════════════════════════════════════════════════

    def _buy(self, sym: str, day: date, price: float, reason: str) -> bool:
        """Buy one lot of `sym` at `price`. Returns True if executed."""
        qty = math.floor(self.lot_size / price)
        if qty < 1:
            self._log({"date": day.isoformat(), "action": "skip",
                        "reason": f"affordability_{reason}", "symbol": sym,
                        "price": price, "lot_size": round(self.lot_size, 2)})
            return False

        cost = qty * price
        brk = cost * self.costs["brokerage_pct"] / 100
        if self.cash < cost + brk:
            self._log({"date": day.isoformat(), "action": "skip",
                        "reason": f"cash_insufficient_{reason}", "symbol": sym,
                        "price": price, "need": round(cost + brk, 2),
                        "cash": round(self.cash, 2)})
            return False

        self.cash -= cost + brk
        if sym not in self.positions:
            self.positions[sym] = Position(sym)
        self.positions[sym].add_lot(day, price, qty)

        self._log({"date": day.isoformat(), "action": "buy", "reason": reason,
                    "symbol": sym, "price": price, "qty": qty,
                    "lot_capital": round(cost, 2), "brokerage": round(brk, 2),
                    "lots_open_after": self._lots_open(),
                    "cash_after": round(self.cash, 2)})
        return True

    def _sell(self, sym: str, day: date, price: float, reason: str) -> float:
        """Sell entire position in `sym`. Returns realized P&L post-cost."""
        pos = self.positions[sym]
        qty   = pos.total_qty
        avg   = pos.avg_cost
        gross = qty * price
        basis = qty * avg

        # Costs
        brk_sell = gross * self.costs["brokerage_pct"] / 100
        brk_buy  = basis * self.costs["brokerage_pct"] / 100  # paid at buy time
        stt      = gross * self.costs["stt_pct"] / 100

        # Tax per lot (STCG vs LTCG based on holding period)
        tax = 0.0
        for lot in pos.lots:
            lot_gain = (price - lot.buy_price) * lot.qty
            if lot_gain > 0:
                hd = (day - lot.buy_date).days
                rate = (self.costs["ltcg_tax_pct"]
                        if hd >= self.costs["ltcg_holding_period_days"]
                        else self.costs["stcg_tax_pct"])
                tax += lot_gain * rate / 100

        total_costs = brk_sell + brk_buy + stt + tax
        gross_pnl   = gross - basis
        rpnl        = gross_pnl - total_costs

        # Update cash (buy brokerage already paid at buy time)
        self.cash += gross - brk_sell - stt - tax
        self.cum_rpnl += rpnl

        # Stats
        self.sells += 1
        if rpnl > 0:
            self.wins += 1
        if day <= self.is_end:
            self.is_rpnl += rpnl
            self.is_sells += 1
            if rpnl > 0:
                self.is_wins += 1
        else:
            self.oos_rpnl += rpnl
            self.oos_sells += 1
            if rpnl > 0:
                self.oos_wins += 1

        lots_freed = pos.num_lots
        gain_pct = (gross_pnl / basis * 100) if basis else 0.0
        del self.positions[sym]

        self._log({"date": day.isoformat(), "action": "sell", "reason": reason,
                    "symbol": sym, "price": price, "qty": qty,
                    "avg_cost": round(avg, 2), "gain_pct": round(gain_pct, 2),
                    "gross_pnl": round(gross_pnl, 2),
                    "costs": round(total_costs, 2),
                    "realized_pnl_post_cost": round(rpnl, 2),
                    "lots_freed": lots_freed,
                    "lots_open_after": self._lots_open(),
                    "cash_after": round(self.cash, 2)})
        return rpnl

    # ═════════════════════════════════════════════════════════════════
    # DAILY ALGORITHM STEPS (exact order from spec)
    # ═════════════════════════════════════════════════════════════════

    # Step 2 ── Exits ─────────────────────────────────────────────────

    def _step_exits(self, day: date):
        """Sell positions hitting the profit target."""
        cands: list[tuple[str, Position, float]] = []
        for sym, pos in list(self.positions.items()):
            px = self._close_px(sym, day)
            if px is None:
                continue
            threshold = pos.avg_cost * (1 + self.target_pct / 100)
            if px >= threshold:
                cands.append((sym, pos, px))

        if not cands:
            return

        # Tie-break if more candidates than daily sell slots
        if len(cands) > self.max_sells:
            if self.tie_break == "highest_capital_invested":
                cands.sort(key=lambda x: x[1].capital_invested, reverse=True)
            cands = cands[:self.max_sells]

        for sym, _, px in cands:
            self._sell(sym, day, px, "exit_target")

    # Step 3 ── Entries ───────────────────────────────────────────────

    def _step_entries(self, day: date, universe: set[str]) -> int:
        """Buy new positions for stocks below DMA. Returns count of NEW stocks bought."""
        open_lots = self._lots_open()
        if open_lots >= self.max_lots:
            return 0

        max_new = self.max_new_per_day
        throttled = open_lots >= self.throttle_after
        if throttled:
            max_new = min(max_new, self.throttle_to)

        slots = self.max_lots - open_lots

        # Score ALL eligible stocks (held ones stay in ranking per spec)
        scored: list[tuple[str, float, float]] = []
        for sym in universe:
            px  = self._close_px(sym, day)
            sma = self._sma_val(sym, day)
            if px is None or sma is None or sma == 0:
                continue
            pct_below = (sma - px) / sma * 100
            if pct_below >= self.min_pct_below:
                scored.append((sym, pct_below, px))

        if not scored:
            return 0

        # Rank by % below DMA (most below first), take top N
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:self.candidates_n]

        # From top N, buy stocks NOT already held
        bought = 0
        for sym, _pct, px in top:
            if sym in self.positions:
                continue  # already held — not a NEW entry
            if bought >= max_new:
                reason = "throttle_limit" if throttled else "max_new_per_day"
                self._log({"date": day.isoformat(), "action": "skip",
                            "reason": reason, "symbol": sym})
                continue
            if slots <= 0:
                self._log({"date": day.isoformat(), "action": "skip",
                            "reason": "max_lots_cap", "symbol": sym})
                continue
            if self._buy(sym, day, px, "entry"):
                bought += 1
                slots -= 1
            # _buy logs its own skip if affordability/cash fails; try next

        return bought

    # Step 4 ── Averaging ─────────────────────────────────────────────

    def _step_averaging(self, day: date):
        """Average down on held stocks (only called when no new entries)."""
        open_lots = self._lots_open()
        if open_lots >= self.max_lots:
            return

        cands: list[tuple[str, float, float]] = []
        for sym, pos in self.positions.items():
            px = self._close_px(sym, day)
            if px is None:
                continue

            lbp = pos.last_buy_price
            if lbp == 0:
                continue
            drop = (lbp - px) / lbp * 100
            if drop < self.avg_trigger:
                continue

            # Max lots per stock check
            if (self.avg_max_lots is not None
                    and pos.num_lots >= self.avg_max_lots):
                self._log({"date": day.isoformat(), "action": "skip",
                            "reason": "max_lots_per_stock", "symbol": sym,
                            "lots": pos.num_lots, "cap": self.avg_max_lots})
                continue

            # Stop-loss exclusion check
            if self.avg_stop_loss is not None:
                ac = pos.avg_cost
                if ac > 0:
                    drop_avg = (ac - px) / ac * 100
                    if drop_avg >= self.avg_stop_loss:
                        self._log({"date": day.isoformat(), "action": "skip",
                                    "reason": "stop_loss_exclude", "symbol": sym,
                                    "price": px, "avg_cost": round(ac, 2),
                                    "drop_from_avg_pct": round(drop_avg, 2)})
                        continue

            cands.append((sym, drop, px))

        if not cands:
            return

        # Worst performer first (largest drop from last buy)
        cands.sort(key=lambda x: x[1], reverse=True)

        bought = 0
        remaining_slots = self.max_lots - open_lots
        for sym, _drop, px in cands:
            if bought >= self.avg_max_buys or remaining_slots <= 0:
                break
            if self._buy(sym, day, px, "averaging"):
                bought += 1
                remaining_slots -= 1

    # Step 5 ── Corporate actions ─────────────────────────────────────

    def _step_corp(self, day: date):
        """Handle HDFC merger on 2023-07-13."""
        if day != HDFC_MERGER_DATE:
            return
        if "HDFC" not in self.positions:
            return

        handling = self.corp["hdfc_merger_handling"]

        if handling == "force_exit":
            px = self._close_px("HDFC", day)
            if px is None:
                # Fallback: last known close
                for d in sorted(self.close.get("HDFC", {}), reverse=True):
                    if d <= day:
                        px = self.close["HDFC"][d]
                        break
            if px is not None:
                self._sell("HDFC", day, px, "corporate_action_hdfc_merger")
            else:
                self._log({"date": day.isoformat(), "action": "skip",
                            "reason": "hdfc_merger_no_price", "symbol": "HDFC"})

        elif handling == "convert_to_hdfcbank":
            self._log({"date": day.isoformat(), "action": "skip",
                        "reason": "hdfc_conversion_not_implemented",
                        "symbol": "HDFC",
                        "note": "convert_to_hdfcbank requires share-swap logic"})

    # Step 6 ── Annual capital recalculation ──────────────────────────

    def _step_recalc(self, day: date):
        """Recalculate lot size every 365 days from inception."""
        if self.recalc_anchor != "inception":
            return
        if (day - self.last_recalc).days < 365:
            return

        old = self.lot_size
        eff_cap = self.initial_capital + self.cum_rpnl
        self.lot_size = eff_cap / self.divisor
        self.last_recalc = day

        self._log({"date": day.isoformat(), "action": "recalc",
                    "reason": "annual_capital_recalc",
                    "effective_capital": round(eff_cap, 2),
                    "cumulative_rpnl": round(self.cum_rpnl, 2),
                    "old_lot_size": round(old, 2),
                    "new_lot_size": round(self.lot_size, 2)})

    # ═════════════════════════════════════════════════════════════════
    # DAILY SNAPSHOT + BENCHMARK
    # ═════════════════════════════════════════════════════════════════

    def _snapshot(self, day: date):
        """End-of-day portfolio snapshot + drawdown tracking."""
        val = self._pf_value(day)

        # Overall drawdown
        if val > self.peak:
            self.peak = val
        dd = (self.peak - val) / self.peak if self.peak else 0
        self.max_dd = max(self.max_dd, dd)

        # IS / OOS drawdown
        if day <= self.is_end:
            if val > self.is_peak:
                self.is_peak = val
            self.is_dd = max(self.is_dd,
                             (self.is_peak - val) / self.is_peak
                             if self.is_peak else 0)
        else:
            if self.oos_peak is None:
                self.oos_peak = val
            if val > self.oos_peak:
                self.oos_peak = val
            self.oos_dd = max(self.oos_dd,
                              (self.oos_peak - val) / self.oos_peak
                              if self.oos_peak else 0)

        self.daily_vals.append((day, val))

        # Position details
        pos_snap = {}
        for sym, pos in self.positions.items():
            px = self._close_px(sym, day)
            cv = pos.total_qty * (px if px is not None else pos.avg_cost)
            pos_snap[sym] = {
                "qty": pos.total_qty, "lots": pos.num_lots,
                "avg_cost": round(pos.avg_cost, 2),
                "current_price": px,
                "current_value": round(cv, 2),
            }

        with open(self.dlog, "a") as f:
            f.write(json.dumps({
                "date": day.isoformat(),
                "portfolio_value": round(val, 2),
                "cash": round(self.cash, 2),
                "open_lots": self._lots_open(),
                "open_positions": len(self.positions),
                "lot_size": round(self.lot_size, 2),
                "cumulative_rpnl": round(self.cum_rpnl, 2),
                "positions": pos_snap,
            }) + "\n")

    def _update_bnh(self, day: date, universe: set[str]):
        """Track equal-weight synthetic Nifty 50 buy-and-hold return."""
        rets: list[float] = []
        for sym in universe:
            px = self._close_px(sym, day)
            if px is not None and sym in self.bnh_prev:
                rets.append((px - self.bnh_prev[sym]) / self.bnh_prev[sym])
        if rets:
            self.bnh_val *= (1 + sum(rets) / len(rets))
        # Update previous prices for all constituents with data today
        for sym in universe:
            px = self._close_px(sym, day)
            if px is not None:
                self.bnh_prev[sym] = px

    # ═════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═════════════════════════════════════════════════════════════════

    def run(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for p in (self.tlog, self.dlog):
            if p.exists():
                p.unlink()

        print(f"\nBacktest '{self.run_name}'")
        print(f"  {self.bt_start} → {self.bt_end}  (IS end: {self.is_end})")
        print(f"  Capital ₹{self.initial_capital:,.0f}, "
              f"lot ₹{self.lot_size:,.0f}, max {self.max_lots} lots")
        print(f"  {len(self.days)} trading days\n")

        for i, day in enumerate(self.days):
            # Step 1 — build today's eligible universe
            universe = self.pit.on(day)

            # Step 2 — exits (checks ALL held stocks, not just universe)
            self._step_exits(day)

            # Step 3 — entries (new positions from universe)
            new_bought = self._step_entries(day, universe)

            # Step 4 — averaging (only when step 3 bought zero NEW stocks)
            if new_bought == 0:
                self._step_averaging(day)

            # Step 5 — corporate actions
            self._step_corp(day)

            # Step 6 — annual capital recalculation
            self._step_recalc(day)

            # End-of-day snapshot + benchmark
            self._snapshot(day)
            self._update_bnh(day, universe)

            # Progress every 500 days + final
            if (i + 1) % 500 == 0 or i == len(self.days) - 1:
                v = self._pf_value(day)
                print(f"  {i+1}/{len(self.days)} ({day}) "
                      f"₹{v:,.0f}  [{self._lots_open()} lots, "
                      f"{len(self.positions)} pos]")

        self._write_summary()

    # ═════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═════════════════════════════════════════════════════════════════

    def _write_summary(self):
        if not self.daily_vals:
            return

        sv = self.initial_capital
        ev = self.daily_vals[-1][1]
        sd = self.days[0]
        ed = self.days[-1]
        yrs = (ed - sd).days / 365.25

        def _cagr(start_v: float, end_v: float, years: float) -> float:
            if years <= 0 or start_v <= 0:
                return 0.0
            return ((end_v / start_v) ** (1 / years) - 1) * 100

        def _wr(w: int, t: int) -> float:
            return w / t * 100 if t else 0.0

        cagr = _cagr(sv, ev, yrs)
        wr   = _wr(self.wins, self.sells)

        # In-sample
        is_vals = [(d, v) for d, v in self.daily_vals if d <= self.is_end]
        is_ev   = is_vals[-1][1] if is_vals else sv
        is_yrs  = (is_vals[-1][0] - sd).days / 365.25 if is_vals else 0

        # Out-of-sample
        oos_vals = [(d, v) for d, v in self.daily_vals if d > self.is_end]
        oos_sv   = is_ev  # OOS starts where IS portfolio ended
        oos_ev   = oos_vals[-1][1] if oos_vals else oos_sv
        oos_yrs  = ((oos_vals[-1][0] - oos_vals[0][0]).days / 365.25
                    if len(oos_vals) > 1 else 0)

        bnh_cagr = _cagr(sv, self.bnh_val, yrs)

        summary = {
            "run_name": self.run_name,
            "period": {
                "start": sd.isoformat(),
                "end": ed.isoformat(),
                "trading_days": len(self.days),
                "years": round(yrs, 2),
            },
            "returns": {
                "initial_capital": sv,
                "final_value": round(ev, 2),
                "total_return_pct": round((ev / sv - 1) * 100, 2),
                "cagr_pct": round(cagr, 2),
                "max_drawdown_pct": round(self.max_dd * 100, 2),
            },
            "trades": {
                "total_sells": self.sells,
                "winning_sells": self.wins,
                "win_rate_pct": round(wr, 2),
                "cumulative_rpnl": round(self.cum_rpnl, 2),
            },
            "in_sample": {
                "period": f"{sd.isoformat()} → {self.is_end.isoformat()}",
                "final_value": round(is_ev, 2),
                "cagr_pct": round(_cagr(sv, is_ev, is_yrs), 2),
                "max_drawdown_pct": round(self.is_dd * 100, 2),
                "win_rate_pct": round(_wr(self.is_wins, self.is_sells), 2),
                "realized_pnl": round(self.is_rpnl, 2),
            },
            "out_of_sample": {
                "period": f"{self.is_end.isoformat()} → {ed.isoformat()}",
                "final_value": round(oos_ev, 2),
                "cagr_pct": round(_cagr(oos_sv, oos_ev, oos_yrs), 2),
                "max_drawdown_pct": round(self.oos_dd * 100, 2),
                "win_rate_pct": round(_wr(self.oos_wins, self.oos_sells), 2),
                "realized_pnl": round(self.oos_rpnl, 2),
            },
            "buy_and_hold": {
                "method": "equal_weight_daily_rebalanced_nifty50_constituents",
                "final_value": round(self.bnh_val, 2),
                "cagr_pct": round(bnh_cagr, 2),
                "note": "Synthetic equal-weight, rebalanced at constituent "
                        "changes. Not market-cap-weighted like actual Nifty 50.",
            },
            "final_state": {
                "cash": round(self.cash, 2),
                "open_positions": len(self.positions),
                "open_lots": self._lots_open(),
                "lot_size": round(self.lot_size, 2),
            },
            "config": self.cfg,
        }

        self.slog.write_text(json.dumps(summary, indent=2))

        # Console summary
        print(f"\n{'═'*60}")
        print(f"  {self.run_name}")
        print(f"  {sd} → {ed}  ({yrs:.1f}y)")
        print(f"  ₹{sv:,.0f} → ₹{ev:,.0f}")
        print(f"  CAGR {cagr:.2f}%  |  MaxDD {self.max_dd*100:.1f}%  |  "
              f"WR {wr:.0f}% ({self.wins}/{self.sells})")
        print(f"  IS  CAGR {_cagr(sv, is_ev, is_yrs):.2f}%  "
              f"DD {self.is_dd*100:.1f}%  "
              f"WR {_wr(self.is_wins, self.is_sells):.0f}%")
        print(f"  OOS CAGR {_cagr(oos_sv, oos_ev, oos_yrs):.2f}%  "
              f"DD {self.oos_dd*100:.1f}%  "
              f"WR {_wr(self.oos_wins, self.oos_sells):.0f}%")
        print(f"  BnH CAGR {bnh_cagr:.2f}% (equal-weight synthetic)")
        print(f"{'═'*60}")
        print(f"  → {self.run_dir}/")


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

REQUIRED_CONFIG_KEYS = {
    "run_name", "backtest_start", "backtest_end", "in_sample_end",
    "capital", "entry", "averaging", "exit", "corporate_actions", "costs",
}

REQUIRED_CAPITAL = {
    "initial", "divisor", "max_lots", "throttle_after_lots",
    "throttle_to_lots_per_day", "recalc_anchor",
}
REQUIRED_ENTRY = {
    "dma_period", "min_pct_below_dma", "candidates_per_day",
    "max_new_positions_per_day",
}
REQUIRED_AVERAGING = {
    "trigger_pct_from_last_buy", "max_buys_per_day",
    "max_lots_per_stock", "stop_loss_pct_from_avg_cost",
}
REQUIRED_EXIT = {
    "target_pct_above_avg_cost", "max_sells_per_day", "tie_break",
}
REQUIRED_CORP = {"hdfc_merger_handling"}
REQUIRED_COSTS = {
    "brokerage_pct", "stt_pct", "stcg_tax_pct", "ltcg_tax_pct",
    "ltcg_holding_period_days",
}


def validate_config(cfg: dict) -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errs: list[str] = []

    def _check(section_name: str, section: dict | None, required: set[str]):
        if section is None:
            errs.append(f"Missing section: {section_name}")
            return
        for k in required:
            if k not in section:
                errs.append(f"Missing {section_name}.{k}")

    missing_top = REQUIRED_CONFIG_KEYS - set(cfg.keys())
    if missing_top:
        errs.append(f"Missing top-level keys: {missing_top}")

    _check("capital", cfg.get("capital"), REQUIRED_CAPITAL)
    _check("entry", cfg.get("entry"), REQUIRED_ENTRY)
    _check("averaging", cfg.get("averaging"), REQUIRED_AVERAGING)
    _check("exit", cfg.get("exit"), REQUIRED_EXIT)
    _check("corporate_actions", cfg.get("corporate_actions"), REQUIRED_CORP)
    _check("costs", cfg.get("costs"), REQUIRED_COSTS)

    # Date order
    try:
        bs = date.fromisoformat(cfg.get("backtest_start", ""))
        be = date.fromisoformat(cfg.get("backtest_end", ""))
        ie = date.fromisoformat(cfg.get("in_sample_end", ""))
        if bs >= ie:
            errs.append("backtest_start must be before in_sample_end")
        if ie >= be:
            errs.append("in_sample_end must be before backtest_end")
    except (ValueError, TypeError) as e:
        errs.append(f"Date parse error: {e}")

    # Data files
    if not PIT_FILE.exists():
        errs.append(f"Missing {PIT_FILE}")
    ohlcv_files = list(OHLCV_DIR.glob("*.json"))
    ohlcv_data = [f for f in ohlcv_files if not f.name.startswith("_")]
    if not ohlcv_data:
        errs.append(f"No OHLCV files in {OHLCV_DIR}/")

    return errs


def main():
    parser = argparse.ArgumentParser(
        description="NiftyShop — configurable backtest engine",
    )
    parser.add_argument(
        "-c", "--config", default="backtest_config.json",
        help="Path to backtest config JSON (default: backtest_config.json)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate config and data files, then exit without running.",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"Config not found: {cfg_path}\n"
                 f"Copy backtest_config.example.json → {cfg_path} and adjust.")
    cfg = json.loads(cfg_path.read_text())

    errs = validate_config(cfg)
    if errs:
        print("Config validation errors:")
        for e in errs:
            print(f"  ✗ {e}")
        sys.exit(1)

    if args.validate:
        print("Config and data validated OK.")
        return

    engine = BacktestEngine(cfg)
    engine.run()


if __name__ == "__main__":
    main()
