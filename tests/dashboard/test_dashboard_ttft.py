import json
import logging

from agent.dashboard import ttft


def _event(
    method: str,
    data: dict[str, object],
    *,
    namespace: list[str],
    event_id: str,
) -> bytes:
    payload = {
        "type": "event",
        "event_id": event_id,
        "method": method,
        "params": {"namespace": namespace, "timestamp": 2_250, "data": data},
    }
    return f"event: {method}\r\ndata: {json.dumps(payload)}\r\n\r\n".encode()


def _lifecycle(run_id: str) -> bytes:
    return _event(
        "lifecycle",
        {"event": "running"},
        namespace=[],
        event_id=f"synth:{run_id}:lc::running",
    )


def _message(data: dict[str, object], event_id: str = "1-0") -> bytes:
    return _event("messages", data, namespace=["agent"], event_id=event_id)


def test_detector_handles_fragmented_ai_text_events() -> None:
    detector = ttft.AssistantTextEventDetector()
    lifecycle = _lifecycle("run-1")
    start = _message({"event": "message-start", "role": "ai", "id": "message-1"})
    empty = _message(
        {
            "event": "content-block-delta",
            "index": 0,
            "delta": {"type": "text-delta", "text": ""},
        },
        "2-0",
    )
    text = _message(
        {
            "event": "content-block-delta",
            "index": 0,
            "delta": {"type": "text-delta", "text": "Hello"},
        },
        "3-0",
    )

    assert detector.feed(lifecycle + start[:12]) == []
    assert detector.feed(start[12:] + empty + text[:20]) == []
    assert detector.feed(text[20:]) == [
        ttft.AssistantTextObservation(run_id="run-1", event_timestamp_ms=2_250)
    ]
    assert detector.feed(text) == []


def test_detector_ignores_non_ai_and_correlates_later_runs() -> None:
    detector = ttft.AssistantTextEventDetector()
    text_delta = {
        "event": "content-block-delta",
        "index": 0,
        "delta": {"type": "text-delta", "text": "Hello"},
    }

    assert detector.feed(_lifecycle("run-1")) == []
    assert detector.feed(_message({"event": "message-start", "role": "human"})) == []
    assert detector.feed(_message(text_delta)) == []
    assert detector.feed(_message({"event": "message-start", "role": "ai"})) == []
    assert detector.feed(_message(text_delta)) == [
        ttft.AssistantTextObservation(run_id="run-1", event_timestamp_ms=2_250)
    ]
    assert detector.feed(_message({"event": "message-finish"})) == []
    assert detector.feed(_lifecycle("run-2")) == []
    assert detector.feed(_message({"event": "message-start", "role": "ai"})) == []
    assert detector.feed(_message(text_delta, "2-0")) == [
        ttft.AssistantTextObservation(run_id="run-2", event_timestamp_ms=2_250)
    ]


async def test_record_dashboard_thread_ttft_emits_histogram_and_log(
    monkeypatch,
    caplog,
) -> None:
    observation = ttft.AssistantTextObservation(run_id="run-1", event_timestamp_ms=2_250)
    histogram_values: list[float] = []
    monkeypatch.setattr(ttft, "_record_ttft_histogram", histogram_values.append)

    with caplog.at_level(logging.INFO, logger=ttft.__name__):
        await ttft.record_dashboard_thread_ttft(
            observation,
            thread_id="thread-1",
            started_at_ms=1_000,
        )

    assert histogram_values == [1.25]
    assert "Dashboard thread TTFT 1250.0 ms (thread=thread-1, run=run-1)" in caplog.text
