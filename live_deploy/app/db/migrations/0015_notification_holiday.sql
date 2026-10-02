-- Single-row "mute Kite-connection alerts" switch (the "Mark as
-- holiday" button) -- see app/routers/notifications.py's mute/unmute
-- endpoints. id pinned to 1 via CHECK, same one-row-per-service pattern
-- as kite_sessions (0002). muted_until NULL means not muted; otherwise
-- every kite_disconnected/kite_reconnected alert is suppressed until
-- that moment (set to 7:00 IST the day after the button was pressed).
CREATE TABLE IF NOT EXISTS notification_holiday (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    muted_until  TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
