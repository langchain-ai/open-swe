from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import trace, tracing_context
from langsmith.run_trees import RunTree

import openswe.server as server
from openswe.agent_api.run_state import as_messages
from openswe.agent_api.tokens import AgentTurnSettings
from openswe.input_messages import human_input
from openswe.middleware.model_selection import (
    ModelSelectionMiddleware,
    RouteSelection,
    SelectedRoute,
)
from openswe.middleware.prepare_run import PrepareRunState
from openswe.model_request import ModelRequestIntent
from openswe.prompt import SystemPrompt
from openswe.utils.jev import JevDecision
from openswe.utils.thread_settings import ThreadSettings


def _said(text: str, message_id: str | None = None) -> HumanMessage:
    """What a person wrote, as the backend builds it."""
    [message] = as_messages(
        [human_input(text, {"sender_id": "github:alice", "surface": "web", "kind": "human"})]
    )
    assert isinstance(message, HumanMessage)
    message.id = message_id or message.id
    return message


@dataclass
class Handoff:
    middleware: server.PrepareAgentRunMiddleware
    settings: ThreadSettings
    store: AsyncMock

    async def prepare(self, state: PrepareRunState | None = None) -> dict[str, object]:
        return await self.middleware._prepare(state or {"messages": []}, MagicMock())


@pytest.fixture
def handoff(monkeypatch: pytest.MonkeyPatch) -> Handoff:
    settings: ThreadSettings = {"model_id": "openai:gpt-6.1-sol", "repo_instructions": "retain"}

    async def persist(
        client: object, thread_id: str, value: ThreadSettings, *, strict: bool
    ) -> None:
        settings.update(value)

    store = AsyncMock(side_effect=persist)
    monkeypatch.setattr(server, "load_thread_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(server, "store_thread_settings", store)
    monkeypatch.setattr(server, "resolve_github_token", AsyncMock(return_value=("token", None)))
    monkeypatch.setattr(server, "schedule_thread_title_generation", lambda **kw: None)
    monkeypatch.setattr(server, "get_sandbox_metadata", AsyncMock(return_value={}))
    for name in (
        "resolve_triggering_user_identity",
        "load_workspace",
        "_resolve_prompt_default_repo",
        "_resolve_user_custom_instructions",
    ):
        monkeypatch.setattr(server, name, AsyncMock(return_value=None))
    monkeypatch.setattr(server, "_thread_participant_identities", AsyncMock(return_value=[]))
    monkeypatch.setattr(server.THREADS, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        server, "construct_system_prompt", lambda *args, **kw: SystemPrompt("system prompt", "")
    )
    monkeypatch.setattr(
        server,
        "client",
        MagicMock(
            threads=MagicMock(
                update=AsyncMock(),
                get=AsyncMock(return_value={"metadata": {"visibility": "public"}}),
            )
        ),
    )
    config: RunnableConfig = {
        "configurable": {
            "thread_id": "thread-1",
            "source": "slack",
            "invocation_id": "inv-1",
            "resolved_agent_model_id": "openai:gpt-6.1-sol",
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.2",
                "triggering_user_id": "U1",
                "triggering_event_ts": "2.0",
            },
        }
    }
    turn = AgentTurnSettings(
        title_model_id="openai:gpt-6-luna",
        route_models={"fast": ("openai:gpt-6-luna", "low")},
        offers_requested_models=True,
    )
    middleware = server.PrepareAgentRunMiddleware(
        config, turn, repo_instructions=None, sandbox_id="sandbox-1"
    )
    return Handoff(middleware, settings, store)


