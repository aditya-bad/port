"""The app boots on the test database and its front door works."""


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database_connected"] is True
    assert body["not_running"] == []


def test_anonymous_api_is_401_json(app_client):
    r = app_client.get("/deployments")
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/json")


def test_login_wrong_password(app_client):
    r = app_client.post("/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
