#!/usr/bin/env python3
"""
Generic backtest runner — works with any user-defined universe.

Imports and reuses BacktestEngine's full trading logic (entry, averaging,
exit, position sizing, cost model, corporate actions, snapshots, summary)
from the Nifty-specific backtest.py.  Subclasses only to redirect data
paths and to support static-list universes (Mode 1) alongside point-in-time
interval universes (Mode 2).

Usage:
    python -m generic.backtest_generic --universe universes/midcapshop
    python -m generic.backtest_generic --universe universes/midcapshop -c custom.json
    python -m generic.backtest_generic --universe universes/midcapshop --validate
"""

import json
import sys
import argparse
from datetime import date
from pathlib import Path

# ── Import the engine + supporting classes from the Nifty backtest ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (
    BacktestEngine,
    PITUniverse,
    Position,
    Lot,
    validate_config,
    REQUIRED_CONFIG_KEYS,
)

GENERIC_DIR = Path(__file__).resolve().parent


# ═════════════════════════════════════════════════════════════════════
# STATIC UNIVERSE (Mode 1) — every symbol eligible every day
# ═════════════════════════════════════════════════════════════════════

class StaticUniverse:
    """All symbols in the list are active for the entire backtest window."""

    def __init__(self, symbols: set[str], excluded: set[str]):
        self._symbols = symbols - excluded

    def on(self, day: date) -> set[str]:
        return self._symbols


# ═════════════════════════════════════════════════════════════════════
# GENERIC ENGINE — subclass with redirected paths + universe logic
# ═════════════════════════════════════════════════════════════════════

class GenericBacktestEngine(BacktestEngine):
    """
    Thin subclass that redirects data paths to the universe folder and
    supports both static-list (Mode 1) and PIT-interval (Mode 2) universes.

    All trading logic (entry, averaging, exit, position sizing, costs,
    corporate actions, snapshots, summary writing) is inherited as-is.
    """

    def __init__(self, config: dict, universe_dir: Path):
        self._universe_dir = universe_dir.resolve()
        self._data_dir = self._universe_dir / "data"
        self._ohlcv_dir = self._data_dir / "ohlcv"
        self._runs_dir = self._data_dir / "runs"
        self._fetch_log_file = self._ohlcv_dir / "_fetch_log.json"

        # Detect universe mode before calling super().__init__
        self._universe_mode = self._detect_universe_mode()

        # super().__init__ calls self._load() — we override it below
        super().__init__(config)

        # Override output paths (super set them from Nifty's RUNS_DIR)
        self.run_dir = self._runs_dir / self.run_name
        self.tlog = self.run_dir / "trade_log.jsonl"
        self.dlog = self.run_dir / "daily_portfolio.jsonl"
        self.slog = self.run_dir / "summary.json"

    def _detect_universe_mode(self) -> str:
        """Detect whether this universe uses Mode 1 (static) or Mode 2 (PIT)."""
        pit_file = self._universe_dir / "universe_input.json"
        if pit_file.exists():
            try:
                raw = json.loads(pit_file.read_text())
                if (isinstance(raw, list) and raw
                        and isinstance(raw[0], dict)
                        and "symbol" in raw[0]
                        and "start_date" in raw[0]):
                    return "pit"
            except (json.JSONDecodeError, KeyError):
                pass
        return "static"

    def _load_universe_symbols(self) -> set[str]:
        """Load static universe from universe_input.{txt|csv|json}."""
        # Reuse the loader from fetch_ohlcv_generic
        from generic.fetch_ohlcv_generic import load_universe_symbols
        return set(load_universe_symbols(self._universe_dir))

    def _load(self):
        """
        Override BacktestEngine._load() to read from the universe's
        data directory instead of the hardcoded Nifty paths.
        """
        # Excluded symbols from fetch log
        excluded: set[str] = set()
        if self._fetch_log_file.exists():
            fl = json.loads(self._fetch_log_file.read_text())
            for sym, info in fl.get("symbols", {}).items():
                if info.get("status") == "no_kite_data":
                    excluded.add(sym)
        self._excluded = excluded

        # Universe — Mode 1 (static) or Mode 2 (PIT intervals)
        if self._universe_mode == "pit":
            pit_path = self._universe_dir / "universe_input.json"
            pit_raw = json.loads(pit_path.read_text())
            self.pit = PITUniverse(pit_raw, excluded)
        else:
            all_symbols = self._load_universe_symbols()
            self.pit = StaticUniverse(all_symbols, excluded)

        # OHLCV data
        self.close: dict[str, dict[date, float]] = {}
        for p in sorted(self._ohlcv_dir.glob("*.json")):
            if p.name.startswith("_"):
                continue
            sym = p.stem
            candles = json.loads(p.read_text())
            self.close[sym] = {
                date.fromisoformat(c["date"]): c["close"] for c in candles
            }

        if not self.close:
            sys.exit(f"No OHLCV files in {self._ohlcv_dir}/. "
                     f"Run fetch_ohlcv_generic.py first.")

        # Trading calendar
        all_dates: set[date] = set()
        for d_map in self.close.values():
            all_dates.update(d_map)
        self.days = sorted(d for d in all_dates
                           if self.bt_start <= d <= self.bt_end)
        if not self.days:
            sys.exit(f"No trading days in [{self.bt_start}, {self.bt_end}].")

        # Precompute SMAs (inherited method, works on self.close)
        self._build_sma()

        mode_label = "PIT intervals (Mode 2)" if self._universe_mode == "pit" \
            else "static list (Mode 1)"
        print(f"Loaded {len(self.close)} symbols, {len(self.days)} trading days "
              f"({self.days[0]}…{self.days[-1]})")
        print(f"Universe mode: {mode_label}")
        if excluded:
            print(f"Excluded (no_kite_data): {sorted(excluded)}")


