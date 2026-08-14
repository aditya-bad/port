# Running live_deploy

Three genuinely different ways to run this service, in increasing order
of "how long is this supposed to stay up unattended": local development
(fast iteration, you're watching the terminal), background/production on
a machine you already have (runs across weeks, no babysitting), and
containerized (portable, reproducible). Pick the one that matches what
you're actually doing — they're not variations on one command, they have
different failure modes and different things you need to have ready
first.

Every command below was actually run against a real (local) Postgres
instance while writing this guide, not just reviewed for plausibility —
see each section's own "Verify it worked" for what that looked like. The
one exception, called out explicitly where it matters, is the Docker
build itself: this guide's own authoring sandbox blocks all outbound
container-registry traffic (confirmed against two independent
registries, a genuine policy denial, not a transient failure), so that
specific `docker build`/`docker run` pair could not be executed inside
that sandbox. The Dockerfile's paths were still verified by hand against
`app/config.py` and `app/main.py`'s actual path-resolution logic (see
that section) — but if you hit something unexpected running it for
real, that's the one seam in this guide that genuinely wasn't
click-tested end to end.

If you haven't yet: read the README's own "Setup" section first for
what `config.json`/`tokens.json` actually need to contain (Kite Connect
credentials, `database_url`, `app_auth_secret`) and the one-time Kite
redirect-URL registration step. This guide assumes those are already in
hand and focuses purely on *how to run the process itself*.

## The bug you'll hit if you try `python app/main.py`

Don't. It crashes:

```
Traceback (most recent call last):
  File "/…/live_deploy/app/main.py", line 46, in <module>
    from . import strategies  # noqa: F401 — importing runs every @register_strategy in it
    ^^^^^^^^^^^^^^^^^^^^^^^^
ImportError: attempted relative import with no known parent package
```

This is an extremely natural first thing to try, and the failing line
happens to contain the word "strategies" — it reads exactly like a bug
in the strategy code even though it's completely unrelated. The real
cause: `app/main.py` uses relative imports throughout (`from . import
strategies`, `from .auth import ...`, etc.), which only resolve when
Python loads the file AS PART OF the `app` package — `python -m
app.main`, or `uvicorn app.main:app` (uvicorn imports the dotted path
itself, it never executes the file directly) — never when the file
itself is executed as a standalone script the way `python app/main.py`
does it. An `if __name__ == "__main__":` guard inside `app/main.py`
can't fix this either — the crash happens at IMPORT time, before that
guard is ever reached.

**Fixed properly, not just documented around**: `run.py`, a tiny
launcher at the `live_deploy/` root, outside the `app` package entirely.
It puts `live_deploy/` on `sys.path` itself (so it works no matter what
your current directory is when you invoke it — verified below, unlike
`python -m app.main`, which needs your cwd to already be `live_deploy/`)
and then does exactly what `uvicorn app.main:app --port 8000` does.

```bash
python run.py
```

**Verified working**, reproduced the exact same two ways the original
bug was found:

```bash
# from live_deploy/ itself
cd live_deploy && python run.py
# from a completely different directory
cd /tmp && python /path/to/live_deploy/run.py
# from the REPO ROOT, one level above live_deploy/ — also works,
# because run.py locates live_deploy/ from its own file path, not cwd
cd /path/to/port && python live_deploy/run.py
```

All three reach the real startup sequence (migrations apply, dispatcher
starts, `Application startup complete`) — no `ImportError`, regardless
of where you ran it from. (With no `config.json` yet, all three instead
show `config.py`'s own clear `RuntimeError: Config not found: …` message
— see "Common failure modes" below — which is the CORRECT failure at
that point, not the import bug.)

`uvicorn app.main:app --reload --port 8000` (see Option 1 below) is
still the right way to run this for local development — it gives you
auto-reload, which `run.py` deliberately doesn't (same as `app/main.py`'s
own `__main__` guard, which `run.py` mirrors exactly: host `0.0.0.0`,
port `8000`, `reload=False`). `run.py` exists for the "just run one
command, no reload, works from anywhere" case — supervisors and
containers (Options 2 and 3 below) both use it directly.

---

## Option 1 — Local development (fast iteration)

```bash
cd live_deploy
cp config.example.json config.json   # fill in real values — see below
uvicorn app.main:app --reload --port 8000
```

