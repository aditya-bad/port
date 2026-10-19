"""
live_deploy — no stale app shell.

StaticFiles sends ETag/Last-Modified but no Cache-Control, so browsers
guess a freshness lifetime from the file's age: a manifest unchanged for
weeks could be reused for days without asking. That is how an installed
Linck kept the old name, icon and purple splash, and how a page could run
yesterday's scripts after a redeploy. Every response that doesn't set its
own Cache-Control gets "no-cache": keep a copy, but check it's current
first (a cheap 304 when nothing changed).
"""

NO_CACHE = (b"cache-control", b"no-cache")


class NoStaleShellMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append(NO_CACHE)
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)
