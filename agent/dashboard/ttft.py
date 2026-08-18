"""Dashboard thread time-to-first-token measurement."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

STARTED_AT_MS_KEY = "dashboard_ttft_started_at_ms"
RECORDED_RUN_ID_KEY = "dashboard_ttft_recorded_run_id"
_DASHBOARD_THREAD_TTFT: Any | None = None


class AssistantTextEventDetector:
    """Detect each run's first non-empty streamed AI text delta."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._run_id: str | None = None
        self._ai_namespaces: set[tuple[str, ...]] = set()
        self._observed_namespaces: set[tuple[str, ...]] = set()

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer.extend(chunk)
        observations: list[str] = []
        while True:
            frame = self._pop_frame()
            if frame is None:
                return observations
            payload = self._payload(frame)
            if payload is not None and (run_id := self._observe(payload)) is not None:
                observations.append(run_id)

    def _pop_frame(self) -> bytes | None:
        separators = ((self._buffer.find(b"\r\n\r\n"), 4), (self._buffer.find(b"\n\n"), 2))
        matches = [(index, length) for index, length in separators if index >= 0]
        if not matches:
            return None
        index, length = min(matches)
        frame = bytes(self._buffer[:index])
        del self._buffer[: index + length]
        return frame

    @staticmethod
    def _payload(frame: bytes) -> dict[str, Any] | None:
        data_lines = []
        for line in frame.splitlines():
            if line.startswith(b"data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return None
        try:
            payload = json.loads(b"\n".join(data_lines))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _observe(self, payload: dict[str, Any]) -> str | None:
        params = payload.get("params")
        if not isinstance(params, dict):
            return None
        namespace_value = params.get("namespace")
        if not isinstance(namespace_value, list) or not all(
            isinstance(part, str) for part in namespace_value
        ):
            return None
        namespace = tuple(namespace_value)
        data = params.get("data")
        if not isinstance(data, dict):
            return None
        if payload.get("method") == "lifecycle" and not namespace:
            self._observe_lifecycle(payload, data)
            return None
        if payload.get("method") != "messages":
            return None
        event = data.get("event")
        if event == "message-start":
            if data.get("role") == "ai":
                self._ai_namespaces.add(namespace)
                self._observed_namespaces.discard(namespace)
            return None
        if event == "message-finish":
            self._ai_namespaces.discard(namespace)
            self._observed_namespaces.discard(namespace)
            return None
        if event != "content-block-delta" or namespace not in self._ai_namespaces:
            return None
        delta = data.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text-delta":
            return None
        text = delta.get("text")
        if (
            not isinstance(text, str)
            or not text
            or namespace in self._observed_namespaces
            or self._run_id is None
        ):
            return None
        self._observed_namespaces.add(namespace)
        return self._run_id

    def _observe_lifecycle(self, payload: dict[str, Any], data: dict[str, Any]) -> None:
        event_id = payload.get("event_id")
        if data.get("event") != "running" or not isinstance(event_id, str):
            return
        parts = event_id.split(":", 2)
        if len(parts) == 3 and parts[0] == "synth" and parts[1]:
            self._run_id = parts[1]
            self._ai_namespaces.clear()
            self._observed_namespaces.clear()


def _record_ttft_histogram(duration_seconds: float) -> None:
    from langgraph_api.metrics_datadog import (
        METRIC_TIER_INFO,
        def_latency,
        get_datadog_metrics_reporter,
    )

    global _DASHBOARD_THREAD_TTFT
    if _DASHBOARD_THREAD_TTFT is None:
        _DASHBOARD_THREAD_TTFT = def_latency("open_swe_dashboard_thread_ttft", METRIC_TIER_INFO)
    get_datadog_metrics_reporter().record_latency(
        _DASHBOARD_THREAD_TTFT,
        duration_seconds,
        attributes={"source": "dashboard"},
    )


def record_dashboard_thread_ttft(
    duration_seconds: float,
    *,
    thread_id: str,
    run_id: str | None,
) -> None:
    try:
        _record_ttft_histogram(duration_seconds)
    except Exception:
        logger.warning("Failed to record dashboard thread TTFT histogram", exc_info=True)
    logger.info(
        "Dashboard thread TTFT %.1f ms (thread=%s, run=%s)",
        duration_seconds * 1000,
        thread_id,
        run_id or "unknown",
    )
