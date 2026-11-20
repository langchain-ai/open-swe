import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from langchain_core.messages import HumanMessage

from openswe.agent_api.run_state import as_messages
from openswe.input_messages import human_input, system_input
from openswe.middleware.model_selection import (
    RouteSelection,
    classify_route,
)


def _said(text: str) -> HumanMessage:
    """What a person wrote, as the backend records it."""
    [message] = as_messages(
        [human_input(text, {"sender_id": "github:alice", "surface": "web", "kind": "human"})]
    )
    assert isinstance(message, HumanMessage)
    return message


def _sent(text: str, sender: str) -> HumanMessage:
    """An event a system sent the run, as the backend records it."""
    [message] = as_messages(
        [system_input(text, {"sender_id": sender, "surface": "automation", "kind": "system"})]
    )
    assert isinstance(message, HumanMessage)
    return message


@pytest.fixture(autouse=True)
def _no_gateway_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("messages", "task"),
    [
        # A run no person started is classified by the latest event sent to it.
        (
            [_sent("Old event", "system:schedule:s1"), _sent("Audit deps", "system:schedule:s1")],
            "Audit deps",
        ),
        # A person's request wins over an event after it.
        ([_said("Rename the flag"), _sent("CI finished", "system:ci")], "Rename the flag"),
    ],
)
async def test_classifies_the_latest_request_or_event(
    monkeypatch: pytest.MonkeyPatch, messages: list[HumanMessage], task: str
) -> None:
    jev = AsyncMock(return_value=RouteSelection("balanced"))
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", jev)

    assert await classify_route(messages) == RouteSelection("balanced")

    jev.assert_awaited_once_with(task)


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
    jev = AsyncMock(return_value=RouteSelection("balanced"))
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", jev)
    await classify_route(
        [HumanMessage(content=_HUMAN_ENVELOPE), HumanMessage(content=_PERSON_BLOCK)]
    )

    jev.assert_awaited_once_with("how's the weather in sf today")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "auth", "timeout", "http", "malformed", "confidence", "nan"]
)
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
        if failure == "auth" and len(requests) == 1:
            return httpx2.Response(403)
        if failure == "malformed":
            return httpx2.Response(200, json={"answers": {}})
        confidence = "NaN" if failure == "nan" else 0.5 if failure == "confidence" else 0.9
        return httpx2.Response(
            200,
            json={
                "model": "typesafe/jev-1.13.0",
                "answers": {
                    "task_type": {
                        "type": "choice",
                        "choice": "lookup",
                        "confidence": confidence,
                        "probabilities": {"lookup": 0.9, "design": 0.1},
                    },
                    "complexity": {
                        "type": "choice",
                        "choice": "routine",
                        "confidence": 0.9,
                        "probabilities": {"routine": 0.9, "open_ended": 0.1},
                    },
                },
            },
        )

    client = httpx2.AsyncClient
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "gateway-key")
    monkeypatch.setenv("LANGSMITH_API_KEY", "other-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gateway.example.com/")
    monkeypatch.setattr(
        "httpx2.AsyncClient",
        lambda **kwargs: client(**{**kwargs, "transport": httpx2.MockTransport(handle)}),
    )
    selection = await classify_route([_said("x" * 8_001)])
    assert selection == (
        RouteSelection("default", False) if failure else RouteSelection("balanced")
    )
    assert len(requests) == 1
    assert requests[0].url == "https://gateway.example.com/v1/systemone"
    assert requests[0].headers["Authorization"] == "Bearer gateway-key"
    payload = json.loads(requests[0].read())
    # A long request reaches the classifier as its start and its end.
    assert payload["state"] == f"{'x' * 4_000}\n…\n{'x' * 4_000}"
    assert payload["model"] == "typesafe/jev-1.13.0"
    assert sorted(payload["questions"]) == ["complexity", "task_type"]
    if failure == "auth":
        assert await classify_route([_said("Try again")]) == RouteSelection("balanced")
        assert len(requests) == 2


@pytest.mark.parametrize(
    ("kept", "route"),
    [
        ("fast", "fast"),
        ("fast_alt", "fast"),
        ("balanced", "balanced"),
        ("performance", "performance"),
        ("default", "default"),
        ("cheapest", None),
        ("", None),
        (None, None),
    ],
)
def test_a_kept_route_reads_as_both_sides_route_it(kept: object, route: str | None) -> None:
    from runtime_api.catalog import kept_route

    assert kept_route(kept) == route
