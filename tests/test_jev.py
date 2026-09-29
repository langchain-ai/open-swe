import asyncio
import json
from collections.abc import Callable, Coroutine

import httpx2
import pytest

from agent.utils.jev import JevDecision, select_jev_choice

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
    monkeypatch.setattr("agent.utils.jev.JEV_TIMEOUT_SECONDS", 0.01)
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
