-- India VIX and index history are fetched from Kite when a view asks
-- (app/vix.py), not stored: drop the tables an earlier build created.
DROP TABLE IF EXISTS vix_1m;
DROP TABLE IF EXISTS vix_no_data;
DROP TABLE IF EXISTS index_1d;
