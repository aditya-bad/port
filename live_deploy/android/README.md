# live_deploy — Android app

A native Android client for the `live_deploy` paper-trading backend (the
`live_deploy/` FastAPI app this project lives next to). Kotlin + Jetpack
Compose, talks to the exact same REST API the web UI already uses — no
backend changes were needed to stand this up (see "Auth" below).

**Status: Phase 1.** This is a real, working app — not a stub — but it
deliberately does NOT yet cover every screen the web UI has. See "What's
here" / "What's not here yet" below.

## Why this couldn't be built-and-handed-over as an APK

The sandbox this was written in has no Android SDK, and `dl.google.com`
(where the SDK itself comes from) is blocked by that environment's
network policy — so none of this has been compiled or run. It needs to
be opened in **Android Studio, on a real machine**, to build and install.
Read every file with a bit more scrutiny than you would code that had
already been through a build — in particular, the widget code
(`widget/`) uses Jetpack Glance, the newest library in this stack and
the one most likely to have a minor API surface drift from what's
written here if Android Studio's bundled library versions have moved on.

## Getting it running

1. Open the `android/` directory (this one) in Android Studio — "Open an
   existing project", point it at this folder.
2. Let Gradle sync. There's no committed Gradle wrapper jar (a binary
   file this session couldn't hand-author) — Android Studio will offer
   to generate one on first sync; accept that, or run `gradle wrapper`
   yourself once if you'd rather do it from the command line first.
3. Build & run onto a device/emulator (Run ▸ Run 'app', or `Shift+F10`).
4. On first launch, the app asks for two things:
   - **Server URL** — wherever your `live_deploy` backend is reachable,
     e.g. `https://your-host.ts.net` (a Tailscale-served instance, the
     deployment shape this backend's own RUN_GUIDE.md recommends) or a
     plain-HTTP tailnet address (see "Auth"/cleartext note below).
   - **API key** — your server's `app_auth_secret` (config.json, or the
     `APP_AUTH_SECRET` env var if you're running it that way instead).
     Same value you'd use for `curl -H "X-API-Key: ..."` against this
     backend today.
5. Tap **Test & Connect**. It won't save anything until the connection
   check actually succeeds, so you can't land on a blank Dashboard with
   no way to tell why.

## Auth — why no new backend code was needed

`live_deploy`'s backend already has a second auth path alongside the
browser's session cookie: an `X-API-Key` header, checked against the
same `app_auth_secret`, meant for "scripted/API access" (see
`app/auth.py`'s own module docstring). This app just reuses that path —
every request goes out with that header attached (see
`network/ApiClient.kt`). That means:

- Zero backend changes were required to build this.
- It authenticates the **app installation**, not a specific person — the
  backend's audit log will show these actions with no attributable
  user, same as any other API-key request today.
- The key lives in this app's own private app-storage (Preferences
  DataStore, not the Android Security library's
  EncryptedSharedPreferences — see `data/SettingsStore.kt`'s own comment
  for why that extra layer wasn't worth its well-documented
  Keystore-initialization fragility for a secret in this exact threat
  model).

If you'd rather each device/install have its own **revocable, per-user**
credential instead of everyone sharing the one `app_auth_secret`, that's
a real, small backend addition (a token endpoint + a second header
check) — flag it and it's a follow-up, not a redesign.

## Notifications — deliberately not built yet

You asked to keep Chrome/the PWA's existing Web Push as the notification
channel for now, and build native Android notifications (with actionable
buttons, e.g. "Pause" right from the notification) as a later phase once
you decide whether that's worth a Firebase Cloud Messaging setup.

Nothing notification-related is built into this app yet — not even a
stub interface. That's deliberate, not an oversight: an interface with
no second implementation to justify its shape is a guess, and probably
the wrong one before FCM's actual constraints are known. What "plug and
play" means in practice here instead:

- `network/ApiService.kt` / `data/DeploymentsRepository.kt` are the only
  places that know how to talk to the backend — a future notification
  feature (whether FCM push or in-app polling) calls into the SAME
  repository, not a parallel network stack.
- `LiveDeployApplication` is the natural place to initialize FCM (or a
  WorkManager-based poller) once you're ready — nothing here currently
  reaches into it that a notification subsystem would need to route
  around.
- The widget's own refresh worker (`widget/PnlWidgetWorker.kt`) is a
  working, real example of the WorkManager-based "poll the backend
  periodically" pattern already in this codebase — if you decide to
  start with polling-based local notifications instead of jumping
  straight to FCM (see the tradeoffs we discussed), that worker is the
  template to copy, not build from scratch.

## Widgets

One home-screen widget in this first phase: **live_deploy P&L** — total
P&L (realized + unrealized, same `include_in_reports` scoping the web
Dashboard uses) and a count of currently-active deployments. Tapping it
opens the app.

- `widget/PnlWidget.kt` — the widget's own Compose-flavored UI (Jetpack
  Glance, not the older RemoteViews/XML approach).
- `widget/PnlWidgetReceiver.kt` — the `AppWidgetProvider` Android needs;
  schedules the refresh worker on placement/re-enable.
- `widget/PnlWidgetWorker.kt` — a `WorkManager` periodic job (15-minute
  floor — an OS-enforced minimum for periodic work, not a choice made
  here) that calls the backend and updates the widget's own state.

Add it the normal way: long-press your home screen ▸ Widgets ▸
live_deploy P&L.

## What's here

- **Setup** — server URL + API key, tested before saving.
- **Dashboard** — portfolio-wide KPIs (total/realized/unrealized P&L,
  active/paused/stopped counts), computed client-side from the same
  `GET /deployments` list the Deployments screen uses.
- **Deployed Strategies** — the full list, with each row's live status
  hint (the same "Waiting for entry time (10:00 IST)"-style line the web
  table's Status column shows — Step 108's `status_fields`), and
  Pause/Resume/Stop/Flatten actions right from the list.
- **Deployment detail** — a single deployment's own P&L breakdown, live
  strategy state, and the same actions.
- **The P&L widget** described above.

## What's not here yet

Reports, Analytics charts (Monthly Performance matrix, P&L distribution,
equity/drawdown curve), History/Positions-Cycles, Catalog (deploying a
NEW strategy from the app), Compare, Portfolio's exposure-by-symbol view,
Settings/Tags, Account/push-notification management, and Strategy Lab's
own filtering. All of these are read the same way the web app already
reads them (the REST API is unchanged) — porting any one of them is
"add a screen that calls an existing endpoint", not new backend work.
Tell me which one you want next.

## Architecture, briefly

- **Networking**: Retrofit + OkHttp + kotlinx.serialization
  (`network/`). `ApiClient.build(baseUrl, apiKey)` constructs a fresh
  client from whatever's currently saved — there's no single app-wide
  Retrofit singleton, since the server address itself is user-configured
  and can change.
- **Data**: `data/SettingsStore.kt` (connection settings) and
  `data/DeploymentsRepository.kt` (everything else — wraps every backend
  call in `ApiResult.Success`/`Failure` so no screen needs its own
  try/catch around a raw network call).
- **UI**: Jetpack Compose + Navigation Compose + a bottom nav bar
  (Dashboard / Deployments), `ViewModel` + `StateFlow` per screen — no
  Hilt/Koin (see `ui/AppViewModelFactory.kt`'s own comment on why a DI
  framework wasn't worth it at this size).
- **Theme**: `ui/theme/Color.kt` mirrors the web app's own CSS custom
  properties (`static/index.html`'s `:root`/dark-mode blocks) exactly,
  light and dark, so this reads as the same product, not a reskin.
