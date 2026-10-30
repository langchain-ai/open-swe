"""Unit tests for LangSmith deployment setting resolution."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from managed_deepagents._langsmith_env import (
    DEFAULT_LANGSMITH_ENDPOINT,
    resolve_langsmith_endpoint,
    resolve_platform_injected_api_key,
    resolve_server_api_key,
    resolve_tenant_workspace_id,
    resolve_workspace_id,
)


@pytest.mark.parametrize(
    ("resolve", "first", "second", "unset"),
    [
        (resolve_server_api_key, "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", ""),
        (resolve_platform_injected_api_key, "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", ""),
        (
            resolve_langsmith_endpoint,
            "LANGSMITH_ENDPOINT",
            "LANGCHAIN_ENDPOINT",
            DEFAULT_LANGSMITH_ENDPOINT,
        ),
        (resolve_workspace_id, "LANGSMITH_WORKSPACE_ID", "LANGSMITH_TENANT_ID", None),
        (resolve_tenant_workspace_id, "LANGSMITH_TENANT_ID", "LANGSMITH_WORKSPACE_ID", None),
    ],
)
def test_resolver_order(
    monkeypatch: pytest.MonkeyPatch,
    resolve: Callable[..., str | None],
    first: str,
    second: str,
    unset: str | None,
) -> None:
    assert resolve({first: " a ", second: "b"}) == "a"
    assert resolve({first: " ", second: "b"}) == "b"
    assert resolve({first: "", second: " "}) == unset
    monkeypatch.delenv(first, raising=False)
    monkeypatch.setenv(second, "from-process")
    assert resolve() == "from-process"