**Requires:**
- A reachable Postgres instance. **Verified**: plain local Postgres
  works with zero special setup — no extensions, no Neon-specific SQL
  anywhere in `app/db/` (checked: the only Postgres-version-sensitive
  thing used is `gen_random_uuid()`, native to Postgres 13+, not an
  extension that needs enabling). This guide's own testing ran every
  example against a local `postgresql://postgres:testpass@localhost/…`
  DSN with no `sslmode` parameter at all, and every migration applied
  cleanly. Neon's free tier (referenced elsewhere in this project) also
  works fine — its connection strings just happen to include
  `sslmode=require`, which `asyncpg` honors automatically because it's
  present in the DSN, not because of anything Neon-specific in this
  codebase.
- Real Kite Connect credentials (`api_key`/`api_secret` in
  `config.json`) — required to actually reach Kite; `access_token` is
  NOT required in `config.json` (see the README's Kite login flow — it's
  obtained via the UI/`/kite/callback` and persisted in Postgres).
- `app_auth_secret` set in `config.json` — this app's own front door
  (not related to Kite at all); the service refuses to start without it.

**Verify it started correctly** — hit `/health`:

```bash
curl -s http://localhost:8000/health -H "X-API-Key: <your app_auth_secret>"
```

```json
{"status":"ok","database_connected":true,"running_deployments":0,
 "kite_connected":false,"needs_login":true, …}
```

