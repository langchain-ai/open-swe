from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, ToolMessage

from agent.middleware.model_selection import (
    ModelSelectionMiddleware,
    RouteDecision,
    fast_alt_bucket,
)


def _middleware(
    route: Literal["fast", "balanced", "performance"] = "fast",
    *,
    route_model_ids: dict[str, str] | None = None,
    fast_alt: bool = False,
    fast_alt_probability: float = 0.5,
    routing_mode: Literal["auto", "performant"] = "auto",
    thread_id: str | None = None,
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock], AsyncMock]:
    profiles = (
        ("fast", "fast_alt", "balanced", "performance")
        if fast_alt
        else (
            "fast",
            "balanced",
            "performance",
        )
    )
    models = {profile: MagicMock(name=profile) for profile in profiles}
    structured = AsyncMock(return_value=RouteDecision(model_route=route))
    classifier = MagicMock()
    classifier.tags = None
    tagged = classifier.model_copy.return_value
    tagged.with_structured_output.return_value.ainvoke = structured
    middleware = ModelSelectionMiddleware(
        cast(Any, models),
        classifier,
        route_model_ids=route_model_ids,
        fast_alt_probability=fast_alt_probability,
        routing_mode=routing_mode,
        thread_id=thread_id,
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
async def test_performant_mode_skips_classifier_and_routing_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark_applied = MagicMock()
    monkeypatch.setattr(
        "agent.middleware.model_selection._mark_model_routing_applied",
        mark_applied,
    )
    middleware, models, classifier = _middleware(routing_mode="performant")
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "performance"
    assert (await _invoke(middleware, state)).model is models["performance"]
    classifier.assert_not_awaited()
    mark_applied.assert_not_called()


@pytest.mark.asyncio
async def test_routing_decision_only_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark_applied = MagicMock()
    monkeypatch.setattr(
        "agent.middleware.model_selection._mark_model_routing_applied",
        mark_applied,
    )
    middleware, _, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))
    await middleware.abefore_model(cast(Any, state), MagicMock())

    classifier.assert_awaited_once()
    mark_applied.assert_called_once_with()


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
async def test_explicit_plan_mode_overrides_stale_state_without_caching() -> None:
    middleware, _, classifier = _middleware()
    state = {
        "messages": [HumanMessage(content="Implement the approved plan")],
        "plan_mode": True,
    }

    assert await middleware.select_route(cast(Any, state), plan_mode=False) == "fast"
    assert await middleware.select_route(cast(Any, state), plan_mode=True) == "performance"
    classifier.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_plan_mode_uses_performance_without_routing_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agent.middleware.model_selection.get_stream_writer",
        lambda: events.append,
    )
    middleware, models, classifier = _middleware(
        route_model_ids={
            "fast": "openai:gpt-5.6-sol",
            "performance": "anthropic:claude-opus-5",
        }
    )
    entered_plan_mode = {
        "messages": [HumanMessage(content="Plan the next change")],
        "model_route": "fast",
        "plan_mode": True,
    }
    started_in_plan_mode = {
        "messages": [HumanMessage(content="Plan the next change")],
        "plan_mode": True,
    }

    assert await middleware.abefore_model(cast(Any, entered_plan_mode), MagicMock()) == {}
    assert await middleware.abefore_model(cast(Any, started_in_plan_mode), MagicMock()) == {}

    assert events == []
    classifier.assert_not_awaited()
    assert (await _invoke(middleware, entered_plan_mode)).model is models["performance"]
    assert (await _invoke(middleware, started_in_plan_mode)).model is models["performance"]


@pytest.mark.asyncio
async def test_fast_route_splits_between_fast_and_fast_alt_models() -> None:
    middleware, models, _ = _middleware(fast_alt=True, fast_alt_probability=1.0, thread_id="t")
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast_alt"
    assert (await _invoke(middleware, state)).model is models["fast_alt"]


@pytest.mark.asyncio
async def test_fast_alt_is_never_picked_without_a_split() -> None:
    middleware, models, _ = _middleware(fast_alt=False, fast_alt_probability=1.0)
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_model(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]


def test_fast_alt_bucket_is_deterministic_per_thread() -> None:
    assert fast_alt_bucket("thread-a") == fast_alt_bucket("thread-a")
    assert fast_alt_bucket(None) == fast_alt_bucket(None)
    assert fast_alt_bucket("thread-a") != fast_alt_bucket("thread-b")
    assert 0.0 <= fast_alt_bucket("thread-a") < 1.0


@pytest.mark.asyncio
async def test_fast_alt_split_is_repeatable_for_a_thread() -> None:
    thread_id = "repeatable-thread"
    probability = fast_alt_bucket(thread_id)
    first, _, _ = _middleware(
        fast_alt=True, fast_alt_probability=probability + 0.001, thread_id=thread_id
    )
    second, _, _ = _middleware(
        fast_alt=True, fast_alt_probability=probability + 0.001, thread_id=thread_id
    )
    state = {"messages": [HumanMessage(content="Update the README")]}

    first_state: dict[str, Any] = {**state}
    second_state: dict[str, Any] = {**state}
    first_state.update(await first.abefore_model(cast(Any, first_state), MagicMock()))
    second_state.update(await second.abefore_model(cast(Any, second_state), MagicMock()))

    assert first_state["model_route"] == second_state["model_route"] == "fast_alt"


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