@pytest.mark.parametrize(
    "requested", ["anthropic:claude-opus-5-5", None, "inference_failure", "low_confidence"]
)
async def test_initial_handoff_persists_before_work_and_attributes_selected_model(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, requested: str | None
) -> None:
    intent = (
        None
        if requested in {"inference_failure", "low_confidence"}
        else ModelRequestIntent(requested_model=requested)
    )
    outcome = requested
    requested = intent.requested_model if intent else None

    async def infer_intent(*, decision: JevDecision, **kwargs: object) -> ModelRequestIntent | None:
        if outcome == "inference_failure":
            decision.outcome = "classifier_failure"
            decision.reason = "classifier_error"
        else:
            decision.choice = requested or ("no_request" if intent else "anthropic:claude-opus-5-5")
            decision.confidence = 0.4 if outcome == "low_confidence" else 0.95
            decision.outcome = "low_confidence" if outcome == "low_confidence" else "accepted"
            decision.reason = (
                "confidence_below_threshold_or_invalid"
                if outcome == "low_confidence"
                else "confident_choice"
            )
        return intent

    infer = AsyncMock(side_effect=infer_intent)
    monkeypatch.setattr(server, "infer_requested_model", infer)
    with (
        tracing_context(enabled="local"),
        trace("agent", inputs={}, metadata={"title_seed": "private opening request"}) as parent,
    ):
        prepared = await handoff.prepare()
    (decision_span,) = parent.child_runs
    assert decision_span.name == "Model selection decision"
    assert decision_span.parent_run_id == parent.id
    assert decision_span.trace_id == parent.trace_id
    assert decision_span.inputs == {}
    assert "title_seed" not in decision_span.metadata
    assert parent.metadata["title_seed"] == "private opening request"
    outputs = decision_span.outputs
    assert outputs is not None
    assert outputs["selected_model_id"] == prepared["selected_model_id"]
    assert outputs["selected_effort"] == prepared["selected_effort"]
    assert outputs["requested_model"] == requested
    assert outputs["pin_persisted"] is bool(requested)
    assert outputs["outcome"] == (
        "accepted_request"
        if requested
        else "classifier_failure"
        if outcome == "inference_failure"
        else "low_confidence"
        if outcome == "low_confidence"
        else "no_request"
    )
    assert outputs["classifier"]["confidence"] == (
        None if outcome == "inference_failure" else 0.4 if outcome == "low_confidence" else 0.95
    )
    assert handoff.settings["model_handoff_complete"] is True
    assert handoff.settings["repo_instructions"] == "retain"
    # The run routes the thread itself: preparing attributes the run's own model.
    assert prepared["selected_model_id"] == (requested or "openai:gpt-6.1-sol")
    assert prepared["selected_effort"] == ("high" if requested else None)
    assert prepared["requested_model"] == requested
    if requested:
        assert handoff.settings["model_id"] == requested
        assert handoff.settings["model_routing_enabled"] is False
    handoff.store.assert_awaited_once_with(server.client, "thread-1", handoff.settings, strict=True)
    with tracing_context(enabled="local"), trace("agent", inputs={}) as later:
        assert (await handoff.prepare())["selected_model_id"] == prepared["selected_model_id"]
    (reused,) = later.child_runs
    assert reused.outputs is not None
    assert reused.outputs["outcome"] == ("reused_saved_choice" if requested else "not_classified")
    assert reused.outputs["classifier"]["outcome"] == "not_run"
    infer.assert_awaited_once()


async def test_a_saved_choice_no_longer_offered_falls_back_to_the_runs_model(
    handoff: Handoff,
) -> None:
    handoff.settings.update(
        model_handoff_complete=True, requested_model="google_genai:gemini-4-argon"
    )
    prepared = await handoff.prepare()
    assert prepared["selected_model_id"] == "openai:gpt-6.1-sol"
    assert prepared["requested_model"] is None


@pytest.mark.parametrize("previous_route", ["fast", "default"])
async def test_explicit_auto_selection_has_the_run_classify_the_route_again(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, previous_route: SelectedRoute
) -> None:
    handoff.middleware._config["configurable"].update(
        source="dashboard", model_selection="auto", model_selection_changed=True
    )
    handoff.middleware._requested_models = None
    classify = AsyncMock(side_effect=AssertionError("The run classifies its route itself"))
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", classify)
    state = {
        "messages": [HumanMessage(content="Fix the typo")],
        "model_route": previous_route,
        "requested_model": "anthropic:claude-opus-5-5",
    }

    prepared = await handoff.prepare(cast(PrepareRunState, state))

    # The route kept from before is dropped, so the run's own classifier decides again.
    assert prepared["requested_model"] is None
    assert prepared["model_route"] is None
    assert prepared["selected_model_id"] == "openai:gpt-6.1-sol"


