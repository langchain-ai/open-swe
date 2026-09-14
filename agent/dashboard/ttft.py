"""Dashboard thread time-to-first-token measurement."""

import logging
from dataclasses import dataclass
from typing import Any

from agent.utils.streaming import root_lifecycle

logger = logging.getLogger(__name__)

_DASHBOARD_THREAD_TTFT: Any | None = None


@dataclass(frozen=True, slots=True)
class AssistantTextObservation:
    run_id: str
    event_timestamp_ms: int


class AssistantTextEventDetector:
    """Detect each run's first non-empty streamed AI text delta."""

    def __init__(self, run_id: str | None = None) -> None:
        self._target_run_id = run_id
        self._run_id: str | None = None
        self._ai_namespaces: set[tuple[str, ...]] = set()
        self._observed_namespaces: set[tuple[str, ...]] = set()

    def observe(self, event: dict[str, Any]) -> AssistantTextObservation | None:
        lifecycle = root_lifecycle(event)
        if lifecycle is not None:
            if lifecycle[1] == "running" and self._target_run_id in (None, lifecycle[0]):
                self._run_id = lifecycle[0]
                self._ai_namespaces.clear()
                self._observed_namespaces.clear()
            return None
        params = event.get("params")
        if not isinstance(params, dict):
            return None
        namespace_value = params.get("namespace")
        if not isinstance(namespace_value, list) or not all(
            isinstance(part, str) for part in namespace_value
        ):
            return None
        namespace = tuple(namespace_value)
        data = params.get("data")
        if not isinstance(data, dict) or event.get("method") != "messages":
            return None
        message_event = data.get("event")
        if message_event == "message-start":
            if data.get("role") == "ai":
                self._ai_namespaces.add(namespace)
                self._observed_namespaces.discard(namespace)
            return None
        if message_event == "message-finish":
            self._ai_namespaces.discard(namespace)
            self._observed_namespaces.discard(namespace)
            return None
        if message_event != "content-block-delta" or namespace not in self._ai_namespaces:
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
            or self._target_run_id not in (None, self._run_id)
        ):
            return None
        timestamp = params.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            return None
        self._observed_namespaces.add(namespace)
        return AssistantTextObservation(
            run_id=self._run_id,
            event_timestamp_ms=int(timestamp),
        )


def _record_ttft_histogram(duration_seconds: float) -> None:
    from langgraph_api.metrics_datadog import (  # pyright: ignore[reportMissingImports]
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


async def record_dashboard_thread_ttft(
    observation: AssistantTextObservation,
    *,
    thread_id: str,
    started_at_ms: int,
) -> None:
    try:
        if observation.event_timestamp_ms < started_at_ms:
            return
        duration_seconds = (observation.event_timestamp_ms - started_at_ms) / 1000
        _record_ttft_histogram(duration_seconds)
    except Exception:
        logger.warning("Failed to record dashboard thread TTFT histogram", exc_info=True)
        return
    logger.info(
        "Dashboard thread TTFT %.1f ms (thread=%s, run=%s)",
        duration_seconds * 1000,
        thread_id,
        observation.run_id,
    )
