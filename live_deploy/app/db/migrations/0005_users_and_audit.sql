-- Multi-user auth + a durable audit trail for every state-changing
-- action. Replaces the single shared app_auth_secret as THE login
-- credential -- see app/main.py's startup for the one-time bootstrap
-- that turns app_auth_secret into the first user's password instead
-- (config.json's own comment on app_auth_secret explains why it's kept
-- around at all after that: it's also the session-cookie signing key
-- and the X-API-Key value for scripted/API access, neither of which
-- this migration touches).

-- `role` is unused by any check today (no RBAC yet, by explicit
-- request -- every authenticated user can do everything) but exists so
-- adding real role-based checks later is a column read, not a schema
-- migration. See app/rbac.py's own docstring for exactly where a real
-- check would plug in.
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'member',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

-- One row per state-changing (POST/PUT/PATCH/DELETE) request that
-- reached a real handler -- written by AuditLogMiddleware (app/auth.py),
-- not by individual routers, so a new router gets audited automatically
-- without anyone remembering to wire it in (same fail-closed reasoning
-- AuthMiddleware itself already uses). `username` is a snapshot, not
-- just a join through user_id -- a later-deleted user's own history
-- stays legible; user_id is kept too (nullable) for a real per-user
-- filter/join when one's needed.
CREATE TABLE IF NOT EXISTS audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    username        TEXT,
    method          TEXT NOT NULL,
    path            TEXT NOT NULL,
    status_code     INT,
    request_body    JSONB,
    remote_addr     TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
