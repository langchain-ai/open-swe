import json
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from agent.middleware.model_selection import ModelSelectionMiddleware, ModelSelectionState


@pytest.fixture(autouse=True)
def _no_gateway_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


def _middleware(
    route_model_ids: dict[str, str] | None = None,
    routing_mode: Literal["auto", "fast"] = "auto",
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock]]:
    profiles = ("fast", "balanced", "performance")
    models = {profile: MagicMock(name=profile) for profile in profiles}
    middleware = ModelSelectionMiddleware(
        cast(Any, models),
        route_model_ids=route_model_ids,
        routing_mode=routing_mode,
    )
    return middleware, models


async def _invoke(middleware: ModelSelectionMiddleware, state: dict[str, Any]) -> ModelRequest:
    request = ModelRequest(
        model=MagicMock(),
        messages=state["messages"],
        state=cast(Any, state),
    )
    seen: list[ModelRequest] = []

    async def handler(routed: ModelRequest) -> ModelResponse:
        seen.append(routed)
        return MagicMock()

    await middleware.awrap_model_call(request, handler)
    return seen[0]


@pytest.mark.asyncio
async def test_route_is_stored_in_state_and_used_for_model_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev = AsyncMock(return_value="fast")
    monkeypatch.setattr("agent.middleware.model_selection._select_jev_route", jev)
    middleware, models = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]
    jev.assert_awaited_once_with("Update the README")


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_route", [None, "performance", "balanced", "fast"])
async def test_fast_mode_skips_classifier_and_routing_event(
    monkeypatch: pytest.MonkeyPatch,
    existing_route: str | None,
) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agent.middleware.model_selection.get_stream_writer",
        lambda: events.append,
    )
    middleware, models = _middleware(routing_mode="fast")
    state = {"messages": [HumanMessage(content="Update the README")]}

    if existing_route is not None:
        state["model_route"] = existing_route

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    expected_route = existing_route or "fast"
    assert state["model_route"] == expected_route
    assert (await _invoke(middleware, state)).model is models[expected_route]
    assert events == []


@pytest.mark.asyncio
async def test_routing_decision_only_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    jev = AsyncMock(return_value="balanced")
    monkeypatch.setattr("agent.middleware.model_selection._select_jev_route", jev)
    middleware, _ = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))
    await middleware.abefore_model(cast(Any, state), MagicMock())

    jev.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_route_is_reused_without_classifier() -> None:
    middleware, models = _middleware()
    state = {
        "messages": [HumanMessage(content="Follow up on the task")],
        "model_route": "performance",
    }

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "performance"
    assert (await _invoke(middleware, state)).model is models["performance"]


@pytest.mark.asyncio
async def test_persisted_fast_alt_route_is_migrated_to_fast() -> None:
    middleware, models = _middleware()
    state = {
        "messages": [HumanMessage(content="Follow up on the task")],
        "model_route": "fast_alt",
    }

    assert (await _invoke(middleware, state)).model is models["fast"]
    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"


@pytest.mark.asyncio
async def test_legacy_plan_state_does_not_override_existing_route() -> None:
    middleware, models = _middleware()
    state = {
        "messages": [HumanMessage(content="Plan the next change")],
        "model_route": "fast",
        "plan_mode": True,
    }

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]

    state["plan_mode"] = False
    assert (await _invoke(middleware, state)).model is models["fast"]


@pytest.mark.asyncio
async def test_routed_model_id_is_streamed_for_the_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agent.middleware.model_selection.get_stream_writer",
        lambda: events.append,
    )
    monkeypatch.setattr(
        "agent.middleware.model_selection._select_jev_route", AsyncMock(return_value="fast")
    )
    middleware, _ = _middleware(route_model_ids={"fast": "openai:gpt-5.6-sol"})
    state = {"messages": [HumanMessage(content="Update the README")]}

    await middleware.abefore_model(cast(Any, state), MagicMock())

    assert events == [
        {
            "type": "model_routed",
            "route": "fast",
            "model_id": "openai:gpt-5.6-sol",
        }
    ]


@pytest.mark.asyncio
async def test_routed_model_event_omitted_without_a_known_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agent.middleware.model_selection.get_stream_writer",
        lambda: events.append,
    )
    monkeypatch.setattr(
        "agent.middleware.model_selection._select_jev_route", AsyncMock(return_value="fast")
    )
    middleware, _ = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    await middleware.abefore_model(cast(Any, state), MagicMock())

    assert events == []


_HUMAN_ENVELOPE = (
    '<input-message sender="github:alice" surface="web" kind="human">\n'
    "how's the weather in sf today\n"
    "</input-message>"
)
_PERSON_BLOCK = (
    '<dynamic-context kind="person" id="github:alice">\n'
    "display_name: Alice\n"
    "workspace_admin: yes\n"
    "</dynamic-context>"
)


@pytest.mark.asyncio
async def test_jev_sees_the_human_request_not_injected_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jev = AsyncMock(return_value="balanced")
    monkeypatch.setattr("agent.middleware.model_selection._select_jev_route", jev)
    middleware, _ = _middleware()
    state = {
        "messages": [HumanMessage(content=_HUMAN_ENVELOPE), HumanMessage(content=_PERSON_BLOCK)]
    }

    await middleware.abefore_model(cast(Any, state), MagicMock())

    jev.assert_awaited_once_with("how's the weather in sf today")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "timeout", "http", "malformed", "confidence", "nan"])
async def test_jev_routes_or_falls_back(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx2.TimeoutException("timed out")
        if failure == "http":
            return httpx2.Response(503)
        if failure == "malformed":
            return httpx2.Response(200, json={"answers": {}})
        return httpx2.Response(
            200,
            json={
                "model": "typesafe/jev-1.13.0",
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "fast",
                        "confidence": "NaN"
                        if failure == "nan"
                        else 0.5
                        if failure == "confidence"
                        else 0.9,
                        "probabilities": {
                            "fast": 0.9,
                            "balanced": 0.05,
                            "performance": 0.05,
                        },
                    }
                },
            },
        )

    client = httpx2.AsyncClient
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "gateway-key")
    monkeypatch.setenv("LANGSMITH_API_KEY", "other-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gateway.example.com/")
    monkeypatch.setattr(
        "agent.middleware.model_selection.httpx2.AsyncClient",
        lambda **kwargs: client(**kwargs, transport=httpx2.MockTransport(handle)),
    )
    middleware, _ = _middleware()
    state = ModelSelectionState(messages=[HumanMessage(content="x" * 8_001)])
    route = await middleware.select_route(state)
    assert route == ("balanced" if failure else "fast")
    assert len(requests) == 1
    assert requests[0].url == "https://gateway.example.com/v1/systemone"
    assert requests[0].headers["Authorization"] == "Bearer gateway-key"
    payload = json.loads(requests[0].read())
    assert payload["state"] == "x" * 8_000
    assert payload["model"] == "typesafe/jev-1.13.0"
    state["model_route"] = route
    assert await middleware.select_route(state) == route
    assert len(requests) == 1
