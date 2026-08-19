-- Real-time mobile notifications (Web Push) + the per-deployment toggle
-- that gates them.
--
-- push_subscriptions: one row per browser/device that opted in via
-- Account -> Notifications' "Enable notifications" button, which calls
-- the browser's PushManager.subscribe() and POSTs the resulting
-- subscription here. `endpoint` is the push service URL the browser
-- gave us (globally unique per subscription -- Chrome's own push
-- service issues a fresh one per device/browser-profile) -- UNIQUE so
-- re-subscribing the same device (e.g. after clearing permission and
-- re-granting it) updates the existing row instead of accumulating
-- duplicates that would send the same phone the same notification
-- twice. p256dh/auth are the subscription's own encryption keys,
-- required by the Web Push protocol to encrypt the payload the way
-- only that specific browser instance can decrypt.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-deployment mute for the entry/exit execution notifications (BOTH
-- the in-app toast and the mobile push together -- see
-- DeploymentRunner.notify_execution's own docstring). DEFAULT true so
-- every existing deployment keeps notifying exactly as this feature
-- intends the moment it ships, same "opt-out, not opt-in" convention
-- as include_in_reports (migration 0009). Deliberately does NOT affect
-- fills/pause/resume/stop/strategy_error events, which keep recording
-- to deployment_events (the Activity tab) and toasting exactly as
-- before -- this only gates the NEW execution-level notification.
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS notifications_enabled BOOLEAN NOT NULL DEFAULT true;