**Important nuance found while writing this guide**: the top-level
`"status": "ok"` field ONLY reflects database connectivity (see
`app/routers/health.py` — it's `"ok" if db_ok else "degraded"`,
computed from a bare `SELECT 1`). It says nothing about Kite. A totally
fresh deploy with no Kite login yet shows `status: "ok"` right alongside
`kite_connected: false, needs_login: true` — that's the CORRECT and
expected state on first boot, not a problem, but don't treat
`status: "ok"` alone as "everything's fine, ticks are flowing." Check
`kite_connected`/`needs_login` specifically for that (see "Common
failure modes" below).

**Deploy a strategy and confirm it's genuinely running** (not just "the
server is up"):

1. Open `http://localhost:8000/` — first run shows "Not connected —
   login required." Click **Login with Kite**, complete the popup login.
   `/health`'s `kite_connected` flips to `true` within a couple seconds,
   no restart. Already have a `request_token` from completing Kite's own
   login in a separate tab (or the popup can't reach this service from
   wherever you're logging in)? Click **Enter manually** instead — same
   end state, see the README's own section on this for exactly what it
   does and doesn't persist.
2. Go to **Strategy Catalog**, pick a strategy (e.g. `pivot_supertrend`),
   fill in the config, deploy it. Need an `instrument_token` for the
   config and don't already know the raw number? The **Instruments**
   page (sidebar) searches Kite's instrument master by symbol/name
   across NSE/NFO/BSE/BFO. **Verified**: `POST /deployments`
   against a real running instance returns `201` with
   `"strategy_registered": true` and `"status": "active"` immediately.
3. Go to **Deployed Strategies** → click into it → **Strategy Detail**.
   The **Positions**/**Trades** tabs start empty — that's expected until
   a live tick actually triggers the strategy's own entry condition
   (during market hours, with a real Kite connection). What tells you
   it's GENUINELY alive, not just sitting there: `/health`'s
   `ticks_received` counter climbing and `last_tick_at` advancing (proof
   ticks are actually flowing to it), and — once the strategy's own
   entry condition fires — a real row appearing in the **Trades** tab
   with its full `trigger`/`trigger_values` metadata (click the row to
   expand it), which is independently checkable against the strategy's
   own documented rule, not something you have to just trust.

---

## Option 2 — Background/production, same machine

For actually leaving this running unattended across weeks — the normal
mode for a paper-trading system, not something you babysit in a
foreground terminal. Use a real process supervisor, not `nohup`.

**This guide uses `supervisord`** (no such tooling exists in this repo
yet, and this was the one actually exercisable inside this guide's own
testing sandbox — it has no `systemd` PID 1, so a systemd **user** unit
genuinely can't be started there; on a normal Linux host with systemd,
a user unit following the exact same restart/re-login discipline below
is an equally valid choice — the important properties are "restarts on
crash" and "never silently assumes a stale Kite session is still
valid," not the specific supervisor).

```bash
cd live_deploy
pip install supervisor   # one-time, not in requirements.txt (a dev/ops choice, not a runtime dependency)
supervisord -c supervisord.conf
supervisorctl -c supervisord.conf status
```

`live_deploy/supervisord.conf` is already committed in this repo —
no paths to edit before using it. It uses supervisor's own `%(here)s`
interpolation (this config file's own directory) throughout, so it
works unmodified regardless of where `live_deploy/` is checked out, and
writes its logs/socket/pidfile to `./logs/` (created alongside the
config, gitignored except for a `.gitkeep`) rather than `/var/log`, so
it needs no root/sudo to run as-is:

```ini
[unix_http_server]
file=%(here)s/logs/supervisor.sock

[supervisord]
logfile=%(here)s/logs/supervisord.log
pidfile=%(here)s/logs/supervisord.pid

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://%(here)s/logs/supervisor.sock

[program:live_deploy]
command=python3 %(here)s/run.py
directory=%(here)s
autostart=true
autorestart=true
startsecs=3
stopsignal=TERM
stdout_logfile=%(here)s/logs/stdout.log
stderr_logfile=%(here)s/logs/stderr.log
```

Point `stdout_logfile`/`stderr_logfile`/`[supervisord] logfile` at
`/var/log/live_deploy/…` instead for a more conventional system-wide
install (create that directory first).

**Requires**: same as Option 1 (`config.json` with real Kite
credentials + a reachable Postgres + `app_auth_secret`) — a supervisor
doesn't change anything about what the process itself needs, only how
it's kept alive.

**Verify it started correctly:**

```bash
supervisorctl -c supervisord.conf status
# live_deploy   RUNNING   pid 13747, uptime 0:00:03
curl -s http://localhost:8000/health -H "X-API-Key: <your app_auth_secret>"
```

**Verified for real, including the crash-restart behavior that's the
actual point of using a supervisor**: with a deployment already active
and running, sending the process `kill -9` (simulating a genuine crash,
not a graceful stop) — `supervisorctl status` shows it back to `RUNNING`
with a new PID within ~3 seconds, and `/health` immediately afterward
correctly shows `"running_deployments": 1` again — `DeploymentManager`
resumed it from Postgres exactly as it would after any restart (see the
README's "persistent, resumable" design), no state lost, no manual
intervention needed for the deployment itself.

**What a restart does NOT do — and must not, silently**: automatically
re-establish a valid Kite session. Kite's `access_token` expires daily
regardless of process restarts; whatever's in the `kite_sessions` table
is what a fresh process picks up, valid or not. **Verified**: after the
crash-restart above (this test's Kite session was never real to begin
with), `/health` correctly came back with `needs_login: true` rather
than pretending everything was fine — the dispatcher just sits idle
until someone completes the login flow, same behavior as first boot.
This is exactly why a supervisor restarting the PROCESS is safe and
correct (a real crash — OOM, an unhandled exception — should absolutely
come back up on its own) while the KITE SESSION is a separate, human-
gated concern: **tie any restart (scheduled or crash-triggered) to a
check of `/health`'s `kite_connected`/`needs_login`**, and re-trigger
the existing daily re-login flow (`GET /kite/login-url` → complete the
popup → `GET /kite/callback`, see the README's "Kite login flow"
section) if it comes back `false`/`true` respectively. A monitoring
script/cron hitting `/health` after any restart and alerting on
`needs_login: true` (or `kite_connected: false` with `needs_login:
false` — the "session exists but currently down" state, which usually
recovers on its own but is worth watching if it persists) is a
reasonable low-effort way to make this an active check rather than
something you only discover market-open the next day.

**Deploy a strategy and confirm it's running**: identical to Option 1's
own steps above — the UI/API surface doesn't change based on how the
process is supervised.

---

## Option 3 — Containerized (Docker)

For portability/reproducibility. `live_deploy/Dockerfile` (didn't exist
before this guide — added alongside it):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY static/ ./static/
COPY run.py ./run.py
COPY config.example.json ./config.example.json
EXPOSE 8000
CMD ["python", "run.py"]
```

(`config.json`/`tokens.json` are deliberately NOT copied into the image
— see "Stateless, verified" below.)

```bash
docker build -t live-deploy .

docker run -d --name live-deploy -p 8000:8000 \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/tokens.json:/app/tokens.json:ro \
  live-deploy
```

**Requires**: same underlying prerequisites as Options 1/2 (Kite
credentials, a reachable Postgres — from inside a container, `localhost`
means the CONTAINER, not the host; point `database_url` at the host's
real address, e.g. `host.docker.internal` on Docker Desktop, or your
actual Postgres/Neon host — and `app_auth_secret`), just supplied
differently: via a mounted `config.json` (shown above) or by generating
`config.json` from environment variables in your own entrypoint/CI step
before `docker run` — config/secrets should never be baked into the
image itself either way.

**Stateless, verified**: grepped the entire `app/` tree for any file
read/write beyond `config.json`/`tokens.json` (both loaded once, at
startup — `config.json` at import time, `tokens.json` inside the
FastAPI startup event) and the committed `static/` assets (served
read-only, part of the image, not user data). Found nothing — no local
log files (logging goes to stdout only), no cache directories, no
SQLite, no writes anywhere. Every genuinely durable thing (deployments,
positions, fills, the daily Kite session) lives in Postgres. A container
built from this image really can be killed and replaced at any time
without losing anything that matters, PROVIDED `config.json`/
`tokens.json` are handed back on the next run (mounted or regenerated)
— they're read-once startup input, not state the container itself owns.
Also verified by hand against the actual path-resolution code (not
assumed): `app/config.py`'s `CONFIG_PATH`/`TOKENS_PATH` are computed as
`Path(__file__).resolve().parent.parent / "config.json"` — with
`app/config.py` living at `/app/app/config.py` inside this image
(`WORKDIR /app` + `COPY app/ ./app/`), that resolves to exactly
`/app/config.json`, matching the mount path in the `docker run` command
above; same math for `app/main.py`'s `STATIC_DIR` resolving to
`/app/static`, matching `COPY static/ ./static/`.

**One honest caveat**: this specific `docker build`/`docker run` pair
was authored and its paths verified by hand as described above, but
could not be executed end-to-end inside the sandboxed environment this
guide was written in — that environment's own network policy blocks all
outbound container-registry traffic (confirmed against two independent
registries — Docker Hub and GitHub Container Registry both came back
with an explicit policy denial, not a timeout or a one-off flake), so
`docker build` can't even pull the `python:3.11-slim` base image there.
Options 1 and 2 above have no such gap — both were run for real,
including the crash-restart behavior. If you hit something unexpected
running this on a real machine with normal internet access, that's the
one part of this guide genuinely asking you to be the first to click it.

**Verify it started correctly / deploy a strategy**: identical
`/health` check and UI/API deploy flow as Options 1 and 2 — `docker logs
live-deploy` in place of a supervisor's own log files or a foreground
terminal.

---

## Credential hardening

`config.json` (Options 1/2) or a mounted copy of it (Option 3) puts
`api_key`/`api_secret`/`database_url`/`app_auth_secret` on disk as
plaintext. Be honest about what fixing that does and doesn't buy you:
**encrypting `config.json` would NOT fully protect against a live
compromise of the same server it runs on** — the app still needs the
decryption key accessible to itself somewhere on that machine, and an
attacker who's already gotten far enough to read `config.json` can
typically get that too. The actual improvement available here is
reducing what sits as a readable FILE in the first place, not obscuring
one that still has to exist — which is exactly what environment
variables do, and it's why this project doesn't ship a custom
`config.json` encryption scheme (see the note at the end of this
section for why not, if a stronger guarantee is genuinely needed later).

**The recommended path for anything actually server-hosted**:
`api_key`, `api_secret`, `database_url`, and `app_auth_secret` can each
be supplied as an environment variable instead of a `config.json` key —
`KITE_API_KEY`, `KITE_API_SECRET`, `DATABASE_URL`, `APP_AUTH_SECRET`
respectively. **Verified end-to-end**: with all four set and
`config.json` genuinely absent from disk, the REAL app — full FastAPI
lifespan, migrations, dispatcher, deployment manager, not just
`load_config()` called in isolation — boots successfully and serves
`/health` correctly, and the env-sourced `app_auth_secret` is
confirmably LIVE (the correct value authorizes requests, a wrong one
still 401s, not silently bypassed). This is additive, not a breaking
change — `config.json` alone, exactly as it works today, is untouched
for any key an env var doesn't override; **verified** a config.json
covering only some of the four keys, with the rest supplied as env
vars, merges correctly per-key rather than all-or-nothing.

How to supply them per deployment option:

- **Option 1 (local dev)**: not really the point of this option — stick
  with `config.json`, it's the quick-start convenience path and there's
  no "server" here to hold credentials at risk on. Nothing wrong with
  using env vars here too if you'd rather, they work identically.
- **Option 2 (supervisord/systemd)**: use an `EnvironmentFile=`-style
  mechanism — a **separate, restricted-permission file** (see below),
  never the repo's own `config.json`. For supervisord specifically, add
  an `environment=` line to `supervisord.conf`'s `[program:live_deploy]`
  section (supervisord doesn't read a `.env` file on its own); for a
  systemd user unit, `EnvironmentFile=/etc/live-deploy/env` pointing at
  a file `chmod 600`'d to the service's own user, containing
  `KITE_API_KEY=...` etc., one per line.
- **Option 3 (Docker)**: `docker run -e KITE_API_KEY=... -e
  KITE_API_SECRET=... -e DATABASE_URL=... -e APP_AUTH_SECRET=...`, or
  Docker secrets / your orchestrator's own secret injection if you have
  one, or a cloud provider's secret manager (AWS Secrets Manager, GCP
  Secret Manager, etc.) writing them into the container's environment at
  launch — no `-v config.json:...` mount needed at all in this case,
  since env vars alone now satisfy every required key.

**Cheap, immediate, zero-code step, regardless of everything else
above**: `chmod 600 config.json` — tightens exposure from "any local
user/process that can read files" to "only the exact user running this
app." Do this even if you're also moving to env vars for the keys env
vars cover; `config.json` may still exist locally for `tick_mode` or as
an `access_token` bootstrap.

**What this deliberately does NOT include, and why**: a custom
encryption scheme for `config.json`. Given the honest limitation stated
at the top of this section — the app still needs the key to decrypt it
accessible to itself somewhere — that adds real implementation
complexity for a smaller security improvement than env vars + tight
file permissions already provide for free. If genuinely stronger
protection is wanted later, a REAL secrets manager (not custom
encryption code written for this project) is the right next step, and
it's only worth building against a specific hosting target once one's
actually chosen, not speculatively now.

---

## Common failure modes

Real ones, either already known from this project's own history or
found while writing/testing this guide — not a generic troubleshooting
list.

**`python app/main.py` → `ImportError: attempted relative import with
no known parent package`.** Covered in full above. Fix: `python run.py`
(from anywhere), or `uvicorn app.main:app --reload --port 8000` for
local dev.

**`uvicorn app.main:app` run from the wrong directory** (e.g. the repo
root, one level above `live_deploy/`, instead of from inside
`live_deploy/` itself) → `ModuleNotFoundError: No module named 'app'`.
**Reproduced while writing this guide.** uvicorn needs `app` importable
from your CURRENT directory when you pass it a dotted path like
`app.main:app` — `cd live_deploy` first, or use `python
live_deploy/run.py` instead, which works from any directory (verified
above) because it locates `live_deploy/` from its own file path, not
your shell's cwd.

**Missing `config.json` AND missing environment variables** — crashes
at IMPORT time, before FastAPI even starts serving, not on the first
request. **Reproduced while writing this guide**, exact message (now
naming both ways to fix it, per "Credential hardening" below):
```
RuntimeError: Config not found: /…/live_deploy/config.json, and the
following required value(s) were not supplied via environment variable
either: api_key (or $KITE_API_KEY), api_secret (or $KITE_API_SECRET),
database_url (or $DATABASE_URL), app_auth_secret (or $APP_AUTH_SECRET).
Either copy config.example.json -> config.json and fill in your Kite
Connect credentials, or set the corresponding environment variable(s)
instead — see RUN_GUIDE.md's 'Credential hardening' section.
```
If `config.json` exists but is still missing a key not covered by any
env var either, the message is shorter and names only what's actually
still missing (`Config missing: app_auth_secret (or $APP_AUTH_SECRET)`,
say) — **verified** for both the "nothing at all" case and the "3 of 4
env vars set, config.json absent" case, confirming the message correctly
narrows down to only the genuinely missing piece rather than always
listing all four. Same style of clear message for a missing
`tokens.json` (env vars don't cover that one — it's the token
subscription list, not a credential).

**Expired Kite session after any restart** (crash, redeploy, or a
scheduled bounce) — not a bug, documented, existing behavior; see this
guide's own Option 2 section above for the restart-specific discipline,
and the README's "Kite login flow" section for the login mechanism
itself. The one thing worth restating here because it's easy to miss:
`/health`'s top-level `status: "ok"` does NOT mean this is fine — it
only reflects database connectivity (see Option 1's own "important
nuance" above). Check `kite_connected`/`needs_login` specifically.

**`/health` says `status: "ok"` but nothing is trading.** Found while
writing this guide, effectively the same trap as the one above stated
the other way round: a perfectly healthy-LOOKING `/health` response
(`status: "ok"`, `database_connected: true`) can still mean zero ticks
are flowing (`needs_login: true`, `ticks_received: 0`). If a deployed
strategy isn't producing trades and you're trying to figure out why,
check `kite_connected`/`ticks_received`/`last_tick_at` before assuming
the strategy logic itself is broken — "the service is up" and "this
strategy is genuinely seeing live prices" are two different questions,
and only the second one actually matters for a paper-trading deployment.
