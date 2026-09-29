from dataclasses import dataclass
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

import agent.server as server
from agent.dashboard.options import available_requested_models
from agent.middleware.model_selection import (
    ModelSelectionMiddleware,
    ModelSelectionState,
    SelectedRoute,
)
from agent.middleware.prepare_run import PrepareRunState
from agent.model_request import ModelRequestIntent
from agent.utils.thread_settings import ThreadSettings


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
    monkeypatch.setattr(server, "_workspace_admin", AsyncMock(return_value=False))
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
        requested_model_factory=lambda _: MagicMock(),
    )
    middleware._routing_defaults = {"fast": ("openai:gpt-6-luna", "low")}
    return Handoff(middleware, settings, store, record)


@pytest.mark.parametrize("requested", ["anthropic:claude-opus-5-5", None, "inference_failure"])
async def test_initial_handoff_persists_before_work_and_attributes_selected_model(
    handoff: Handoff, monkeypatch: pytest.MonkeyPatch, requested: str | None
) -> None:
    intent = (
        None if requested == "inference_failure" else ModelRequestIntent(requested_model=requested)
    )
    requested = intent.requested_model if intent else None
    infer = AsyncMock(return_value=intent)
    monkeypatch.setattr(server, "infer_requested_model", infer)
    prepared = await handoff.prepare()
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
    assert (await handoff.prepare())["selected_model_id"] == prepared["selected_model_id"]
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
    classify = AsyncMock(return_value=fresh_route)
    monkeypatch.setattr("agent.middleware.model_selection._select_jev_route", classify)
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
        with pytest.raises(error, match=match):
            await handoff.prepare(state)
        select.assert_not_called()
        handoff.record.assert_not_awaited()
        if failure != "persistence":
            handoff.store.assert_not_awaited()
        assert handoff.settings == {"model_id": "openai:gpt-6.1-sol", "repo_instructions": "retain"}
    else:
        assert (await handoff.prepare(state))["selected_model_id"] == model
        handoff.store.assert_awaited_once()
        assert handoff.settings["requested_model"] == model
