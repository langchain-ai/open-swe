import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from unittest.mock import AsyncMock

import httpx2
import pytest
from langchain_core.messages import HumanMessage

from openswe.dashboard.options import available_requested_models
from openswe.model_request import ModelRequestIntent, infer_requested_model
from openswe.utils.jev import JevDecision, select_jev_choice

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


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_credentials_report_all_decisions_without_gateway(
    transport: InstallTransport, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status)

    transport(handle)
    model_decision, effort_decision = JevDecision(), JevDecision()
    with caplog.at_level(logging.ERROR):
        assert (
            await infer_requested_model(
                messages=[HumanMessage("Use Opus with max reasoning effort")],
                requested_models=available_requested_models(fable_enabled=False),
                decision=model_decision,
                effort_decision=effort_decision,
            )
            is None
        )
    assert len(requests) == 1
    for decision in (model_decision, effort_decision):
        assert decision.outcome == "classifier_failure"
        assert decision.reason == "classifier_auth_failure"
    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert record.message == "Jev classifier credential rejected"
    assert record.__dict__["status_code"] == status
    assert record.__dict__["classifier_transport"] == "direct"
    assert record.__dict__["questions"] == ["runtime_model", "runtime_effort"]
    assert "test-key" not in caplog.text


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("response_status", [False, True])
async def test_http_authentication_exceptions_are_recognized(
    monkeypatch: pytest.MonkeyPatch,
    transport: InstallTransport,
    status: int,
    response_status: bool,
) -> None:
    error = (
        httpx2.HTTPStatusError(
            "Rejected",
            request=httpx2.Request("POST", "https://example.com"),
            response=httpx2.Response(status),
        )
        if response_status
        else type("AuthenticationError", (Exception,), {"status_code": status})("Rejected")
    )
    monkeypatch.setattr(
        "openswe.utils.jev.TypeSafeClassifier.ainvoke", AsyncMock(side_effect=error)
    )
    decision = JevDecision()
    assert await classify(decision) is None
    assert decision.reason == "classifier_auth_failure"


@pytest.mark.parametrize("gateway_env", ["LANGSMITH_GATEWAY_API_KEY", "LANGSMITH_API_KEY"])
@pytest.mark.parametrize(
    "choice,status", [("fast", 200), ("balanced", 200), (None, 403), (None, 503)]
)
async def test_rejected_direct_credential_retries_gateway_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    transport: InstallTransport,
    gateway_env: str,
    choice: str | None,
    status: int,
) -> None:
    monkeypatch.setenv(gateway_env, "gateway-key")
    if gateway_env == "LANGSMITH_GATEWAY_API_KEY":
        monkeypatch.setenv("LANGSMITH_API_KEY", "other-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gateway.example.com/")
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(403)
        return httpx2.Response(
            status,
            json={
                "model": "typesafe/jev-1.13.0",
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": choice or "fast",
                        "confidence": 0.95,
                        "probabilities": {choice or "fast": 1.0},
                    }
                },
            },
        )

    transport(handle)
    decision = JevDecision()
    assert (
        await select_jev_choice(
            "Fix the bug",
            question="route",
            instructions="Choose a route",
            criteria={"fast": "Small task", "balanced": "Medium task"},
            decision=decision,
        )
        == choice
    )
    assert len(requests) == 2
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert requests[1].url == "https://gateway.example.com/v1/systemone"
    assert requests[1].headers["Authorization"] == "Bearer gateway-key"
    assert json.loads(requests[1].read())["model"] == "typesafe/jev-1.13.0"
    assert decision.outcome == ("accepted" if choice else "classifier_failure")
    assert decision.reason == ("confident_choice" if choice else "classifier_auth_failure")


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
