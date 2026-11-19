"""Lend the replay tasks' containers GitHub OIDC tokens for Robin, and nothing else.

Commands Robin runs inside a task container can read its environment, so the job's
token-request credentials never go in. This broker runs on the runner instead; a
container asks it with a per-job secret, and it mints only tokens whose audience is
the Robin deployment.

    ROBIN_OIDC_AUDIENCE=<backend> ROBIN_OIDC_BROKER_TOKEN=<secret> \\
        uv run --no-project --with aiohttp --with httpx2 python oidc_broker.py <port>
"""

import hmac
import os
import sys

import httpx2
from aiohttp import web


class TokenBroker:
    """Answers the CLI's OIDC token requests, for the one audience it may have."""

    def __init__(self) -> None:
        self.audience = os.environ["ROBIN_OIDC_AUDIENCE"]
        self.secret = os.environ["ROBIN_OIDC_BROKER_TOKEN"]
        self.request_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
        self.request_token = os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
        self.client = httpx2.AsyncClient(timeout=30)

    async def token(self, request: web.Request) -> web.Response:
        authorized = hmac.compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {self.secret}"
        )
        if not authorized or request.query.getall("audience", []) != [self.audience]:
            return web.Response(status=403)
        # The request URL carries GitHub's api-version, which a params= argument would drop.
        minted = await self.client.get(
            httpx2.URL(self.request_url).copy_set_param("audience", self.audience),
            headers={"Authorization": f"Bearer {self.request_token}"},
        )
        minted.raise_for_status()
        return web.Response(body=minted.content, content_type="application/json")

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/token", self.token)
        return app


if __name__ == "__main__":
    web.run_app(TokenBroker().app(), host="0.0.0.0", port=int(sys.argv[1]), print=None)
