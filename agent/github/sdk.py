"""Async GitHub SDK configuration for authentication and access checks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from githubkit import GitHub
from githubkit.auth import BaseAuthStrategy

GITHUB_API_VERSION: Literal["2022-11-28"] = "2022-11-28"


@asynccontextmanager
async def github_sdk[A: BaseAuthStrategy](
    auth: A, *, timeout: float = 30.0, connect_timeout: float = 10.0
) -> AsyncIterator[GitHub[A]]:
    async with GitHub(
        auth,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        # Access checks must observe removals immediately. Token caching stays
        # scoped by repository and permission in agent.github.app.
        http_cache=False,
        auto_retry=False,
    ) as client:
        yield client
