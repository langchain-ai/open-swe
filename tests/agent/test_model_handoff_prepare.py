from dataclasses import dataclass
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx2
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import trace, tracing_context
from langsmith.run_trees import RunTree

import openswe.server as server
from openswe.dashboard.options import available_requested_models
from openswe.middleware.model_selection import (
    ModelSelectionMiddleware,
    ModelSelectionState,
    RouteSelection,
    SelectedRoute,
)
from openswe.middleware.prepare_run import PrepareRunState
from openswe.model_request import ModelRequestIntent
from openswe.utils.jev import JevDecision
from openswe.utils.thread_settings import ThreadSettings


@dataclass
class Handoff:
    middleware: server.PrepareAgentRunMiddleware
    settings: ThreadSettings
    store: AsyncMock
    record: AsyncMock

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
    record = AsyncMock()
    monkeypatch.setattr(server, "load_thread_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(server, "store_thread_settings", store)
    monkeypatch.setattr(server, "record_agent_invocation_usage", record)
    monkeypatch.setattr(server, "resolve_github_token", AsyncMock(return_value=("token", None)))
    monkeypatch.setattr(server, "schedule_thread_title_generation", lambda **kw: None)
    monkeypatch.setattr(
        server,
        "get_or_create_sandbox_backend_proxy",
        lambda _: MagicMock(ready=AsyncMock(return_value=MagicMock(id="sandbox-1"))),
    )
    monkeypatch.setattr(server, "resolve_sandbox_work_dir", AsyncMock(return_value="/workspace"))
    for name in (
        "resolve_triggering_user_identity",
        "load_workspace",
        "_resolve_prompt_default_repo",
        "_resolve_user_custom_instructions",
    ):
        monkeypatch.setattr(server, name, AsyncMock(return_value=None))
    monkeypatch.setattr(server, "_thread_participant_identities", AsyncMock(return_value=[]))
    monkeypatch.setattr(server, "construct_system_prompt", lambda *args, **kw: "system prompt")
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
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "1.2",
                "triggering_user_id": "U1",
                "triggering_event_ts": "2.0",
            },
        }
    }
    middleware = server.PrepareAgentRunMiddleware(
        thread_id="thread-1",
        config=config,
        profile_login=None,
        repo_instructions=None,
        model_id="openai:gpt-6.1-sol",
        effort=None,
        title_model=MagicMock(),
        source="slack",
        user_email="",
        linear_project_id="",
        linear_issue_number="",
        draft_prs=False,
        recent_thread_context_enabled=False,
        admin_workspaces=False,
    )
    middleware._requested_models = available_requested_models(fable_enabled=False)
    middleware._model_selection = ModelSelectionMiddleware(
        {"fast": MagicMock()},
        MagicMock(),
        routing_mode="fast",
        requested_model_factory=lambda _model, _effort: MagicMock(),
    )
    middleware._routing_defaults = {"fast": ("openai:gpt-6-luna", "low")}
    return Handoff(middleware, settings, store, record)


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
    assert prepared["selected_model_id"] == (requested or "openai:gpt-6-luna")
    assert prepared["selected_effort"] == ("high" if requested else "low")
    assert prepared["requested_model"] == requested
    assert handoff.record.call_args.kwargs["model_id"] == prepared["selected_model_id"]
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


@pytest.mark.parametrize("previous_route", ["fast", "default"])
@pytest.mark.parametrize("fresh_route", ["fast", "default"])
async def test_explicit_auto_selection_replaces_checkpoint_route(
    handoff: Handoff,
    monkeypatch: pytest.MonkeyPatch,
    previous_route: SelectedRoute,
    fresh_route: SelectedRoute,
) -> None:
    handoff.middleware._config["configurable"].update(
        source="dashboard", model_selection="auto", model_selection_changed=True
    )
    handoff.middleware._requested_models = None
    selection = handoff.middleware._model_selection
    assert selection is not None
    selection._routing_mode = "auto"
    classify = AsyncMock(return_value=RouteSelection(fresh_route))
    monkeypatch.setattr("openswe.middleware.model_selection._select_jev_route", classify)
    state: ModelSelectionState = {
        "messages": [HumanMessage(content="Fix the typo")],
        "model_route": previous_route,
        "requested_model": "anthropic:claude-opus-5-5",
    }
    prepared = await handoff.prepare(cast(PrepareRunState, state))
    state.update(cast(ModelSelectionState, prepared))
    assert state["requested_model"] is None
    assert state["model_route"] == fresh_route
    assert prepared["selected_model_id"] == (
        "openai:gpt-6-luna" if fresh_route == "fast" else "openai:gpt-6.1-sol"
    )
    assert (await selection.abefore_model(state, MagicMock()))["model_route"] == fresh_route
    classify.assert_awaited_once_with("Fix the typo")


async def test_prepare_auth_fallback_attributes_default_without_pinning_route(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff.middleware._requested_models = None
    selection = handoff.middleware._model_selection
    assert selection is not None
    selection._routing_mode = "auto"
    monkeypatch.setenv("TYPESAFE_API_KEY", "rejected-key")
    monkeypatch.delenv("LANGSMITH_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(403)
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "fast",
                        "confidence": 0.95,
                        "probabilities": {"fast": 1.0},
                    }
                },
            },
        )

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: client(**kwargs, transport=httpx2.MockTransport(handle)),
    )
    state: PrepareRunState = {"messages": [HumanMessage(content="Fix the typo")]}
    prepared = await handoff.prepare(state)
    assert "model_route" not in prepared
    assert prepared["selected_model_id"] == "openai:gpt-6.1-sol"
    assert handoff.record.call_args.kwargs["model_id"] == "openai:gpt-6.1-sol"
    assert (
        handoff.middleware._config["configurable"]["resolved_agent_model_id"]
        == "openai:gpt-6.1-sol"
    )
    state.update(cast(PrepareRunState, prepared))
    assert len(requests) == 1
    recovered = await handoff.prepare(state)
    assert recovered["model_route"] == "fast"
    assert recovered["selected_model_id"] == "openai:gpt-6-luna"
    state.update(cast(PrepareRunState, recovered))
    assert await selection.abefore_model(cast(ModelSelectionState, state), MagicMock()) == {
        "model_route": "fast"
    }
    assert len(requests) == 2


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
        select = MagicMock()
        monkeypatch.setattr(handoff.middleware._model_selection, "use_requested_model", select)
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
        select.assert_not_called()
        handoff.record.assert_not_awaited()
        if failure != "persistence":
            handoff.store.assert_not_awaited()
        assert handoff.settings == {"model_id": "openai:gpt-6.1-sol", "repo_instructions": "retain"}
    else:
        assert (await handoff.prepare(state))["selected_model_id"] == model
        handoff.store.assert_awaited_once()
        assert handoff.settings["requested_model"] == model


async def test_later_run_traces_saved_choice_without_classification(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = "anthropic:claude-opus-5-5"
    handoff.middleware._requested_models = None
    handoff.middleware._saved_requested_model = model
    handoff.middleware._model_id = model
    handoff.middleware._effort = "high"
    assert handoff.middleware._model_selection is not None
    handoff.middleware._model_selection._routing_mode = None
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
    assert handoff.record.call_args.kwargs["effort"] == effort
    handoff.store.assert_awaited_once()
