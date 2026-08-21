"""The error policy in :mod:`agent.store`: a missing item reads as ``None``, everything else raises."""

import httpx
import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent import store as agent_store


def _install(monkeypatch: pytest.MonkeyPatch, client: FakeLangGraphClient) -> None:
    monkeypatch.setattr(agent_store, "store_client", lambda: client)


async def test_missing_item_reads_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeLangGraphClient(missing="404"))

    assert await agent_store.get_value(("team_settings",), "absent") is None


async def test_store_outage_is_not_an_empty_record(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeLangGraphClient(missing="404")
    request = httpx.Request("GET", "http://langgraph.test/items")
    outage = httpx.HTTPStatusError(
        "service unavailable", request=request, response=httpx.Response(503, request=request)
    )

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise outage

    monkeypatch.setattr(client.store, "get_item", fail)
    _install(monkeypatch, client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await agent_store.get_value(("team_settings",), "present")

    assert exc_info.value is outage


async def test_deleting_an_item_that_is_already_gone_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeLangGraphClient(missing="404"))

    await agent_store.delete_value(("team_settings",), "absent")


async def test_round_trips_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeLangGraphClient(missing="404")
    _install(monkeypatch, client)

    await agent_store.put_value(("team_settings",), "model", {"default_model": "anthropic:opus"})

    assert await agent_store.get_value(("team_settings",), "model") == {
        "default_model": "anthropic:opus"
    }
    await agent_store.delete_value(("team_settings",), "model")
    assert await agent_store.get_value(("team_settings",), "model") is None
