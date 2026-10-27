-- Shadow mode's margin check used to refuse an order when every
-- deployment's open legs together needed more than the Kite account had
-- free, even when the order's own deployment fitted its capital. Paper
-- runs many deployments the real account was never funded for at once,
-- so that said nothing about the order: it is a warning now
-- (app/shadow.py check_margin). Checks stored before then are re-graded
-- the same way: the margin check becomes a warning, and the verdict is
-- what the order book said (as _verdict in app/shadow.py) unless another
-- rule refused it.

WITH regraded AS (
    SELECT s.lot_id,
           jsonb_agg(CASE
                 WHEN c->>'rule' = 'margin' AND c->>'status' = 'reject'
                      AND NOT coalesce((c->>'placing')::boolean, false)
                      AND c ? 'account_required' AND c ? 'account_free'
                      AND (c->>'required')::numeric <= (c->>'capital')::numeric + 0.5
                 THEN c || jsonb_build_object(
                     'status', 'warn',
                     'detail', format('Fits this deployment: ₹%s of its ₹%s. Every deployment''s open legs together would need ₹%s; '
                                      'the Kite account has ₹%s free, so all of them live on this account at once would be refused.',
                                      to_char((c->>'required')::numeric, 'FM999,999,999,990'),
                                      to_char((c->>'capital')::numeric, 'FM999,999,999,990'),
                                      to_char((c->>'account_required')::numeric, 'FM999,999,999,990'),
                                      to_char((c->>'account_free')::numeric, 'FM999,999,999,990')))
                 ELSE c END ORDER BY t.ord) AS checks
    FROM shadow_checks s, jsonb_array_elements(s.checks) WITH ORDINALITY AS t(c, ord)
    WHERE s.verdict = 'reject'
    GROUP BY s.lot_id
)
UPDATE shadow_checks s
SET checks = r.checks,
    verdict = CASE
        WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(r.checks) x WHERE x->>'status' = 'reject') THEN 'reject'
        WHEN s.filled_qty IS NULL THEN 'unknown'
        WHEN s.filled_qty >= s.qty - 0.000000001 THEN CASE WHEN coalesce(s.shortfall, 0) >= 1 THEN 'worse' ELSE 'fill' END
        WHEN s.filled_qty > 0 THEN 'partial'
        ELSE 'no_fill' END
FROM regraded r
WHERE s.lot_id = r.lot_id AND s.checks IS DISTINCT FROM r.checks;
