"""Signing in, sessions, users, the audit log, and what the middleware
lets through without a session."""

import pytest

from tests.conftest import ADMIN_PASSWORD


def login(c, user="admin", pw=ADMIN_PASSWORD):
    return c.post("/auth/login", json={"username": user, "password": pw})


def test_login_me_logout(app_client):
    assert login(app_client).status_code == 200
    me = app_client.get("/auth/me").json()
    assert me["username"] == "admin"
    assert app_client.post("/auth/logout").status_code == 200
    assert app_client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("user,pw", [("admin", "wrong"), ("nobody", ADMIN_PASSWORD), ("", "")])
def test_bad_credentials_are_refused(app_client, user, pw):
    assert login(app_client, user, pw).status_code in (401, 422)


def test_change_password_needs_the_current_one_and_keeps_you_signed_in_on_failure(client):
    r = client.post("/auth/change-password", json={"old_password": "nope", "new_password": "something-long"})
    assert r.status_code == 401
    assert client.get("/auth/me").status_code == 200, "a wrong current password must not sign you out"
    r = client.post("/auth/change-password", json={"old_password": ADMIN_PASSWORD, "new_password": "another-pass-1"})
    assert r.status_code == 200
    client.post("/auth/logout")
    assert login(client, pw=ADMIN_PASSWORD).status_code == 401
    assert login(client, pw="another-pass-1").status_code == 200


def test_logout_everywhere_revokes_other_sessions(app_client):
    assert login(app_client).status_code == 200
    phone = dict(app_client.cookies)                 # device 1's session
    app_client.cookies.clear()
    assert login(app_client).status_code == 200      # device 2
    assert app_client.post("/auth/logout-everywhere").status_code == 200
    assert app_client.get("/auth/me").status_code == 401
    app_client.cookies.clear()
    app_client.cookies.update(phone)
    assert app_client.get("/auth/me").status_code == 401, "the other device is signed out too"


def test_users_can_be_added_and_listed_and_sign_in(client):
    r = client.post("/auth/users", json={"username": "riya", "password": "riya-pass-123"})
    assert r.status_code in (200, 201)
    assert "riya" in [u["username"] for u in client.get("/auth/users").json()]
    assert client.post("/auth/users", json={"username": "riya", "password": "x-pass-12345"}).status_code == 409
    client.post("/auth/logout")
    assert login(client, "riya", "riya-pass-123").status_code == 200
    assert client.get("/auth/me").json()["username"] == "riya", "a new user's session works straight away"


def test_audit_log_records_changes_without_secrets(client):
    client.post("/auth/change-password", json={"old_password": "nope", "new_password": "secret-new-pw"})
    rows = client.get("/auth/audit-log").json()
    assert rows and rows[0]["path"] == "/auth/change-password"
    assert "secret-new-pw" not in str(rows[0]["request_body"]) and "nope" not in str(rows[0]["request_body"])


def test_api_key_header_authenticates(app_client):
    r = app_client.get("/deployments", headers={"X-API-Key": ADMIN_PASSWORD})
    assert r.status_code == 200
    assert app_client.get("/deployments", headers={"X-API-Key": "wrong"}).status_code == 401
    assert app_client.get("/deployments?api_key=" + ADMIN_PASSWORD).status_code == 401, "query key only for streams"


# ── What's public, what isn't ─────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/manifest.json", "/sw.js", "/icons/linck-192.png", "/icons/linck-maskable-512.png",
    "/linck/css/tokens.css", "/linck/fonts/funnel-sans.woff2", "/linck/login/login.js",
])
def test_app_shell_assets_are_public(app_client, path):
    assert app_client.get(path).status_code == 200


@pytest.mark.parametrize("path", [
    "/linck/js/app.js", "/linck/js/core/api.js", "/js/api.js", "/deployments", "/v2/book",
    "/linck/css/../js/app.js", "/linck/login/../js/app.js",
])
def test_app_code_and_data_need_a_session(app_client, path):
    r = app_client.get(path)
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


def test_signed_out_root_serves_classic_sign_in(app_client):
    r = app_client.get("/")
    assert r.status_code == 401 and "text/html" in r.headers["content-type"]
    assert "/auth/login" in r.text


