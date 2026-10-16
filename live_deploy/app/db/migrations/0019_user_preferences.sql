-- Per-user UI preferences for the Linck UI (app/v2/preferences.py):
-- theme, profit colour, highlight colour, contrast, text size, typeface,
-- and whatever preference is added later. A free-form JSON object so a
-- new preference needs no migration; the UI owns the keys and defaults.
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    prefs       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
