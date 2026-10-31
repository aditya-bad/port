-- Videos (app/video/): a replay told as a narrated, animated MP4, made on
-- the server one at a time. The story (what's drawn and said) is kept
-- with the request, so a video that was being made when the app stopped
-- is made again from it after a restart. The file itself sits in the
-- videos folder (LINCK_VIDEO_DIR), named by the id.
CREATE TABLE IF NOT EXISTS videos (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    ep_from        INTEGER,
    ep_to          INTEGER,
    live           BOOLEAN NOT NULL DEFAULT false,      -- a cycle still running when it was asked for
    as_of          TIMESTAMPTZ NOT NULL,
    voice          TEXT NOT NULL,
    story          JSONB NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued',      -- queued, voicing, drawing, done, failed
    progress       REAL NOT NULL DEFAULT 0,
    note           TEXT,                                -- why it waits, or why it failed
    seconds        REAL,
    bytes          BIGINT,
    chapters       JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS videos_deployment ON videos (deployment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS videos_open ON videos (created_at) WHERE status NOT IN ('done', 'failed');
