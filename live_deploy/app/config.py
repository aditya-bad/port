"""
live_deploy — config loading.

Standalone: no imports from the rest of the `port` repo, or from any
other folder in it (tg_int_st_pp, generic, etc).
"""

import json
from pathlib import Path

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = LIVE_DEPLOY_DIR / "config.json"
TOKENS_PATH = LIVE_DEPLOY_DIR / "tokens.json"

REQUIRED_CONFIG_KEYS = ("api_key", "api_secret", "database_url", "app_auth_secret")
VALID_TICK_MODES = ("ltp", "quote", "full")


def load_config(path: Path = CONFIG_PATH) -> dict:
    """
    Load Kite credentials + service settings.

    access_token is deliberately NOT required here — it expires daily,
    so the DB (kite_sessions table, updated via the /kite/login-url +
    /kite/callback flow) is the primary source of truth for it. A value
    in config.json is honored only as a one-time bootstrap fallback if
    the DB doesn't have one yet (see main.py's startup). api_key/
    api_secret are the actual long-lived app credentials and stay
    required.

    app_auth_secret is unrelated to Kite entirely — it's THIS app's own
    front door (see app/auth.py): a password you set yourself, doubling
    as this app's session-cookie signing key. Required, not optional —
    "protect everything by default" only holds if the service refuses
    to start without something to protect it with, rather than falling
    back to running unauthenticated.

    Raises RuntimeError with a clear message on any problem — this runs
    at import time / inside a FastAPI startup event, where a bare
    sys.exit() doesn't shut the ASGI server down cleanly, so a normal
    exception is used instead.
    """
    if not path.exists():
        raise RuntimeError(
            f"Config not found: {path}\n"
            f"Copy config.example.json -> {path.name} and fill in your "
            f"Kite Connect credentials."
        )
    cfg = json.loads(path.read_text())
    missing = [k for k in REQUIRED_CONFIG_KEYS if not cfg.get(k)]
    if missing:
        raise RuntimeError(f"Config missing: {', '.join(missing)}")

    cfg.setdefault("tick_mode", "full")
    if cfg["tick_mode"] not in VALID_TICK_MODES:
        raise RuntimeError(
            f"Invalid tick_mode: {cfg['tick_mode']!r}. "
            f"Choose from {VALID_TICK_MODES}"
        )
    return cfg


def load_tokens(path: Path = TOKENS_PATH) -> list[dict]:
    """
    Load the list of instrument tokens this dispatcher subscribes to on
    Kite's behalf, e.g. [{"symbol": "NIFTY 50", "instrument_token": 256265}].
    """
    if not path.exists():
        raise RuntimeError(
            f"Tokens file not found: {path}\n"
            f"Create {path.name} — a JSON array of "
            f'{{"symbol": ..., "instrument_token": ...}} entries.'
        )
    tokens = json.loads(path.read_text())
    if not isinstance(tokens, list) or not tokens:
        raise RuntimeError(
            f"{path}: expected a non-empty JSON array of "
            f"{{symbol, instrument_token}} entries"
        )
    for t in tokens:
        if "instrument_token" not in t:
            raise RuntimeError(f"{path}: entry missing instrument_token: {t}")
    return tokens
