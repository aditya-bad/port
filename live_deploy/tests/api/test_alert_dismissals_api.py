"""Alert dismissals (app/v2/alerts.py): kept per user on the server, so a
dismissal on one device holds on every other, and the other open devices
are told at once (an alerts_dismissed event)."""

from tests.conftest import ADMIN_PASSWORD


class device:
    """Another browser on the same running app: its own session cookie.
    Use as `with device(client) as d: d.get(...)`; the client's own
    cookies are back afterwards."""

    def __init__(self, client, username="admin", password=ADMIN_PASSWORD):
        self.c, self.username, self.password, self.jar = client, username, password, None

    def _set(self, cookies):
        self.c.cookies.clear()
        for ck in cookies:
            self.c.cookies.jar.set_cookie(ck)

    def __enter__(self):
        self.mine = list(self.c.cookies.jar)
        self.c.cookies.clear()
        if self.jar is None:
            r = self.c.post("/auth/login", json={"username": self.username, "password": self.password})
            assert r.status_code == 200, r.text
            self.jar = list(self.c.cookies.jar)
        else:
            self._set(self.jar)
        return self.c

    def __exit__(self, *exc):
        self._set(self.mine)


def test_a_dismissal_on_one_device_holds_on_another(client):
    phone = device(client)
    with phone as p:
        assert p.get("/v2/alerts/dismissed").json() == {}
    r = client.post("/v2/alerts/dismissed", json={"items": [{"key": "streak:abc", "token": "5"}, {"key": "kite", "token": "expired:2026-10-23"}]})
    assert r.json() == {"streak:abc": "5", "kite": "expired:2026-10-23"}
    with phone as p:
        assert p.get("/v2/alerts/dismissed").json() == {"streak:abc": "5", "kite": "expired:2026-10-23"}, "the other device sees it"
        p.post("/v2/alerts/dismissed", json={"restore": ["kite"]})
    assert client.get("/v2/alerts/dismissed").json() == {"streak:abc": "5"}, "and brought back there, it's back here"


def test_dismissing_again_keeps_the_newest_token_and_bring_back_removes_it(client):
    client.post("/v2/alerts/dismissed", json={"items": [{"key": "streak:abc", "token": "5"}]})
    r = client.post("/v2/alerts/dismissed", json={"items": [{"key": "streak:abc", "token": "6"}]})
    assert r.json() == {"streak:abc": "6"}, "a longer streak dismissed again: the new length"
    r = client.post("/v2/alerts/dismissed", json={"restore": ["streak:abc"]})
    assert r.json() == {}


def test_dismissals_are_per_user(client):
    client.post("/v2/alerts/dismissed", json={"items": [{"key": "kite", "token": "expired:2026-10-23"}]})
    r = client.post("/auth/users", json={"username": "riya", "password": "riya-pass-123"}); assert r.status_code < 300, r.text
    with device(client, "riya", "riya-pass-123") as riya:
        assert riya.get("/v2/alerts/dismissed").json() == {}


def test_bad_items_are_dropped_and_a_user_is_needed(client):
    r = client.post("/v2/alerts/dismissed", json={"items": [{"key": "", "token": "x"}, {"key": "k" * 161, "token": "x"},
                                                         {"key": "ok", "token": 5}, "nope", {"key": "fine", "token": ""}]})
    assert r.json() == {"fine": ""}
    client.cookies.clear()   # no session: the API key alone has no user
    h = {"X-API-Key": ADMIN_PASSWORD}
    assert client.get("/v2/alerts/dismissed", headers=h).json() == {}
    assert client.post("/v2/alerts/dismissed", json={"items": [{"key": "a", "token": "b"}]}, headers=h).status_code == 400


def test_old_dismissals_are_dropped(client):
    from tests.conftest import sql
    client.post("/v2/alerts/dismissed", json={"items": [{"key": "old", "token": "1"}, {"key": "new", "token": "1"}]})
    sql(client, "UPDATE alert_dismissals SET dismissed_at = now() - interval '31 days' WHERE key = 'old'")
    assert client.get("/v2/alerts/dismissed").json() == {"new": "1"}


def test_other_devices_are_told_at_once(client, monkeypatch):
    seen = []
    eb = client.app.state.event_broadcaster

    async def grab(ev):
        seen.append(ev)
    monkeypatch.setattr(eb, "broadcast", grab)
    client.post("/v2/alerts/dismissed", json={"items": [{"key": "kite", "token": "t"}]})
    client.post("/v2/alerts/dismissed", json={"restore": ["kite"]})
    assert [e["event_type"] for e in seen] == ["alerts_dismissed", "alerts_dismissed"]
