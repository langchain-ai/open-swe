"""Run the real GitHub SDK against a deterministic HTTP transport."""

from collections.abc import Callable

import httpx2
import pytest

from openswe.github import sdk


def mock_github_sdk(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    # Under the SDK's metering, so requests are logged as they are in production.
    monkeypatch.setattr(sdk, "pooled_transport", lambda: httpx2.MockTransport(handler))

    def sign(*args: object, **kwargs: object) -> str:
        return "test-app-jwt"

    monkeypatch.setattr("githubkit.auth.app.jwt.encode", sign)
