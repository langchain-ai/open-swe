import asyncio
import json
from collections.abc import Callable, Coroutine

import httpx2
import pytest
from langchain_core.messages import HumanMessage

from openswe.model_request import ModelRequestIntent, infer_requested_model
from openswe.utils.jev import JevDecision, select_jev_choice
from openswe.web.options import available_requested_models

type ResponseHandler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)
type InstallTransport = Callable[[ResponseHandler], None]


@pytest.fixture
def transport(
    monkeypatch: pytest.MonkeyPatch,
) -> InstallTransport:
    for name in (
        "TYPESAFE_API_KEY",
        "LANGSMITH_GATEWAY_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    client = httpx2.AsyncClient

    def install(handler: ResponseHandler) -> None:
        monkeypatch.setattr(
            httpx2,
            "AsyncClient",
            lambda **kw: client(**kw, transport=httpx2.MockTransport(handler)),
        )

    return install


async def classify(decision: JevDecision | None = None) -> str | None:
    return await select_jev_choice(
        "Fix the bug",
        question="route",
        instructions="Choose a route",
        criteria={"fast": "Small task"},
        decision=decision,
    )


@pytest.mark.parametrize(
    ("choice", "confidence", "status", "expected"),
    [
        ("fast", 0.95, 200, "fast"),
        ("fast", 0.6, 200, "fast"),
        ("fast", 0.59, 200, None),
        ("fast", -0.1, 200, None),
        ("fast", 1.1, 200, None),
        ("unknown", 0.95, 200, None),
        ("fast", 0.95, 503, None),
    ],
)
async def test_classification_accepts_only_confident_valid_answers(
    transport: InstallTransport, choice: str, confidence: float, status: int, expected: str | None
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.read())["state"] == "Fix the bug"
        return httpx2.Response(
            status,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": confidence,
                        "probabilities": {choice: 1.0},
                    }
                },
            },
        )

    transport(handle)
    decision = JevDecision()
    assert await classify(decision) == expected
    valid_response = status == 200 and 0 <= confidence <= 1
    assert decision.choice == (choice if valid_response and choice == "fast" else None)
    assert decision.confidence == (confidence if valid_response else None)
    assert decision.outcome == (
        "accepted"
        if expected
        else "low_confidence"
        if valid_response and confidence < 0.6
        else "classifier_failure"
    )
    if not valid_response or choice == "unknown":
        assert decision.reason == ("classifier_error" if not valid_response else "invalid_choice")


async def test_missing_credentials_skips_classification(
    monkeypatch: pytest.MonkeyPatch, transport: InstallTransport
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY")

    def handle(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Classification must not send a request without credentials")

    transport(handle)
    decision = JevDecision()
    assert await classify(decision) is None
    assert decision == JevDecision(outcome="classifier_failure", reason="missing_credentials")


async def test_classifier_deadline_cancels_stalled_request(
    monkeypatch: pytest.MonkeyPatch, transport: InstallTransport
) -> None:
    monkeypatch.setattr("openswe.utils.jev.JEV_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def handle(request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("Request should have been cancelled")

    transport(handle)
    decision = JevDecision()
    assert await asyncio.wait_for(classify(decision), timeout=1) is None
    assert decision.outcome == "classifier_failure"
    assert cancelled.is_set()


@pytest.mark.parametrize("failed_question", [None, "runtime_model", "runtime_effort"])
@pytest.mark.parametrize("failure", ["low_confidence", "missing"])
async def test_opening_model_and_effort_share_one_request_with_independent_decisions(
    transport: InstallTransport, failed_question: str | None, failure: str
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = json.loads(request.read())
        assert set(payload["questions"]) == {"runtime_model", "runtime_effort"}
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    question: {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.5 if question == failed_question else 0.95,
                        "probabilities": {choice: 1.0},
                    }
                    for question, choice in {
                        "runtime_model": "anthropic:claude-opus-5-5",
                        "runtime_effort": "max",
                    }.items()
                    if question != failed_question or failure != "missing"
                },
            },
        )

    transport(handle)
    model_decision, effort_decision = JevDecision(), JevDecision()
    intent = await infer_requested_model(
        messages=[HumanMessage("Use Opus with max reasoning effort")],
        requested_models=available_requested_models(fable_enabled=False),
        decision=model_decision,
        effort_decision=effort_decision,
    )
    assert len(requests) == 1
    assert intent == (
        None
        if failed_question == "runtime_model"
        else ModelRequestIntent(
            requested_model="anthropic:claude-opus-5-5",
            requested_effort=None if failed_question == "runtime_effort" else "max",
        )
    )
    for question, decision in {
        "runtime_model": model_decision,
        "runtime_effort": effort_decision,
    }.items():
        assert decision.outcome == (
            ("low_confidence" if failure == "low_confidence" else "classifier_failure")
            if question == failed_question
            else "accepted"
        )
