"""Run the real GitHub SDK against a deterministic HTTP transport."""

from collections.abc import Callable, Coroutine
from functools import partial

import httpx
import pytest
from githubkit import GitHub

from agent.github import sdk


def mock_github_sdk(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
) -> None:
    monkeypatch.setattr(
        sdk, "GitHub", partial(GitHub, async_transport=httpx.MockTransport(handler))
    )

    def sign(*args: object, **kwargs: object) -> str:
        return "test-app-jwt"

    monkeypatch.setattr("githubkit.auth.app.jwt.encode", sign)
