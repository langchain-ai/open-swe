"""Async GitHub SDK configuration for authentication and access checks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx2
from githubkit import GitHub
from githubkit.auth import BaseAuthStrategy

from openswe.github.metering import MeteredTransport
from openswe.utils.http_pool import pooled_transport

GITHUB_API_VERSION: Literal["2022-11-28"] = "2022-11-28"


@asynccontextmanager
async def github_sdk[A: BaseAuthStrategy](
    auth: A, *, timeout: float = 30.0, connect_timeout: float = 10.0
) -> AsyncIterator[GitHub[A]]:
    async with GitHub(
        auth,
        timeout=httpx2.Timeout(timeout, connect=connect_timeout),
        # Access checks must observe removals immediately. Token caching stays
        # scoped by repository and permission in openswe.github.app.
        http_cache=False,
        auto_retry=False,
        # Logged like every other GitHub request, so installation lookups are counted too.
        async_transport=MeteredTransport(pooled_transport()),
    ) as client:
        yield client
