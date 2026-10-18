"""Config loading, the aggregate cache, and the broadcaster."""

import asyncio
import json

import pytest

from app import config as C
from app.broadcaster import Broadcaster
from app.cache import AggregateCache
from tests.conftest import _real_load_config as load_config


# ── Config ────────────────────────────────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch):
    for k in ("KITE_API_KEY", "KITE_API_SECRET", "DATABASE_URL", "APP_AUTH_SECRET", "LOCAL_DATABASE_URL",
              "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def write(tmp_path, **cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return p


FULL = {"api_key": "k", "api_secret": "s", "database_url": "postgresql://x/db", "app_auth_secret": "a"}


def test_config_file_alone(clean_env, tmp_path):
    cfg = load_config(write(tmp_path, **FULL))
    assert cfg["api_key"] == "k" and cfg["tick_mode"] == "full"


def test_env_overrides_the_file(clean_env, tmp_path):
    clean_env.setenv("KITE_API_KEY", "envkey")
    assert load_config(write(tmp_path, **FULL))["api_key"] == "envkey"


def test_env_alone_without_a_file(clean_env, tmp_path):
    for k, env in C.ENV_VAR_FOR_KEY.items():
        clean_env.setenv(env, FULL[k])
    assert load_config(tmp_path / "missing.json")["database_url"] == "postgresql://x/db"


def test_local_database_url_always_wins(clean_env, tmp_path):
    clean_env.setenv("LOCAL_DATABASE_URL", "postgresql://local/db")
    clean_env.setenv("DATABASE_URL", "postgresql://remote/db")
    assert load_config(write(tmp_path, **FULL))["database_url"] == "postgresql://local/db"


def test_missing_keys_are_named(clean_env, tmp_path):
    with pytest.raises(RuntimeError, match="app_auth_secret"):
        load_config(write(tmp_path, api_key="k", api_secret="s", database_url="d"))
    with pytest.raises(RuntimeError, match="Config not found"):
        load_config(tmp_path / "missing.json")


def test_bad_tick_mode(clean_env, tmp_path):
    with pytest.raises(RuntimeError, match="tick_mode"):
        load_config(write(tmp_path, **FULL, tick_mode="turbo"))


# ── Cache ─────────────────────────────────────────────────────────────

def test_cache_cold_get_refresh_now_and_keeps_last_good_value():
    async def go():
        calls = {"n": 0, "fail": False}

        async def fetch():
            calls["n"] += 1
            if calls["fail"]:
                raise RuntimeError("db down")
            return calls["n"]
        c = AggregateCache()
        c.register("k", fetch, interval=60)
        assert c.age("k") is None
        assert await c.get("k") == 1, "cold get fetches inline"
        assert await c.get("k") == 1, "then serves memory"
        await c.refresh_now("k")
        assert await c.get("k") == 2
        calls["fail"] = True
        await c.refresh_now("k")
        assert await c.get("k") == 2, "a failed refresh keeps the last value"
        assert c.age("k") is not None
    asyncio.run(go())


def test_cache_loop_refreshes_and_stops():
    async def go():
        n = {"v": 0}

        async def fetch():
            n["v"] += 1
            return n["v"]
        c = AggregateCache()
        c.register("k", fetch, interval=0.02)
        await c.start()
        await asyncio.sleep(0.12)
        await c.stop()
        seen = n["v"]
        assert seen >= 3
        await asyncio.sleep(0.05)
        assert n["v"] == seen, "stopped"
    asyncio.run(go())


# ── Broadcaster ───────────────────────────────────────────────────────

def test_broadcast_fans_out_and_drops_oldest_for_slow_consumers():
    async def go():
        b = Broadcaster(max_queue_size=2)
        fast, slow = await b.subscribe(), await b.subscribe()
        assert b.subscriber_count == 2
        for i in range(3):
            await b.broadcast(i)
            if i < 2:
                assert await fast.get() == i
        assert [slow.get_nowait(), slow.get_nowait()] == [1, 2], "oldest dropped"
        assert b.drops == 1 and b.messages_broadcast == 3
        await b.unsubscribe(slow)
        await b.broadcast("x")
        assert slow.empty() and b.subscriber_count == 1
    asyncio.run(go())
