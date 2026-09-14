import logging

from agent.dashboard import ttft


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


def test_detector_observes_first_ai_text_event() -> None:
    detector = ttft.AssistantTextEventDetector()
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

    assert detector.observe(_lifecycle("run-1")) is None
    assert detector.observe(start) is None
    assert detector.observe(empty) is None
    assert detector.observe(text) == ttft.AssistantTextObservation(
        run_id="run-1", event_timestamp_ms=2_250
    )
    assert detector.observe(text) is None


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
