# Linck

Linck is the new interface, served at `/linck/`. It's plain ES modules with no build step. The classic UI at `/` is unchanged, apart from a **Try Linck** button in its top bar. Linck's **Classic view** (in the More menu and on Account) switches back. The choice is remembered per browser in `localStorage.uiExperience`.

## Layout

```
index.html            shell; applies saved appearance before first paint
css/tokens.css        every colour, font and theme/preference rule
css/base.css          reset, layout variables, breakpoints, type scale
css/components.css    buttons, chips, status markers, tables, dialogs, toasts, tooltip
css/shell.css         header, index strip, phone bottom bar
css/pages.css         page layouts
fonts/                Funnel Display, Funnel Sans, Martian Mono (OFL, self-hosted)
js/app.js             boot: header, routing, live updates
js/core/              dom (safe html``), format (₹, %, IST), api, basis (P&L basis),
                      prefs (appearance registry), router (route table), store (UI state + events)
js/data/              live (ticks, events, Kite), book (all data + per-deployment view models),
                      metrics (pure analytics), alerts (attention rules), config (market facts)
js/ui/                components, charts, tooltip, dialog, toast, fields (settings forms), perfstack
js/actions.js         every deployment action and its confirmation
js/pages/             one module per page; deployment/ has one module per tab
```

## Making changes

- **Add a theme.** Add a `:root[data-th="name"] { … }` block to `css/tokens.css` and an entry to `THEMES` in `js/core/prefs.js`. Account › Appearance lists it automatically.
- **Add a preference.** Add its attribute rules to `css/tokens.css` and an entry to `PREFS` in `js/core/prefs.js`. Account renders the control, and the value is saved per user (`/v2/preferences`).
- **Change a colour.** Edit the token. Components only read tokens.
- **Add a page.** Add a line to `ROUTES` in `js/core/router.js` and create a module in `js/pages/` that exports `{ title, render(main, params) → cleanup }`.
- **Add a deployment tab.** Add it to `TABS` in `js/pages/deployment/index.js` and create a module that exports `render(el, v) → cleanup`.
- **Add an attention rule** (the Desk's "things need you" list). Append it to `RULES` in `js/data/alerts.js`.
- **Describe strategy settings.** Edit `STRATEGIES` / `FIELDS` in `app/v2/catalog.py`. Keys that aren't listed still render, with inferred labels.
- **Change thresholds** (young, quiet, losing streak, Desk lane count) or index tokens. Edit `js/data/config.js`.

## Data

Linck reads the same v1 endpoints as the classic UI, plus the additive `/v2` API in `app/v2/`:

- `/v2/book`: closed trades and daily P&L for all deployments.
- `/v2/session`: today's P&L lanes.
- `/v2/strategies`: the strategy catalogue.
- `/v2/preferences`: per-user appearance settings.
- `/v2/close-everything`: closes shorts first, then pauses.
- `/v2/deployments`: create a deployment, paused or started.

All P&L respects the Gross / After charges / + Slippage switch, which is shared with the classic UI.
