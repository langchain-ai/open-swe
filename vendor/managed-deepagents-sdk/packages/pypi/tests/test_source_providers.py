"""Declared providers must agree across managed auth and runtime identity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from managed_deepagents import _identity_auth, channels
from managed_deepagents._channel import Channel, collect_channels
from managed_deepagents._channels import source_providers
from managed_deepagents._connector import Connector, collect_connectors
from managed_deepagents._identity_auth import (
    ManagedAuthError,
    _is_trusted_source_provider,
    resolve_request_identity,
)
from managed_deepagents._identity_http import build_managed_http_app
from managed_deepagents._identity_runtime import build_runtime_identity
from managed_deepagents.identity import auth, define_identity


class _WebhookConnector(Connector):
    kind = "webhook_connector"

    def requirements(self, name: str) -> dict[str, object]:
        return {"name": name, "provider": "webhook"}


def _declare_source(source: str) -> str:
    if source.startswith("connector"):
        collect_connectors(
            [("connectors/events.py", SimpleNamespace(connector=_WebhookConnector()))]
        )
        return "webhook_connector" if source == "connector-kind" else "webhook"
    channel = channels.http(
        provider="photon",
        verify=lambda _ctx: True,
        parse=lambda _ctx: {"type": "ignore"},
        post=AsyncMock(return_value={"id": "reply"}) if source == "post" else None,
    )
    collect_channels([("channels/inbox.py", SimpleNamespace(channel=channel))])
    return "photon"


def _source_headers(provider: str) -> dict[str, str]:
    return {
        "x-mda-user-id": "person-1",
        "x-mda-source-provider": provider,
        "x-mda-source-thread-id": "conversation-1",
    }


@pytest.fixture(autouse=True)
def _isolate_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_providers, "_declared_providers", set())
    for env in (
        _identity_auth.INGRESS_SECRET_ENV,
        _identity_auth.HANDOFF_SECRET_ENV,
        _identity_auth.LANGSMITH_API_KEY_ENV,
        _identity_auth.LANGCHAIN_API_KEY_ENV,
        _identity_auth.LOCAL_DEV_ENV,
    ):
        monkeypatch.delenv(env, raising=False)

    def fail_remote_auth(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("private handoff must not call remote authentication")

    monkeypatch.setattr(_identity_auth, "urlopen", fail_remote_auth)


@pytest.mark.parametrize("source", ["receive", "post", "connector-kind", "connector-provider"])
@pytest.mark.parametrize("credential", ["handoff-secret", "server-key"])
def test_collection_allows_declared_provider_through_auth_and_runtime(
    monkeypatch: pytest.MonkeyPatch, source: str, credential: str
) -> None:
    provider = _declare_source(source)
    headers = _source_headers(provider)
    if credential == "handoff-secret":
        monkeypatch.setenv(_identity_auth.HANDOFF_SECRET_ENV, "private-secret")
        headers["x-mda-handoff-secret"] = "private-secret"
    else:
        monkeypatch.setenv(_identity_auth.LANGSMITH_API_KEY_ENV, "server-key")
        headers["x-api-key"] = "server-key"

    identity = asyncio.run(
        resolve_request_identity(
            define_identity(auth=auth.langsmith_api_key()).config, SimpleNamespace(headers=headers)
        )
    )

    assert identity == {
        "user": {"kind": "person", "id": "person-1"},
        "source": {"provider": provider, "thread_id": "conversation-1"},
    }


@pytest.mark.parametrize("credential", ["handoff-secret", "server-key"])
def test_private_credential_does_not_allow_undeclared_provider(
    monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    headers = _source_headers("undeclared")
    if credential == "handoff-secret":
        monkeypatch.setenv(_identity_auth.HANDOFF_SECRET_ENV, "private-secret")
        headers["x-mda-handoff-secret"] = "private-secret"
    else:
        monkeypatch.setenv(_identity_auth.LANGSMITH_API_KEY_ENV, "server-key")
        headers["x-api-key"] = "server-key"
    with pytest.raises(ManagedAuthError, match="unsupported x-mda-source-provider") as exc:
        asyncio.run(
            resolve_request_identity(
                define_identity(auth=auth.langsmith_api_key()).config,
                SimpleNamespace(headers=headers),
            )
        )
    assert exc.value.status == 401


@pytest.mark.parametrize("provided", [None, "wrong-secret"])
def test_declared_provider_still_requires_private_credential(
    monkeypatch: pytest.MonkeyPatch, provided: str | None
) -> None:
    provider = _declare_source("receive")
    monkeypatch.setenv(_identity_auth.HANDOFF_SECRET_ENV, "private-secret")
    headers = _source_headers(provider)
    if provided is not None:
        headers["x-mda-handoff-secret"] = provided
    with pytest.raises(ManagedAuthError, match="trusted server credential") as exc:
        asyncio.run(
            resolve_request_identity(
                define_identity(auth=auth.langsmith_api_key()).config,
                SimpleNamespace(headers=headers),
            )
        )
    assert exc.value.status == 401


@pytest.mark.parametrize("provider", ["slack", "github"])
def test_builtin_providers_remain_trusted(provider: str) -> None:
    assert _is_trusted_source_provider(provider)
    identity = build_runtime_identity(
        {"langgraph_auth_user": {"mda_user_id": "person-1", "mda_source_provider": provider}}
    )
    assert identity is not None
    assert identity["source"]["provider"] == provider


def test_runtime_does_not_accept_undeclared_provider() -> None:
    identity = build_runtime_identity(
        {
            "langgraph_auth_user": {
                "mda_user_id": "person-1",
                "mda_source_provider": "undeclared",
            }
        }
    )
    assert identity is not None
    assert identity["source"]["provider"] == "http"


@pytest.mark.parametrize("source", ["channel", "connector"])
def test_direct_named_http_app_sources_register_without_messaging(source: str) -> None:
    if source == "channel":
        channel = channels.http(
            provider="direct-provider",
            verify=lambda _ctx: True,
            parse=lambda _ctx: {"type": "ignore"},
        )
        channel.name = "inbox"
        build_managed_http_app(channels=[channel])
        provider = "direct-provider"
    else:
        connector = _WebhookConnector()
        connector.name = "events"
        build_managed_http_app(connectors=[connector])
        provider = "webhook"
    assert _is_trusted_source_provider(provider)
    identity = build_runtime_identity(
        {"langgraph_auth_user": {"mda_user_id": "person-1", "mda_source_provider": provider}}
    )
    assert identity is not None
    assert identity["source"]["provider"] == provider


@pytest.mark.parametrize("source", ["channel", "connector"])
def test_failed_collection_does_not_register_providers(source: str) -> None:
    if source == "channel":
        valid = channels.http(
            provider="failed-collection",
            verify=lambda _ctx: True,
            parse=lambda _ctx: {"type": "ignore"},
        )
        with pytest.raises(ValueError, match="implements none"):
            collect_channels(
                [
                    ("channels/valid.py", SimpleNamespace(channel=valid)),
                    ("channels/invalid.py", SimpleNamespace(channel=Channel())),
                ]
            )
        provider = "failed-collection"
    else:
        with pytest.raises(ValueError, match="implements none"):
            collect_connectors(
                [
                    ("connectors/valid.py", SimpleNamespace(connector=_WebhookConnector())),
                    ("connectors/invalid.py", SimpleNamespace(connector=Connector())),
                ]
            )
        provider = "webhook"
    assert not _is_trusted_source_provider(provider)


@pytest.mark.parametrize("source", ["channel", "connector"])
def test_requirements_failure_does_not_register_any_providers(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    def fail_requirements(_name: str) -> dict[str, object]:
        raise ValueError("requirements failed")

    if source == "channel":
        channel = channels.http(
            provider="earlier-channel",
            verify=lambda _ctx: True,
            parse=lambda _ctx: {"type": "ignore"},
        )
        failing_channel = channels.http(
            provider="failing-channel",
            verify=lambda _ctx: True,
            parse=lambda _ctx: {"type": "ignore"},
        )
        monkeypatch.setattr(failing_channel, "requirements", fail_requirements)
        with pytest.raises(ValueError, match="requirements failed"):
            collect_channels(
                [
                    ("channels/earlier.py", SimpleNamespace(channel=channel)),
                    ("channels/failing.py", SimpleNamespace(channel=failing_channel)),
                ]
            )
        providers = ("earlier-channel", "failing-channel")
    else:
        failing_connector = _WebhookConnector()
        failing_connector.kind = "failing-connector"
        monkeypatch.setattr(failing_connector, "requirements", fail_requirements)
        with pytest.raises(ValueError, match="requirements failed"):
            collect_connectors(
                [
                    ("connectors/earlier.py", SimpleNamespace(connector=_WebhookConnector())),
                    ("connectors/failing.py", SimpleNamespace(connector=failing_connector)),
                ]
            )
        providers = ("webhook_connector", "webhook", "failing-connector")

    assert not any(_is_trusted_source_provider(provider) for provider in providers)
