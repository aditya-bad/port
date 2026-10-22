-- Reviewing and applying a day's repair from the Repair page
-- (app/repair/dayrepair.py). The replay can't run in the app, so the page
-- asks for it here and the repair worker (custom_scripts/repair_worker.py)
-- answers.

-- The paths you've chosen so far for a day's repair: kept until you push
-- it (or reset them). Nothing in the deployment's book changes until then.
CREATE TABLE IF NOT EXISTS repair_drafts (
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    day            DATE NOT NULL,
    choices        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (deployment_id, day)
);

-- One plan (a dry run, rolled back) or apply (committed only as planned).
CREATE TABLE IF NOT EXISTS repair_jobs (
    id             BIGSERIAL PRIMARY KEY,
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    day            DATE NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('plan', 'apply')),
    choices        JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected       TEXT,             -- apply: the plan's fills_key it must come out as
    author         TEXT,
    status         TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    result         JSONB,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS repair_jobs_queue ON repair_jobs (status, id);
CREATE INDEX IF NOT EXISTS repair_jobs_by_case ON repair_jobs (deployment_id, day, id DESC);
