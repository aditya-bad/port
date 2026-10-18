"""Shared test setup.

Three layers, each a folder:
  unit/  pure logic, no database (always runs)
  db/    real Postgres: migrations, the fill engine, queries, v2 builders
  api/   the real FastAPI app (startup, middleware, every router) on that
         same database, through an HTTP client

Database: TEST_DATABASE_URL (default postgresql://t:t@localhost/t_test).
Its name must contain "test" — the suite drops and recreates the public
schema, so it refuses anything else. Without a reachable test database
the db/ and api/ tests are skipped, and the deploy gate (run_tests.sh,
which passes --require-db) fails instead.

Safety: config.json is ignored, DATABASE_URL and LOCAL_DATABASE_URL both
point at the test database, push notifications are captured (never
sent), and no Kite session exists, so nothing reaches Kite.
"""

import asyncio
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://t:t@localhost/t_test")
_db_name = urlparse(TEST_DB).path.lstrip("/")
if "test" not in _db_name:
    raise SystemExit(f"TEST_DATABASE_URL must name a database containing 'test' (got {_db_name!r}).")

# Kite tick timestamps are naive local time and the app treats local as
# IST (as on the server): run the suite in IST so fill times are right.
os.environ["TZ"] = "Asia/Kolkata"
time.tzset()

os.environ["DATABASE_URL"] = TEST_DB
os.environ["LOCAL_DATABASE_URL"] = TEST_DB   # overrides DATABASE_URL in load_config: must match
os.environ["APP_AUTH_SECRET"] = ADMIN_PASSWORD = "test-secret-123"
os.environ["KITE_API_KEY"] = "test-key"
os.environ["KITE_API_SECRET"] = "test-secret"

import app.config as _config   # noqa: E402

_real_load_config = _config.load_config
_config.load_config = lambda path=None: _real_load_config(Path("/nonexistent/config.json"))


def pytest_addoption(parser):
    parser.addoption("--require-db", action="store_true",
                     help="fail (instead of skip) when the test database is unreachable")


def run(coro):
    return asyncio.run(coro)


# ── Database ──────────────────────────────────────────────────────────

_db_state = {"checked": False, "error": None}

# Tables kept between tests: the schema itself, the admin user, and the
# per-strategy enable flags the app seeds on startup.
_KEEP = {"schema_migrations", "users", "strategy_settings"}


async def _reset_schema():
    import asyncpg
    from app.db.migrate import run_migrations
    conn = await asyncpg.connect(TEST_DB)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()
    from app.db.pool import close_pool, create_pool
    pool = await create_pool(TEST_DB)
    try:
        await run_migrations(pool)
    finally:
        await close_pool(pool)


def _ensure_db(request):
    if not _db_state["checked"]:
        _db_state["checked"] = True
        try:
            run(_reset_schema())
        except Exception as e:   # unreachable, missing, no rights
            _db_state["error"] = f"{type(e).__name__}: {e}"
    if _db_state["error"]:
        msg = (f"test database unavailable ({_db_state['error']}). Create it, e.g. "
               f"`createdb -O <user> {_db_name}`, or set TEST_DATABASE_URL.")
        if request.config.getoption("--require-db"):
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)


