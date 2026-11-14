import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from openswe.dashboard import ttft
from openswe.threads import proxy as thread_proxy


def _event(
    method: str,
    data: dict[str, object],
    *,
    namespace: list[str],
    event_id: str,
) -> dict[str, object]:
    return {
        "type": "event",
        "event_id": event_id,
        "method": method,
        "params": {"namespace": namespace, "timestamp": 2_250, "data": data},
    }


def _lifecycle(run_id: str) -> dict[str, object]:
    return _event(
        "lifecycle",
        {"event": "running"},
        namespace=[],
        event_id=f"synth:{run_id}:lc||running",
    )


def _message(data: dict[str, object], event_id: str = "1-0") -> dict[str, object]:
    return _event("messages", data, namespace=["agent"], event_id=event_id)


def test_detector_ignores_non_ai_and_correlates_later_runs() -> None:
    detector = ttft.AssistantTextEventDetector()
    text_delta = {
        "event": "content-block-delta",
        "index": 0,
        "delta": {"type": "text-delta", "text": "Hello"},
    }

    assert detector.observe(_lifecycle("run-1")) is None
    assert detector.observe(_message({"event": "message-start", "role": "human"})) is None
    assert detector.observe(_message(text_delta)) is None
    assert detector.observe(_message({"event": "message-start", "role": "ai"})) is None
    assert detector.observe(_message(text_delta)) == ttft.AssistantTextObservation(
        run_id="run-1", event_timestamp_ms=2_250
    )
    assert detector.observe(_message({"event": "message-finish"})) is None
    assert detector.observe(_lifecycle("run-2")) is None
    assert detector.observe(_message({"event": "message-start", "role": "ai"})) is None
    assert detector.observe(_message(text_delta, "2-0")) == ttft.AssistantTextObservation(
        run_id="run-2", event_timestamp_ms=2_250
    )


class _SilentThreadStream:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> _SilentThreadStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def subscribe(self, *_: object, **__: object) -> AsyncIterator[dict[str, object]]:
        await asyncio.Event().wait()
        yield {}


async def test_observer_closes_its_stream_when_no_first_token_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _SilentThreadStream()
    client = SimpleNamespace(threads=SimpleNamespace(stream=lambda *_, **__: stream))
    monkeypatch.setattr(thread_proxy, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_proxy, "_TTFT_OBSERVER_TIMEOUT_SECONDS", 0.01)

    await asyncio.wait_for(
        thread_proxy._observe_dashboard_run_ttft("thread", "run", started_at_ms=0), 5
    )

    assert stream.closed
