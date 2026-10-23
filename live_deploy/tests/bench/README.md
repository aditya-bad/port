# Benchmarks

The measurements in `docs/linck-brokers-languages-load.html`. Not part of the
gate; run them by name.

**Today's runner** (needs the test database, like the other tests):

    .venv/bin/python -m pytest -q -s -p no:cacheprovider tests/bench/bench_runner.py

**Across languages** (`ipc/`): a Rust "supervisor" sends tick batches over a
Unix socket to a strategy process in Rust, Go, Node or Python (each decodes
the 32-byte records, updates an EMA per instrument and replies), and times
the round trip; `fanout` serves 8–128 such processes at once; `conflate` is
the latest-value core (one slot per instrument, a changed-set per subscriber).

    cd tests/bench/ipc
    (cd gostrat && go build -o ../gostrat_bin .)
    (cd rs && cargo build --release)
    S=rs/target/release
    $S/rs rust $S/strat; $S/rs go ./gostrat_bin; $S/rs node node strat.js; $S/rs python python3 strat.py
    for k in 8 32 128; do $S/fanout $k 400 python3 strat.py; done
    $S/conflate

Standard libraries only: nothing to download.