@pytest.mark.parametrize("path", ["/linck/", "/linck", "/linck/index.html"])
def test_signed_out_linck_serves_linck_sign_in(app_client, path):
    r = app_client.get(path)
    assert r.status_code == 401 and "text/html" in r.headers["content-type"]
    assert "/linck/login/login.js" in r.text


def test_signed_in_pages_serve_the_apps(client):
    r = client.get("/linck/")
    assert r.status_code == 200 and "js/app.js" in r.text
    assert client.get("/").status_code == 200
    assert client.get("/linck/js/app.js").status_code == 200


def test_manifest_launches_linck_and_keeps_the_app_identity(app_client):
    m = app_client.get("/manifest.json").json()
    assert m["start_url"] == "/linck/" and m["id"] == "/" and m["scope"] == "/"
    assert m["name"] == "Linck" and m["display"] == "standalone"
    purposes = {i["purpose"] for i in m["icons"]}
    assert {"any", "maskable"} <= purposes
    for icon in m["icons"]:
        assert app_client.get(icon["src"]).status_code == 200


def test_the_install_files_are_never_served_stale(app_client, client):
    """Browsers must re-check the manifest, icons, service worker and
    pages (a cached old manifest kept installs on the old name, icon and
    purple splash). Signed out too: install happens from the sign-in."""
    m = app_client.get("/manifest.json?v=linck")
    assert m.status_code == 200 and m.json()["name"] == "Linck"
    for path in ["/manifest.json", "/sw.js", "/icons/linck-512.png", "/", "/linck/"]:
        assert app_client.get(path).headers.get("cache-control") == "no-cache", path
    for path in ["/linck/", "/linck/js/app.js", "/linck/css/geek.css", "/"]:
        assert client.get(path).headers.get("cache-control") == "no-cache", path


def test_every_page_asks_for_the_current_manifest():
    """A new manifest URL makes browsers holding the old file fetch it again."""
    from pathlib import Path
    static = Path(__file__).resolve().parents[2] / "static"
    for page in ["linck/index.html", "linck/login/index.html", "index.html", "login.html"]:
        text = (static / page).read_text()
        assert 'href="/manifest.json?v=linck"' in text, page
        assert 'href="/manifest.json"' not in text, page


# ── Session cookie flags, by how the app is reached ───────────────────

@pytest.mark.parametrize("host,secure", [
    ("localhost:8000", False), ("127.0.0.1", False),
    ("100.101.102.103:8000", False),        # Tailscale: plain http over the tailnet
    ("trade.example.com", True), ("192.168.1.5", True),
])
def test_session_cookie_is_secure_except_on_localhost_and_tailscale(app_client, host, secure):
    r = app_client.post("/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD},
                        headers={"host": host})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert ("secure" in cookie) is secure, cookie
    assert "httponly" in cookie and "samesite=lax" in cookie
    app_client.cookies.clear()


def test_inactive_user_cannot_sign_in(client):
    from tests.conftest import sql
    client.post("/auth/users", json={"username": "gone", "password": "gone-pass-123"})
    sql(client, "UPDATE users SET is_active = false WHERE username = 'gone'")
    client.post("/auth/logout")
    assert login(client, "gone", "gone-pass-123").status_code == 401


def test_a_tampered_session_cookie_is_refused(client):
    good = client.cookies.get("session")
    client.cookies.clear()
    client.cookies.set("session", good[:-4] + "AAAA")
    assert client.get("/auth/me").status_code == 401


def test_audit_log_records_unparsed_bodies_and_redacts_nested_secrets(client):
    client.post("/notifications/unsubscribe", content=b"not json", headers={"content-type": "application/json"})
    client.post("/kite/manual-login", json={"request_token": "x", "api_key": "k", "api_secret": "TOPSECRET"})
    rows = client.get("/auth/audit-log").json()
    by_path = {r["path"]: r for r in rows}
    assert by_path["/notifications/unsubscribe"]["request_body"] == {"_unparsed": True}
    assert "TOPSECRET" not in str(by_path["/kite/manual-login"]["request_body"])
    assert all(r["username"] == "admin" for r in rows)


def test_reads_are_not_audited(client):
    client.get("/deployments")
    assert [r["path"] for r in client.get("/auth/audit-log").json()] == ["/auth/login"], "only the sign-in itself"
