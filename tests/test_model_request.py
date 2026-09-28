import asyncio
import contextvars
import json
from unittest.mock import AsyncMock

import httpx2
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


@pytest.mark.parametrize("choice", ["anthropic:claude-opus-5-5", "no_request", "unavailable"])
async def test_classifier_decision_selects_model_or_reports_unavailable(
    monkeypatch: pytest.MonkeyPatch, choice: str
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    observed: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.read())
        observed.append(payload["state"])
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "runtime_model": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.95,
                        "probabilities": {choice: 0.95},
                    }
                },
            },
        )

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kw: client(**kw, transport=httpx2.MockTransport(handle))
    )
    intent = await infer_requested_model(
        messages=[HumanMessage(content="x" * 8_001)],
        requested_models=available_requested_models(fable_enabled=False),
    )
    assert observed == ["x" * 8_000]
    assert intent == ModelRequestIntent(
        requested_model=choice if choice.startswith("anthropic:") else None,
        unavailable_model=choice == "unavailable",
    )


@pytest.mark.parametrize("failure", ["no_credentials", "unknown"])
async def test_failed_classification_leaves_model_selection_to_default_routing(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    for name in (
        "TYPESAFE_API_KEY",
        "LANGSMITH_GATEWAY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    if failure != "no_credentials":
        monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert failure != "no_credentials"
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "runtime_model": {
                        "type": "choice",
                        "choice": "unknown",
                        "confidence": 0.95,
                        "probabilities": {"unknown": 1.0},
                    }
                },
            },
        )

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kw: client(**kw, transport=httpx2.MockTransport(handle))
    )
    assert (
        await infer_requested_model(
            messages=[HumanMessage(content="Use Opus")],
            requested_models=available_requested_models(fable_enabled=False),
        )
        is None
    )


async def test_classifier_deadline_cancels_stalled_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr("agent.utils.jev.JEV_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def handle(request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("Request should have been cancelled")

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kw: client(**kw, transport=httpx2.MockTransport(handle))
    )
    result = await asyncio.wait_for(
        infer_requested_model(
            messages=[HumanMessage(content="Use Opus")],
            requested_models=available_requested_models(fable_enabled=False),
        ),
        timeout=1,
    )
    assert result is None
    assert cancelled.is_set()
