"""The Open SWE backend this deployment calls back into, as the current run.

Dispatch signs a run token into each run's context. The backend reads the thread,
repository and pull request from that token, so nothing the model writes can
point a call elsewhere. Model tools reach the backend through MDA's MCP support;
the run hooks below are plain HTTP.
"""

import os
from typing import Final, Literal

import httpx2
from managed_deepagents import McpServersDefinition, define_mcp
from pydantic import JsonValue

BACKEND_URL_ENV: Final = "OPEN_SWE_BACKEND_URL"
MCP_SERVER_NAME: Final = "openswe"
# The backend enforces its own deadlines; this only bounds a lost connection.
_RESPONSE_HEADROOM_SECONDS: Final = 30.0

Hook = Literal["prepare", "drain", "settle"]


class BackendCallError(RuntimeError):
    """The backend refused or could not answer a call; never carries the token."""


class OpenSweBackend:
    """The Open SWE backend, called with one run's token."""

    def __init__(self, run_token: str | None) -> None:
        base = os.environ.get(BACKEND_URL_ENV, "").strip().rstrip("/")
        if not base:
            raise BackendCallError(f"{BACKEND_URL_ENV} is not configured")
        self._base = base
        self._run_token = run_token

    def mcp(self) -> McpServersDefinition:
        """The backend's tool server, authenticated as this run when it has a token."""
        url = f"{self._base}/remote-runtime/mcp"
        if self._run_token is None:
            return define_mcp(servers={MCP_SERVER_NAME: {"transport": "http", "url": url}})
        return define_mcp(
            servers={
                MCP_SERVER_NAME: {
                    "transport": "http",
                    "url": url,
                    "headers": {"Authorization": f"Bearer {self._run_token}"},
                }
            }
        )

    async def call(self, hook: Hook, *, timeout_seconds: float) -> JsonValue:
        if self._run_token is None:
            raise BackendCallError(
                "This run carries no Open SWE run token; start it through Open SWE"
            )
        try:
            async with httpx2.AsyncClient(
                timeout=timeout_seconds + _RESPONSE_HEADROOM_SECONDS
            ) as client:
                response = await client.post(
                    f"{self._base}/remote-runtime/hooks/{hook}",
                    headers={"Authorization": f"Bearer {self._run_token}"},
                )
                response.raise_for_status()
        except httpx2.HTTPError as exc:
            # Named by type only: transport errors can echo the request, and it carries the token.
            raise BackendCallError(f"{hook} failed ({type(exc).__name__})") from exc
        return response.json()
