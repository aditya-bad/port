"""Web Push sending (app/notifications.py) with a fake push service:
a no-op without VAPID keys, every device gets it, gone devices are
removed, and one failing device never stops the rest."""

import json

import pytest

import app.notifications as N
from app.db import queries

VAPID = {"vapid_public_key": "pub", "vapid_private_key": "priv", "vapid_subject": "mailto:a@b.c"}


def send():
    return getattr(N, "real_send_push_for_all", N.send_push_for_all)


@pytest.fixture
def push_service(monkeypatch):
    import pywebpush
    sent, fail = [], {}

    class Resp:
        def __init__(self, code):
            self.status_code = code

    def webpush(subscription_info, data, vapid_private_key, vapid_claims):
        ep = subscription_info["endpoint"]
        vapid_claims["aud"] = ep            # the real one mutates its claims
        if ep in fail:
            code = fail[ep]
            if code == "boom":
                raise RuntimeError("network")
            raise pywebpush.WebPushException("gone", response=Resp(code))
        sent.append((ep, json.loads(data)))
    monkeypatch.setattr(pywebpush, "webpush", webpush)
    return sent, fail


def test_is_push_configured():
    assert N.is_push_configured(VAPID)
    assert not N.is_push_configured({**VAPID, "vapid_subject": ""})
    assert not N.is_push_configured({})


def test_no_vapid_is_a_silent_no_op(pool_run, push_service):
    sent, _ = push_service

    async def go(pool):
        await queries.save_push_subscription(pool, "https://p/1", "k", "a")
        await send()(pool, {}, "t", "b")
    pool_run(go)
    assert sent == []


def test_every_device_gets_it_and_gone_ones_are_removed(pool_run, push_service):
    sent, fail = push_service
    fail.update({"https://p/gone": 410, "https://p/missing": 404, "https://p/flaky": 500, "https://p/err": "boom"})

    async def go(pool):
        for ep in ("https://p/1", "https://p/gone", "https://p/missing", "https://p/flaky", "https://p/err", "https://p/2"):
            await queries.save_push_subscription(pool, ep, "k", "a")
        await send()(pool, VAPID, "Entry", "NIFTY short straddle")
        return sorted(r["endpoint"] for r in await queries.list_push_subscriptions(pool))
    left = pool_run(go)
    assert sorted(ep for ep, _ in sent) == ["https://p/1", "https://p/2"]
    assert sent[0][1] == {"title": "Entry", "body": "NIFTY short straddle"}
    assert left == ["https://p/1", "https://p/2", "https://p/err", "https://p/flaky"], "only gone devices are removed"