async def test_preparing_a_turn_never_classifies_its_route(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff.middleware._requested_models = None
    classify = AsyncMock(side_effect=AssertionError("The run classifies its route itself"))
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", classify)

    prepared = await handoff.prepare({"messages": [HumanMessage(content="Fix the typo")]})

    assert "model_route" not in prepared
    assert prepared["selected_model_id"] == "openai:gpt-6.1-sol"
    assert (
        handoff.middleware._config["configurable"]["resolved_agent_model_id"]
        == "openai:gpt-6.1-sol"
    )


@pytest.mark.parametrize("persist", [True, False])
async def test_a_run_its_plan_answers_is_routed_and_attributed_as_it_prepares(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, persist: bool
) -> None:
    """A requested offload or incident runs no MDA loop, so no classifier there routes it."""
    handoff.middleware._requested_models = None
    handoff.middleware._model_selection = ModelSelectionMiddleware(
        {"fast": "openai:gpt-6-luna", "default": "openai:gpt-6.1-sol"}, routing_mode="auto"
    )
    classify = AsyncMock(return_value=RouteSelection("fast", persist))
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", classify)

    prepared = await handoff.prepare({"messages": [_said("Fix the typo")]})

    # run_started, Slack replies and PR footers name the routed model.
    assert (prepared["selected_model_id"], prepared["selected_effort"]) == (
        "openai:gpt-6-luna",
        "low",
    )
    assert handoff.middleware._config["configurable"]["resolved_agent_model_id"] == (
        "openai:gpt-6-luna"
    )
    assert prepared.get("model_route") == ("fast" if persist else None)
    classify.assert_awaited_once_with("Fix the typo")


@pytest.mark.parametrize(
    ("model", "image_type", "failure"),
    [
        ("fireworks:accounts/fireworks/models/kimi-k3", "image", "image"),
        ("fireworks:accounts/fireworks/models/kimi-k3", "image_url", "image"),
        ("fireworks:accounts/fireworks/models/kimi-k3", None, None),
        ("anthropic:claude-opus-5-5", "image", None),
        ("anthropic:claude-fable-5-1", None, "unavailable"),
        ("openai:gpt-6.1-sol", None, "unavailable"),
        ("openai:gpt-6.1-sol", None, "persistence"),
    ],
)
async def test_handoff_validates_and_persists_before_selecting_model(
    handoff: Handoff,
    monkeypatch: pytest.MonkeyPatch,
    image_type: str | None,
    model: str,
    failure: str | None,
) -> None:
    monkeypatch.setattr(
        server,
        "infer_requested_model",
        AsyncMock(
            return_value=ModelRequestIntent(
                requested_model=model,
                unavailable_model=failure == "unavailable" and model == "openai:gpt-6.1-sol",
            )
        ),
    )
    if failure == "persistence":
        handoff.store.side_effect = RuntimeError("write failed")
    content: list[str | dict[str, object]] = [{"type": "text", "text": "Use this model to inspect"}]
    if image_type == "image":
        content.append({"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"})
    elif image_type == "image_url":
        content.append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
        )
    state: PrepareRunState = {"messages": [HumanMessage(content=content)]}
    if failure:
        error = RuntimeError if failure == "persistence" else ValueError
        match = {
            "persistence": "write failed",
            "image": "does not support image input",
            "unavailable": "unavailable",
        }[failure]
        with tracing_context(enabled="local"), trace("agent", inputs={}) as parent:
            with pytest.raises(error, match=match):
                await handoff.prepare(state)
        (decision_span,) = parent.child_runs
        assert decision_span.outputs is not None
        assert decision_span.outputs["selected_model_id"] is None
        assert decision_span.outputs["pin_persisted"] is False
        assert (
            decision_span.outputs["outcome"]
            == {
                "image": "incompatible_request",
                "unavailable": "unavailable_request",
                "persistence": "persistence_failure",
            }[failure]
        )
        if failure != "persistence":
            handoff.store.assert_not_awaited()
        assert handoff.settings == {"model_id": "openai:gpt-6.1-sol", "repo_instructions": "retain"}
    else:
        assert (await handoff.prepare(state))["selected_model_id"] == model
        handoff.store.assert_awaited_once()
        assert handoff.settings["requested_model"] == model