# ═════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION (augmented for generic — same schema, relaxed paths)
# ═════════════════════════════════════════════════════════════════════

def validate_generic_config(cfg: dict, universe_dir: Path) -> list[str]:
    """
    Validate config + universe data.  Same schema as Nifty's config;
    only the file-existence checks point at the universe folder.
    """
    errs: list[str] = []

    # Reuse the schema validation (keys, sections, date ordering)
    from backtest import (
        REQUIRED_CAPITAL, REQUIRED_ENTRY, REQUIRED_AVERAGING,
        REQUIRED_EXIT, REQUIRED_CORP, REQUIRED_COSTS,
    )

    def _check(section_name, section, required):
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

    # Universe-specific data checks
    ohlcv_dir = universe_dir / "data" / "ohlcv"
    ohlcv_files = list(ohlcv_dir.glob("*.json")) if ohlcv_dir.exists() else []
    ohlcv_data = [f for f in ohlcv_files if not f.name.startswith("_")]
    if not ohlcv_data:
        errs.append(f"No OHLCV files in {ohlcv_dir}/")

    # Check universe input exists
    universe_inputs = list(universe_dir.glob("universe_input.*"))
    if not universe_inputs:
        errs.append(f"No universe_input.* file in {universe_dir}/")

    return errs


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generic backtest runner — any universe",
    )
    parser.add_argument(
        "--universe", required=True,
        help="Path to universe directory (e.g. universes/midcapshop)",
    )
    parser.add_argument(
        "-c", "--config", default=None,
        help="Path to backtest config JSON. Default: universes/{name}/configs/backtest_config.json",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate config and data, then exit.",
    )
    args = parser.parse_args()

    # Resolve universe path
    universe_dir = Path(args.universe)
    if not universe_dir.is_absolute():
        universe_dir = GENERIC_DIR / universe_dir
    universe_dir = universe_dir.resolve()

    if not universe_dir.is_dir():
        sys.exit(f"Universe directory not found: {universe_dir}")

    # Config path
    if args.config:
        cfg_path = Path(args.config)
    else:
        cfg_path = universe_dir / "configs" / "backtest_config.json"

    if not cfg_path.exists():
        sys.exit(
            f"Config not found: {cfg_path}\n"
            f"Create it in {universe_dir / 'configs'}/ "
            f"(same schema as backtest_config.example.json)."
        )
    cfg = json.loads(cfg_path.read_text())

    # Validate
    errs = validate_generic_config(cfg, universe_dir)
    if errs:
        print("Config validation errors:")
        for e in errs:
            print(f"  ✗ {e}")
        sys.exit(1)

    if args.validate:
        print("Config and data validated OK.")
        return

    # Run
    engine = GenericBacktestEngine(cfg, universe_dir)
    engine.run()


if __name__ == "__main__":
    main()