async def truncate_all():
    import asyncpg
    conn = await asyncpg.connect(TEST_DB)
    try:
        tables = [r["tablename"] for r in await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        wipe = [t for t in tables if t not in _KEEP]
        if wipe:
            await conn.execute(f"TRUNCATE {', '.join(wipe)} RESTART IDENTITY CASCADE")
        await conn.execute("DELETE FROM users WHERE username <> 'admin'")
    finally:
        await conn.close()


@pytest.fixture
def db(request):
    """A clean database URL for this test (migrated, domain tables empty)."""
    _ensure_db(request)
    run(truncate_all())
    return TEST_DB


@pytest.fixture
def pool_run(db):
    """pool_run(async_fn) -> runs async_fn(pool) on a fresh pool to the test DB."""
    from app.db.pool import close_pool, create_pool

    def _run(fn):
        async def go():
            pool = await create_pool(db)
            try:
                return await fn(pool)
            finally:
                await close_pool(pool)
        return run(go())
    return _run


# ── The real app ──────────────────────────────────────────────────────

_app_state = {"client": None, "pushes": None}


def _start_app():
    from fastapi.testclient import TestClient
    import app.notifications as notifications
    pushes = []

    async def fake_push(pool, config, title, body):
        pushes.append((title, body))
    if not hasattr(notifications, "real_send_push_for_all"):
        notifications.real_send_push_for_all = notifications.send_push_for_all   # for its own tests
    notifications.send_push_for_all = fake_push
    from app.main import app
    app.state.test_pushes = pushes
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()   # runs startup once for the whole session
    _app_state["client"] = client
    _app_state["pushes"] = pushes
    return client


def pytest_sessionfinish(session, exitstatus):
    c = _app_state["client"]
    if c is not None:
        try:
            c.__exit__(None, None, None)
        except Exception:
            pass


_hash_cache = {}


def _admin_hash():
    if "h" not in _hash_cache:   # bcrypt is slow on purpose: hash once per run
        from app.routers.auth import hash_password
        _hash_cache["h"] = hash_password(ADMIN_PASSWORD)
    return _hash_cache["h"]


@pytest.fixture
def app_client(db):
    """The running app (anonymous client). Before each test: runners
    stopped, domain tables emptied, admin password reset, caches
    refreshed, captured pushes cleared."""
    client = _app_state["client"] or _start_app()
    app = client.app
    mgr = app.state.deployment_manager

    async def reset():
        await mgr.shutdown_all()
        mgr.runners.clear()
        mgr.start_errors.clear()
        mgr.health_issues = []
        mgr._alerted.clear()
        app.state.v2_book = None
        app.state.dispatcher.last_prices.clear()
        await truncate_all()
        await app.state.db_pool.execute("UPDATE strategy_settings SET enabled = true")
        await app.state.db_pool.execute(
            "UPDATE users SET password_hash = $1, is_active = true WHERE username = 'admin'",
            _admin_hash(),
        )
        for key in list(app.state.cache._entries):
            try:
                await app.state.cache.refresh_now(key)
            except Exception:
                pass
    client.portal.call(reset)
    client.cookies.clear()
    _app_state["pushes"].clear()
    return client


@pytest.fixture
def client(app_client):
    """The running app, logged in as admin."""
    r = app_client.post("/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return app_client


@pytest.fixture
def pushes(app_client):
    return _app_state["pushes"]


def sql(client, query, *args):
    """Run SQL on the app's own pool (seeding snapshots, events...)."""
    pool = client.app.state.db_pool
    return client.portal.call(lambda: pool.fetch(query, *args))


def call_in_app(client, fn, *args):
    """Run an async function inside the app's event loop (e.g. to drive
    the manager or read app.state)."""
    return client.portal.call(fn, *args)


# ── Factories ─────────────────────────────────────────────────────────

def default_config(strategy):
    """The strategy's own default config (what the deploy form starts from)."""
    import copy
    from app.strategies.registry import list_strategies
    return copy.deepcopy(next(s for s in list_strategies() if s["name"] == strategy)["default_config"])


def make_deployment(client, name="Test Deployment", strategy="intraday_dtt_simple", mode="intraday",
                    capital=500000, config=None, start="paused", **extra):
    """Creates a deployment through the v2 API (paused by default so no
    runner trades during a test). Returns its JSON."""
    body = {"deployment_name": name, "strategy_name": strategy, "mode": mode,
            "initial_capital": capital, "config": config if config is not None else default_config(strategy),
            "start": start, **extra}
    r = client.post("/v2/deployments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def fill(pool, dep_id, symbol, token, action, qty, price, at, reason=None, metadata=None):
    from app.db import queries
    return await queries.record_fill(pool, dep_id, symbol, token, action, qty, price, at,
                                     reason=reason, metadata=metadata or {})


def add_fill(client, dep_id, symbol, token, action, qty, price, at, reason=None, metadata=None):
    """Record a fill through the app's own pool, then refresh its caches."""
    from uuid import UUID
    pool = client.app.state.db_pool
    r = client.portal.call(lambda: fill(pool, UUID(str(dep_id)), symbol, token, action, qty, price, at, reason, metadata))
    refresh(client)
    return r


def refresh(client):
    cache = client.app.state.cache
    client.app.state.v2_book = None       # the v2 book's own 15s cache

    async def go():
        for key in list(cache._entries):
            try:
                await cache.refresh_now(key)
            except Exception:
                pass
    client.portal.call(go)
