from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, ToolMessage

from agent.middleware.model_selection import ModelSelectionMiddleware, RouteDecision


def _middleware(
    route: Literal["fast", "balanced", "performance"] = "fast",
    *,
    route_model_ids: dict[str, str] | None = None,
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock], AsyncMock]:
    models = {profile: MagicMock(name=profile) for profile in ("fast", "balanced", "performance")}
    structured = AsyncMock(return_value=RouteDecision(model_route=route))
    classifier = MagicMock()
    classifier.tags = None
    tagged = classifier.model_copy.return_value
    tagged.with_structured_output.return_value.ainvoke = structured
    middleware = ModelSelectionMiddleware(
        cast(Any, models),
        classifier,
        route_model_ids=route_model_ids,
    )
    classifier.model_copy.assert_called_once_with(update={"tags": ["nostream"]})
    tagged.with_structured_output.assert_called_once_with(
        RouteDecision,
        method="json_schema",
    )
    return middleware, models, structured


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
async def test_route_is_stored_in_state_and_used_for_model_calls() -> None:
    middleware, models, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]
    classifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_route_is_reused_without_classifier() -> None:
    middleware, models, classifier = _middleware()
    state = {
        "messages": [HumanMessage(content="Follow up on the task")],
        "model_route": "performance",
    }

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "performance"
    assert (await _invoke(middleware, state)).model is models["performance"]
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_mode_uses_performance_without_persisting_route() -> None:
    middleware, models, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the docs")], "plan_mode": True}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert "model_route" not in state
    assert (await _invoke(middleware, state)).model is models["performance"]
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_is_classified_from_approved_plan_after_plan_mode_exits() -> None:
    middleware, models, classifier = _middleware()
    approved_plan = (
        "Plan mode is now inactive because the plan was approved. Use the reviewed plan below "
        "as the implementation guide.\n\nImplement the API and UI changes."
    )
    state = {
        "messages": [
            HumanMessage(content="Build the feature"),
            ToolMessage(content=approved_plan, tool_call_id="approve-plan"),
        ],
        "plan_mode": False,
    }

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]
    classifier.assert_awaited_once()
    assert approved_plan in classifier.await_args.args[0]


@pytest.mark.asyncio
async def test_mid_run_plan_mode_temporarily_overrides_existing_route() -> None:
    middleware, models, classifier = _middleware()
    state = {
        "messages": [HumanMessage(content="Plan the next change")],
        "model_route": "fast",
        "plan_mode": True,
    }

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["performance"]
    classifier.assert_not_awaited()

    state["plan_mode"] = False
    assert (await _invoke(middleware, state)).model is models["fast"]


@pytest.mark.asyncio
async def test_classifier_failure_falls_back_to_balanced_route() -> None:
    middleware, models, classifier = _middleware()
    classifier.side_effect = RuntimeError("unavailable")
    state = {"messages": [HumanMessage(content="Do the task")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "balanced"
    assert (await _invoke(middleware, state)).model is models["balanced"]


@pytest.mark.asyncio
async def test_routed_model_id_is_streamed_for_the_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agent.middleware.model_selection.get_stream_writer",
        lambda: events.append,
    )
    middleware, _, _ = _middleware(route_model_ids={"fast": "openai:gpt-5.6-sol"})
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
    middleware, _, _ = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    await middleware.abefore_model(cast(Any, state), MagicMock())

    assert events == []


_HUMAN_ENVELOPE = (
    '<input-message sender="github:alice" surface="web" kind="human">\n'
    "<content>how's the weather in sf today</content>\n"
    "</input-message>"
)
_SENDER_CONTEXT_ENVELOPE = (
    '<input-message sender="system:sender-context" surface="automation" kind="system">\n'
    "<content>This metadata was generated by Open SWE for the sender of this "
    "message. Workspace admin: yes.</content>\n"
    "</input-message>"
)


@pytest.mark.asyncio
async def test_classifier_sees_the_human_request_not_injected_context() -> None:
    middleware, _, classifier = _middleware()
    state = {
        "messages": [
            HumanMessage(content=_HUMAN_ENVELOPE),
            HumanMessage(content=_SENDER_CONTEXT_ENVELOPE),
        ]
    }

    await middleware.abefore_model(cast(Any, state), MagicMock())

    prompt = classifier.await_args.args[0]
    assert "how's the weather in sf today" in prompt
    assert "This metadata was generated by Open SWE" not in prompt


@pytest.mark.asyncio
async def test_plain_human_message_without_an_envelope_is_still_classified() -> None:
    middleware, _, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    await middleware.abefore_model(cast(Any, state), MagicMock())

    assert "Update the README" in classifier.await_args.args[0]
