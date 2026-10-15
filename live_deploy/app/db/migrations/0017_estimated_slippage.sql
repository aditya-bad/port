-- Slippage, recorded ALONGSIDE fills (fills themselves stay at LTP --
-- see app/slippage.py). position_lots.book_price is the price the same
-- fill would have got against the live order book (sell walks the bids,
-- buy walks the asks); NULL means no order book was available for that
-- fill (including every fill made before this migration).
-- positions.estimated_slippage accumulates (LTP fill - book fill) cost
-- over every fill of the position, entry and exit, in rupees -- summed
-- alongside realized_pnl/estimated_charges by every P&L query.
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS book_price NUMERIC(18,4);
ALTER TABLE positions ADD COLUMN IF NOT EXISTS estimated_slippage NUMERIC(18,2) NOT NULL DEFAULT 0;
