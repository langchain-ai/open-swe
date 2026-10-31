"""Unit tests for server-local Agent Server URL resolution."""

from __future__ import annotations

import pytest

from managed_deepagents._loopback import (
    is_loopback_host,
    is_loopback_url,
    resolve_loopback_api_key,
    resolve_loopback_api_url,
    sends_key_securely,
)

_ENV_VARS = (
    "MDA_CHANNEL_API_URL",
    "LANGGRAPH_API_URL",
    "LANGSMITH_ENDPOINT",
    "PORT",
    "MOUNT_PREFIX",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_resolve_prefers_explicit_then_channel_then_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOUNT_PREFIX", "/lgp/probe")
    monkeypatch.setenv("MDA_CHANNEL_API_URL", "http://127.0.0.1:2024/")
    monkeypatch.setenv("LANGGRAPH_API_URL", "http://127.0.0.1:9999")
    assert resolve_loopback_api_url(explicit="http://127.0.0.1:1234/") == "http://127.0.0.1:1234"
    assert resolve_loopback_api_url() == "http://127.0.0.1:2024"
    monkeypatch.setenv("MDA_CHANNEL_API_URL", "")
    assert resolve_loopback_api_url() == "http://127.0.0.1:9999"


@pytest.mark.parametrize("prefix", ["/lgp/probe", "/nested/lgp/probe/"])
def test_default_url_includes_mount_prefix(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    monkeypatch.setenv("PORT", "2024")
    monkeypatch.setenv("MOUNT_PREFIX", prefix)
    assert resolve_loopback_api_url() == f"http://127.0.0.1:2024{prefix.rstrip('/')}"


@pytest.mark.parametrize("prefix", ["", "/", "relative"])
def test_default_url_ignores_empty_or_invalid_mount(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    monkeypatch.setenv("PORT", "2024")
    monkeypatch.setenv("MOUNT_PREFIX", prefix)
    assert resolve_loopback_api_url() == "http://127.0.0.1:2024"


def test_resolve_ignores_langsmith_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """``LANGSMITH_ENDPOINT`` is LangSmith's API, not this server's origin."""
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    monkeypatch.setenv("PORT", "2024")
    assert resolve_loopback_api_url() == "http://127.0.0.1:2024"


def test_resolve_api_key_falls_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    assert resolve_loopback_api_key() == "local"
    assert resolve_loopback_api_key(explicit="  ") == "local"
    assert resolve_loopback_api_key(explicit="lsv2-key") == "lsv2-key"


def test_resolve_loopback_api_key_uses_the_deployment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "deployment-key")
    assert resolve_loopback_api_key() == "deployment-key"


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:2024",
        "http://127.0.0.53/threads/t",
        "http://localhost:8000",
        "http://[::1]:8000",
        "127.0.0.1:8000",
    ],
)
def test_is_loopback_url_accepts_local_origins(value: str) -> None:
    assert is_loopback_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "https://api.smith.langchain.com",
        "https://example.langgraph.app",
        "http://127.0.0.1.evil.com",
        "http://10.0.0.1:8000",
    ],
)
def test_is_loopback_url_rejects_remote_origins(value: str) -> None:
    assert is_loopback_url(value) is False


@pytest.mark.parametrize("value", ["http://[::1", "[::1", ""])
def test_is_loopback_url_rejects_unparseable_values(value: str) -> None:
    """A malformed origin must fail closed, not raise past the caller's error path."""
    assert is_loopback_url(value) is False


def test_is_loopback_host_handles_ports_brackets_and_casing() -> None:
    assert is_loopback_host("LocalHost:5557") is True
    assert is_loopback_host("[::1]:80") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("evil.com:80") is False


def test_sends_key_securely_over_https_or_plain_http_to_this_machine() -> None:
    assert sends_key_securely("https://api.smith.langchain.com") is True
    assert sends_key_securely("http://localhost:1984") is True
    assert sends_key_securely("http://127.0.0.1:1984") is True
    assert sends_key_securely("http://[::1]:1984") is True
    assert sends_key_securely("http://langsmith.example.com") is False
    assert sends_key_securely("ftp://localhost") is False
