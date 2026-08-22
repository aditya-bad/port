-- Step 105 — one-time backfill correcting every historical
-- deployment_snapshots row recorded before Step 99's fix.
--
-- Step 99 fixed how NEW snapshots are computed but deliberately left
-- already-recorded rows untouched, on the assumption there was no
-- reliable way to tell a pre-fix row from a post-fix one. That
-- assumption turns out to be unnecessary: `open_positions_value` and
-- `realized_pnl_cumulative` were ALREADY correct on every row, before
-- and after Step 99 — the bug was only in what `total_value` summed
-- them WITH (`cash`, which itself already embeds the same premium
-- open_positions_value's own formula re-adds — see Step 99's own
-- commit for the full derivation). That means the correct total_value
-- can be RECOMPUTED exactly, for every row, straight from other
-- columns already sitting right there: `initial_capital +
-- realized_pnl_cumulative`, plus `open_positions_value` only if the
-- deployment is "positional" (an "intraday" deployment's
-- open_positions_value is zeroed out here too, matching Step 99's
-- decision to exclude live intraday mark-to-market entirely — an old
-- intraday row may have a nonzero open_positions_value from before
-- that exclusion existed).
--
-- Idempotent by construction: this RECOMPUTES both columns from
-- scratch off other already-correct data rather than adjusting them
-- incrementally, so re-running it (or running it against rows already
-- fixed, or against a database that never had the bug) is a no-op —
-- the CASE/formula below produces the exact same values Step 99's own
-- _snapshot_one now writes for new rows.
UPDATE deployment_snapshots ds
SET
    open_positions_value = CASE WHEN d.mode = 'positional' THEN ds.open_positions_value ELSE 0 END,
    total_value = d.initial_capital + ds.realized_pnl_cumulative +
        (CASE WHEN d.mode = 'positional' THEN ds.open_positions_value ELSE 0 END)
FROM deployments d
WHERE ds.deployment_id = d.id;
