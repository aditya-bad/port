-- Single-row table holding the current Kite access_token. Kite tokens
-- expire daily, and this needs to survive the server restarting mid-day
-- (crash, redeploy) without forcing a re-login if the token is still
-- valid — so it lives in Postgres, not just in memory or config.json.
--
-- id is pinned to 1 via CHECK — there is exactly one "current" Kite
-- session for this whole service, matching the one-Kite-connection
-- design everywhere else in live_deploy.
CREATE TABLE IF NOT EXISTS kite_sessions (
    id            SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    access_token  TEXT,
    login_time    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
