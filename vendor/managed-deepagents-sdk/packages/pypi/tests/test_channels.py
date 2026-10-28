"""Parity tests for shared channel contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from managed_deepagents._channels.binding import resolve_channel_provider
from managed_deepagents._channels.types import normalize_channel_destination


class TestNormalizeDestination:
    def test_stable_shape(self) -> None:
        assert normalize_channel_destination(
            {"type": "provider_conversation", "conversation_id": "C123"}
        ) == {"type": "provider_conversation", "conversation_id": "C123"}
        assert normalize_channel_destination(
            {
                "type": "provider_thread",
                "conversation_id": "C1",
                "thread_id": "123.4",
            }
        ) == {
            "type": "provider_thread",
            "conversation_id": "C1",
            "thread_id": "123.4",
        }


@pytest.mark.parametrize("provider", ["slack", "http"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_telemetry_reads_trusted_provider_without_legacy_binding(
    provider: str, wrapped: bool
) -> None:
    user = {"identity": "caller", "mda_source_provider": provider}
    assert (
        resolve_channel_provider(
            {"langgraph_auth_user": SimpleNamespace(**user) if wrapped else user}
        )
        == provider
    )


def test_telemetry_recognizes_ordinary_http_auth() -> None:
    assert resolve_channel_provider({"langgraph_auth_user": {"identity": "caller"}}) == "http"
    assert (
        resolve_channel_provider({"langgraph_auth_user": SimpleNamespace(identity="caller")})
        == "http"
    )


def test_telemetry_cannot_take_channel_source_from_client_fields() -> None:
    client_fields = {"mda_source_provider": "slack", "mda_channel_binding": {"provider": "slack"}}
    assert resolve_channel_provider(client_fields) is None
    assert (
        resolve_channel_provider({**client_fields, "langgraph_auth_user": {"identity": "caller"}})
        == "http"
    )


def test_telemetry_preserves_unknown_providers_and_rejects_malformed_auth() -> None:
    assert (
        resolve_channel_provider({"langgraph_auth_user": {"mda_source_provider": "future"}})
        == "future"
    )
    assert resolve_channel_provider({"langgraph_auth_user": {"mda_source_provider": None}}) is None
    assert resolve_channel_provider({"langgraph_auth_user": "caller"}) is None
