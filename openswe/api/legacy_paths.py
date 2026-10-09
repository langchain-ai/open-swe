"""Keep the API's pre-rename ``/dashboard/api`` URLs working at ``/api``.

Running sandboxes, installed desktop and CLI builds, and the OAuth callback URLs
registered with GitHub, Slack, and LangSmith all still name the old prefix, so it
is rewritten in place rather than redirected: a redirect would drop request
bodies, CORS preflights, and WebSocket upgrades.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

API_PREFIX = "/api"
LEGACY_API_PREFIX = "/dashboard/api"


def _is_legacy(path: str) -> bool:
    return path == LEGACY_API_PREFIX or path.startswith(LEGACY_API_PREFIX + "/")


class LegacyApiPathMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            root_path: str = scope.get("root_path", "")
            path: str = scope["path"]
            mount = root_path if root_path and path.startswith(root_path) else ""
            if _is_legacy(path[len(mount) :]):
                scope = dict(scope)
                scope["path"] = mount + API_PREFIX + path[len(mount) + len(LEGACY_API_PREFIX) :]
                if raw_path := scope.get("raw_path"):
                    scope["raw_path"] = raw_path.replace(
                        LEGACY_API_PREFIX.encode(), API_PREFIX.encode(), 1
                    )
        await self.app(scope, receive, send)
