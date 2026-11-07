"""Answer each request on the alias domain it came in through, when it came through one."""

from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.utils.dashboard_links import PUBLIC_ORIGIN_HEADER, PublicOrigin

_HEADER = PUBLIC_ORIGIN_HEADER.encode("latin-1")


class PublicOriginMiddleware:
    """Bind the request's alias origin (``DASHBOARD_ALIAS_URLS``) for the URL builders.

    Requests through the canonical domain, or with no or an unlisted origin, are
    answered on ``DASHBOARD_BASE_URL`` as before.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        with PublicOrigin.serving(PublicOrigin.alias(self._claimed_origin(scope))):
            await self.app(scope, receive, send)

    @staticmethod
    def _claimed_origin(scope: Scope) -> str | None:
        for name, value in scope.get("headers", []):
            if name == _HEADER:
                return value.decode("latin-1")
        return None