@pytest.mark.parametrize(("effort", "expected_effort"), [("no_request", "low"), ("max", "max")])
async def test_performance_handoff_uses_tier_effort_unless_overridden(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, effort: str, expected_effort: str
) -> None:
    model = "anthropic:claude-opus-5-5"
    handoff.middleware._routing_defaults["performance"] = (model, "low")
    monkeypatch.setattr(
        "openswe.model_request.JEV",
        SimpleNamespace(
            choose=AsyncMock(
                return_value={"runtime_model": "performance", "runtime_effort": effort}
            )
        ),
    )
    state: PrepareRunState = {
        "messages": [
            HumanMessage(
                content='<input-message sender="slack:U1" kind="human" timestamp="2.0">Use perf</input-message>'
            )
        ]
    }
    prepared = await handoff.prepare(state)
    assert prepared["selected_model_id"] == model
    assert prepared["selected_effort"] == expected_effort


async def test_later_run_traces_saved_choice_without_classification(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = "anthropic:claude-opus-5-5"
    handoff.middleware._requested_models = None
    handoff.middleware._saved_requested_model = model
    handoff.middleware._model_id = model
    handoff.middleware._effort = "high"
    infer = AsyncMock()
    monkeypatch.setattr(server, "infer_requested_model", infer)
    posted: list[RunTree] = []

    def capture(span: RunTree, **kwargs: object) -> None:
        assert span.inputs == {}
        assert span.metadata == {"ls_method": "trace"}
        posted.append(span)

    monkeypatch.setattr(RunTree, "post", capture)
    monkeypatch.setattr(RunTree, "patch", capture)
    tracing_client = MagicMock()
    parent_id = uuid4()
    metadata = {
        "title_seed": "private opening request",
        "prompt": "private system prompt",
        "credentials": "synthetic-secret",
    }
    with tracing_context(enabled=True, client=tracing_client):
        prepared = await RunnableLambda(handoff.prepare).ainvoke(
            {"messages": [HumanMessage(content="private conversation")]},
            config={
                "run_id": parent_id,
                "metadata": metadata,
                "callbacks": [LangChainTracer(client=tracing_client)],
            },
        )
    decision_span = posted[0]
    assert len(posted) == 2
    assert decision_span.name == "Model selection decision"
    assert decision_span.parent_run_id == parent_id
    assert decision_span.trace_id == parent_id
    assert decision_span.outputs is not None
    assert decision_span.outputs["outcome"] == "reused_saved_choice"
    assert decision_span.outputs["requested_model"] == model
    assert decision_span.outputs["selected_model_id"] == prepared["selected_model_id"] == model
    assert decision_span.outputs["pin_persisted"] is True
    assert decision_span.outputs["classifier"]["outcome"] == "not_run"
    infer.assert_not_awaited()
    handoff.store.assert_not_awaited()


@pytest.mark.parametrize(
    ("model", "effort", "invalid"),
    [
        ("anthropic:claude-opus-5-5", "max", False),
        (None, "low", False),
        ("openai:gpt-6.1-sol", "none", False),
        ("openai:gpt-6.1-sol", "max", True),
        (None, "max", True),
    ],
)
async def test_requested_effort_is_validated_persisted_and_reused(
    handoff: Handoff,
    monkeypatch: pytest.MonkeyPatch,
    model: str | None,
    effort: str,
    invalid: bool,
) -> None:
    monkeypatch.setattr(
        server,
        "infer_requested_model",
        AsyncMock(return_value=ModelRequestIntent(requested_model=model, requested_effort=effort)),
    )
    if invalid:
        with pytest.raises(ValueError, match="not supported"):
            await handoff.prepare()
        handoff.store.assert_not_awaited()
        return
    selected_model = model or "openai:gpt-6.1-sol"
    for _ in range(2):
        prepared = await handoff.prepare()
        assert prepared["selected_model_id"] == selected_model
        assert prepared["selected_effort"] == effort
        assert prepared["requested_effort"] == effort
    assert handoff.settings["model_id"] == selected_model
    assert handoff.settings["effort"] == effort
    assert handoff.settings["model_routing_enabled"] is False
    handoff.store.assert_awaited_once()
