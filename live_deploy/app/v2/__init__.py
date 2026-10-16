"""v2 API — read models and actions for the Linck UI (static/linck/).

Everything here is additive: it reads the same tables the v1 routers do
and calls the same DeploymentManager, but no v1 endpoint, query or flow
is changed. Mounted at /v2 by app/main.py, behind the same AuthMiddleware
as every other route.

Modules:
  book        closed trades (episodes) and daily realized P&L, all deployments
  session     today's per-deployment P&L lanes and event markers
  actions     close-everything (shorts before longs) and create-with-start-mode
  catalog     strategy catalogue with field metadata for settings forms
  preferences per-user UI preferences (theme, colours, text size, ...)
"""
