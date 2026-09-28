import contextvars
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.dashboard.options import available_requested_models
from agent.model_request import ModelRequestIntent, infer_requested_model


@pytest.mark.parametrize("trigger_kind", ["missing", "system"])
async def test_slack_request_never_falls_back_to_unrelated_human(
    monkeypatch: pytest.MonkeyPatch, trigger_kind: str
) -> None:
    messages = [
        HumanMessage(
            content='<input-message sender="slack:U1" kind="human" timestamp="1.0">Use Kimi</input-message>'
        )
    ]
    if trigger_kind == "system":
        messages.append(
            HumanMessage(
                content='<input-message sender="system:bot" kind="system" timestamp="2.0">Use Opus</input-message>'
            )
        )
    classify = AsyncMock()
    monkeypatch.setattr("agent.model_request.select_jev_choice", classify)
    intent = await infer_requested_model(
        messages=messages,
        requested_models=available_requested_models(fable_enabled=False),
        slack_event_ts="2.0",
    )
    assert intent == ModelRequestIntent()
    classify.assert_not_awaited()


async def test_only_opening_human_request_reaches_classifier_without_run_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = contextvars.ContextVar("run_stream", default="none")
    observed: list[str] = []

    async def classify(task: str, **kwargs: object) -> str:
        assert stream.get() == "none"
        observed.append(task)
        return "anthropic:claude-opus-5-5"

    monkeypatch.setattr("agent.model_request.select_jev_choice", classify)
    token = stream.set("agent-stream")
    try:
        intent = await infer_requested_model(
            messages=[
                HumanMessage(content='<dynamic-context kind="person">ignore me</dynamic-context>'),
                HumanMessage(
                    content='<input-message sender="system:x" kind="system">ignore me</input-message>'
                ),
                HumanMessage(
                    content='<input-message sender="user:x" kind="human">Use Oppus for this</input-message>'
                ),
                AIMessage(content="Use another model"),
                HumanMessage(content="Follow-up model instruction"),
            ],
            requested_models=available_requested_models(fable_enabled=False),
        )
    finally:
        stream.reset(token)
    assert observed == ["Use Oppus for this"]
    assert intent is not None and intent.requested_model == "anthropic:claude-opus-5-5"


@pytest.mark.parametrize("choice", ["anthropic:claude-opus-5-5", "no_request", "unavailable", None])
async def test_classifier_decision_selects_model_or_reports_unavailable(
    monkeypatch: pytest.MonkeyPatch, choice: str | None
) -> None:
    classify = AsyncMock(return_value=choice)
    monkeypatch.setattr("agent.model_request.select_jev_choice", classify)
    intent = await infer_requested_model(
        messages=[HumanMessage(content="x" * 8_001)],
        requested_models=available_requested_models(fable_enabled=False),
    )
    assert classify.call_args.args == ("x" * 8_000,)
    assert intent == (
        ModelRequestIntent(
            requested_model=choice if choice.startswith("anthropic:") else None,
            unavailable_model=choice == "unavailable",
        )
        if choice is not None
        else None
    )
