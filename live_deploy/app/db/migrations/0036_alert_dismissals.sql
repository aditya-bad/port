-- Alerts the user dismissed (app/v2/alerts.py), so every device agrees.
-- An alert is worked out in the browser from the current state
-- (static/linck/js/data/alerts.js); `token` is what that alert said when
-- it was dismissed (a losing streak's length, an error's text, a day), so
-- it stays dismissed until the situation changes, then shows again.
CREATE TABLE IF NOT EXISTS alert_dismissals (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    token         TEXT NOT NULL,
    dismissed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
