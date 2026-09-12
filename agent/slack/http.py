"""Authenticated Slack Web API requests and cursor pagination."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx2
from fastapi import HTTPException

from agent.config import ENV
from agent.utils import ttl_cache
from agent.utils.http import DEFAULT_HTTP_TIMEOUT


class SlackClient:
    def __init__(self, client: httpx2.AsyncClient, token: str) -> None:
        self._client = client
        self.cache_key = "slack:" + hashlib.sha256(token.encode()).hexdigest()

    async def request(
        self, method: str, *, http_method: Literal["GET", "POST"] = "GET", **params: Any
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                http_method,
                f"https://slack.com/api/{method}",
                params=params if http_method == "GET" else None,
                json=params if http_method == "POST" else None,
            )
            if response.status_code == 429:
                raise HTTPException(
                    429,
                    "Slack is rate limiting requests. Try again shortly.",
                    headers={"Retry-After": response.headers.get("Retry-After", "60")},
                )
            response.raise_for_status()
            data = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Could not reach Slack. Try again.") from exc
        if not isinstance(data, dict):
            raise HTTPException(502, "Slack returned an invalid response.")
        if data.get("error") == "missing_scope":
            needed = data.get("needed") or "the required permissions"
            raise HTTPException(400, f"Reinstall the Slack app with {needed}.")
        if not data.get("ok"):
            raise HTTPException(400, "Slack rejected the request. Check the app permissions.")
        return data

    async def identity(self) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            data = await self.request("auth.test", http_method="POST")
            if not isinstance(data.get("team_id"), str) or not data["team_id"]:
                raise HTTPException(502, "Slack did not return a workspace ID.")
            return data

        return await ttl_cache.cached(f"{self.cache_key}:identity", 300, load)

    async def paginate(
        self, method: str, items_key: str, **params: Any
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            params["cursor"] = cursor
            page = await self.request(method, **params)
            items = page.get(items_key)
            if not isinstance(items, list):
                raise HTTPException(502, "Slack returned an invalid collection.")
            for item in items:
                if isinstance(item, dict):
                    yield item
            metadata = page.get("response_metadata") or {}
            cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
            if not cursor:
                return
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise HTTPException(502, "Slack returned an invalid pagination cursor.")
            seen_cursors.add(cursor)


@asynccontextmanager
async def slack_client(*, token: str | None = None) -> AsyncIterator[SlackClient]:
    token = ENV.SLACK_BOT_TOKEN.get() if token is None else token
    if not token:
        raise HTTPException(400, "Slack is not configured.")
    async with httpx2.AsyncClient(
        timeout=DEFAULT_HTTP_TIMEOUT, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        yield SlackClient(client, token)
