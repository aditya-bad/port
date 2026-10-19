-- The candle store keeps option contracts only (Kite drops their history
-- at expiry; it keeps indices' and stocks' forever, so those are fetched
-- live instead). Remove the index candles recorded before this change:
-- every row whose instrument isn't a known CE/PE contract.
DELETE FROM candles_1m c
WHERE NOT EXISTS (
    SELECT 1 FROM contracts k
    WHERE k.instrument_token = c.instrument_token AND k.instrument_type IN ('CE', 'PE')
) AND NOT EXISTS (
    SELECT 1 FROM positions p
    WHERE p.instrument_token = c.instrument_token AND (p.symbol LIKE '%CE' OR p.symbol LIKE '%PE')
);
DELETE FROM contracts WHERE instrument_type IS DISTINCT FROM 'CE' AND instrument_type IS DISTINCT FROM 'PE';
