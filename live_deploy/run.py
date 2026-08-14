#!/usr/bin/env python3
"""
live_deploy — convenience launcher: `python run.py`.

Exists because `python app/main.py` — an extremely natural first thing
to try — fails with:

    ImportError: attempted relative import with no known parent package

`app/main.py` uses relative imports throughout (`from . import
strategies`, `from .auth import ...`, etc.), which only resolve when
Python loads the file AS PART OF THE `app` PACKAGE — i.e. `python -m
app.main`, or via `uvicorn app.main:app` (uvicorn imports the dotted
path itself, never executes the file directly) — never when the file is
executed as a standalone top-level script the way `python app/main.py`
does it. That error's own failing line happens to read
`from . import strategies`, which looks exactly like a bug in the
strategy code even though it's unrelated — the real cause is just how
the file was invoked.

Run this INSTEAD, from anywhere (not just live_deploy/ — see below):
    python run.py

This is exactly `python -m app.main` under the hood — same host (0.0.0.0),
same port (8000), same reload=False — with ONE improvement: `python -m
app.main` only works if your current directory IS live_deploy/ (or it's
already on PYTHONPATH); this script locates live_deploy/ from its own
file path instead, so `python /wherever/live_deploy/run.py` works no
matter where you run it from.

For local development, prefer running uvicorn directly instead of this
script — it gives you `--reload`, which this intentionally does not
(matches app/main.py's own __main__ guard, meant for supervisors/
containers, not iterative dev):
    uvicorn app.main:app --reload --port 8000
"""

import sys
from pathlib import Path

# Put live_deploy/ (this script's own directory) at the front of
# sys.path — the same thing `python -m app.main` gets for free from
# being invoked as a module with live_deploy/ as the current directory,
# except this works regardless of the CALLER's current directory too.
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
