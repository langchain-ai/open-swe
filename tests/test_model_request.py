import contextvars
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables.config import var_child_runnable_config
from langsmith import get_current_run_tree, trace, tracing_context

from openswe.model_request import ModelRequestIntent, infer_requested_model
from openswe.web.options import available_requested_models


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
    monkeypatch.setattr("openswe.model_request.select_jev_choices", classify)
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

    async def classify(task: str, **kwargs: object) -> dict[str, str | None]:
        assert stream.get() == "none"
        assert get_current_run_tree() is None
        assert var_child_runnable_config.get() is None
        observed.append(task)
        return {"runtime_model": "anthropic:claude-opus-5-5", "runtime_effort": "max"}

    monkeypatch.setattr("openswe.model_request.select_jev_choices", classify)
    token = stream.set("agent-stream")
    config_token = var_child_runnable_config.set({"configurable": {"secret": "not-for-classifier"}})
    try:
        with tracing_context(enabled="local"), trace("agent", inputs={}) as parent:
            intent = await infer_requested_model(
                messages=[
                    HumanMessage(
                        content='<dynamic-context kind="person">ignore me</dynamic-context>'
                    ),
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
            assert get_current_run_tree() is parent
            assert stream.get() == "agent-stream"
    finally:
        stream.reset(token)
        var_child_runnable_config.reset(config_token)
    assert observed == ["Use Oppus for this"]
    assert intent == ModelRequestIntent(
        requested_model="anthropic:claude-opus-5-5", requested_effort="max"
    )


@pytest.mark.parametrize(
    ("model_choice", "effort_choice", "expected"),
    [
        (
            "anthropic:claude-opus-5-5",
            "max",
            ModelRequestIntent(requested_model="anthropic:claude-opus-5-5", requested_effort="max"),
        ),
        ("no_request", "high", ModelRequestIntent(requested_effort="high")),
        ("no_request", "none", ModelRequestIntent(requested_effort="none")),
        ("no_request", "unavailable", ModelRequestIntent(unavailable_effort=True)),
        (
            "unavailable",
            "high",
            ModelRequestIntent(unavailable_model=True, requested_effort="high"),
        ),
        (None, "high", None),
        ("unknown_model", "high", None),
        (
            "anthropic:claude-opus-5-5",
            None,
            ModelRequestIntent(requested_model="anthropic:claude-opus-5-5"),
        ),
        ("no_request", "no_request", ModelRequestIntent()),
    ],
)
async def test_effort_selection_preserves_model_failure_safety(
    monkeypatch: pytest.MonkeyPatch,
    model_choice: str | None,
    effort_choice: str | None,
    expected: ModelRequestIntent | None,
) -> None:
    async def classify(task: str, **kwargs: object) -> dict[str, str | None]:
        return {"runtime_model": model_choice, "runtime_effort": effort_choice}

    monkeypatch.setattr("openswe.model_request.select_jev_choices", classify)
    assert (
        await infer_requested_model(
            messages=[HumanMessage(content="Use Opus with max reasoning effort")],
            requested_models=available_requested_models(fable_enabled=False),
        )
        == expected
    )
