"""
live_deploy — config loading.

Standalone: no imports from the rest of the `port` repo, or from any
other folder in it (tg_int_st_pp, generic, etc).
"""

import json
import os
import sys
from pathlib import Path

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = LIVE_DEPLOY_DIR / "config.json"
TOKENS_PATH = LIVE_DEPLOY_DIR / "tokens.json"

REQUIRED_CONFIG_KEYS = ("api_key", "api_secret", "database_url", "app_auth_secret")
VALID_TICK_MODES = ("ltp", "quote", "full")

# Environment-variable equivalents for each required credential — see
# RUN_GUIDE.md's "Credential hardening" section for the full reasoning.
# Checked FIRST, config.json is the fallback per-key, not the other way
# round: a value sitting as plaintext in a file on disk is strictly more
# exposed than one held only in the process's own environment (readable
# by anything that can read the file + anything with that permission,
# vs. readable only by whatever can already inspect this specific
# process) — env vars are the recommended path for anything actually
# server-hosted, config.json stays for local-dev convenience. This is
# ADDITIVE: config.json alone, exactly as before, still works unchanged
# for every key an env var doesn't override.
ENV_VAR_FOR_KEY = {
    "api_key": "KITE_API_KEY",
    "api_secret": "KITE_API_SECRET",
    "database_url": "DATABASE_URL",
    "app_auth_secret": "APP_AUTH_SECRET",
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    """
    Load Kite credentials + service settings — environment variables
    first (KITE_API_KEY, KITE_API_SECRET, DATABASE_URL,
    APP_AUTH_SECRET), config.json as the fallback for whichever of those
    aren't set as env vars. config.json itself is now fully OPTIONAL —
    it doesn't even need to exist — PROVIDED every key in
    REQUIRED_CONFIG_KEYS ends up covered by an env var; if it doesn't
    exist and env vars don't cover everything, the same kind of clear
    RuntimeError as before is raised, just naming both ways to supply
    whatever's still missing.

    access_token is deliberately NOT part of this env-var scheme (and
    NOT required here at all) — it expires daily, so the DB
    (kite_sessions table, updated via the /kite/login-url +
    /kite/callback flow, or the manual-entry alternative — see
    routers/kite_auth.py) is the primary source of truth for it. A value
    in config.json is honored only as a one-time bootstrap fallback if
    the DB doesn't have one yet (see main.py's startup).

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
    cfg: dict = {}
    if path.exists():
        cfg = json.loads(path.read_text())

    for cfg_key, env_name in ENV_VAR_FOR_KEY.items():
        env_val = os.environ.get(env_name)
        if env_val:
            cfg[cfg_key] = env_val

    # LOCAL DB OVERRIDE — set LOCAL_DATABASE_URL to force this app onto a
    # local database UNCONDITIONALLY, no matter what database_url/
    # DATABASE_URL above resolved to (or didn't). This exists for
    # exactly one purpose: migrating off a remote-hosted DB onto one
    # running alongside this app's own server, without having to hunt
    # down and edit every place the old (remote) connection string might
    # still be sitting around — a stale config.json that didn't get
    # updated, a leftover DATABASE_URL env var in a deploy script, a
    # config.json that gets regenerated from an old template on every
    # redeploy. Checked here, BEFORE the required-keys validation below
    # (not after it) — so LOCAL_DATABASE_URL alone is enough to satisfy
    # database_url even if nothing else supplies one at all, not just an
    # override on top of an otherwise-valid value. As long as
    # LOCAL_DATABASE_URL is set in the environment, THIS wins, always.
    # Unset it entirely to go back to normal database_url resolution —
    # this is a deliberate, visible override, not a permanent rewrite of
    # how database_url normally works.
    local_override = os.environ.get("LOCAL_DATABASE_URL")
    if local_override:
        if cfg.get("database_url") and cfg["database_url"] != local_override:
            print(
                "live_deploy: LOCAL_DATABASE_URL is set — overriding "
                "database_url (which was pointing elsewhere) to use the "
                "local database instead.",
                file=sys.stderr,
            )
        cfg["database_url"] = local_override

    missing = [k for k in REQUIRED_CONFIG_KEYS if not cfg.get(k)]
    if missing:
        missing_desc = ", ".join(f"{k} (or ${ENV_VAR_FOR_KEY[k]})" for k in missing)
        if not path.exists():
            raise RuntimeError(
                f"Config not found: {path}, and the following required "
                f"value(s) were not supplied via environment variable "
                f"either: {missing_desc}.\n"
                f"Either copy config.example.json -> {path.name} and fill "
                f"in your Kite Connect credentials, or set the "
                f"corresponding environment variable(s) instead — see "
                f"RUN_GUIDE.md's 'Credential hardening' section."
            )
        raise RuntimeError(f"Config missing: {missing_desc}")

    cfg.setdefault("tick_mode", "full")
    if cfg["tick_mode"] not in VALID_TICK_MODES:
        raise RuntimeError(
            f"Invalid tick_mode: {cfg['tick_mode']!r}. "
            f"Choose from {VALID_TICK_MODES}"
        )

    # Web Push (mobile notifications) — entirely OPTIONAL, unlike
    # REQUIRED_CONFIG_KEYS above: the app runs completely normally with
    # none of these set, it just means push notifications are silently
    # disabled (see app/notifications.py's own is_push_configured) —
    # this is a feature toggle, not a credential the service refuses to
    # start without. Same env-var-first, config.json-fallback pattern as
    # ENV_VAR_FOR_KEY above, generated once via
    # custom_scripts/generate_vapid_keys.py and then kept STABLE forever
    # after — regenerating them invalidates every existing subscriber's
    # push endpoint, silently turning notifications off for everyone
    # already opted in until they re-subscribe.
    for cfg_key, env_name in (
        ("vapid_public_key", "VAPID_PUBLIC_KEY"),
        ("vapid_private_key", "VAPID_PRIVATE_KEY"),
        ("vapid_subject", "VAPID_SUBJECT"),
    ):
        env_val = os.environ.get(env_name)
        if env_val:
            cfg[cfg_key] = env_val

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
