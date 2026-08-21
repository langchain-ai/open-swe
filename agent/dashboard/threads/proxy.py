"""The transport under the LangGraph SDK protocol the browser speaks.

The frontend runs the LangGraph SDK against our own origin, so the dashboard has
to forward the SDK's ``/threads/{id}/{commands,history,state,stream/events}``
calls to the platform. That hop is raw ``httpx`` rather than the typed SDK
client: the SDK models one call per endpoint, while this is a byte-for-byte
relay of a protocol we do not want to re-implement — we only authorize it and,
for ``run.start``, rewrite the command on the way past.

Nothing here knows what a dashboard thread is. Authorization and enrichment are
the caller's, expressed as the ``enrich`` callback of :func:`proxy_commands`.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import HTTPException

from ...config import langgraph_url, langsmith_credentials
from ..ttft import AssistantTextEventDetector, record_dashboard_thread_ttft

logger = logging.getLogger(__name__)

# Modes required for the v2 event-stream protocol (`POST …/stream/events`).
PROXY_STREAM_MODES: tuple[str, ...] = (
    "values",
    "updates",
    "messages",
    "messages-tuple",
    "tools",
    "checkpoints",
    "events",
)
PROXY_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_PROXY_STREAM_TIMEOUT = httpx.Timeout(None)
_TTFT_OBSERVER_TASKS: set[asyncio.Task[None]] = set()

CommandEnricher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def require_json_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def proxy_headers(
    *, content_type: str = "application/json", accept: str | None = None
) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if accept:
        headers["Accept"] = accept
    credentials = langsmith_credentials("platform")
    if credentials:
        headers["X-API-Key"] = credentials[0]
    return headers


def thread_url(thread_id: str, suffix: str) -> str:
    return f"{langgraph_url().rstrip('/')}/threads/{thread_id}/{suffix}"


async def passthrough(
    method: str,
    thread_id: str,
    suffix: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    params: dict[str, Any] | None = None,
) -> tuple[int, bytes, str | None]:
    """Relay one request to the platform and hand back its raw response."""
    url = thread_url(thread_id, suffix)
    headers = proxy_headers(content_type=content_type)
    async with httpx.AsyncClient(timeout=PROXY_REQUEST_TIMEOUT) as client:
        if method == "GET":
            response = await client.get(url, headers=headers, params=params)
        else:
            response = await client.post(url, content=body, headers=headers, params=params)
    return response.status_code, response.content, response.headers.get("content-type")


def parse_command(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "command body must be a JSON object")
    return parsed


async def proxy_commands(
    thread_id: str,
    body: bytes,
    *,
    enrich: CommandEnricher,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    """Forward one SDK command, letting ``enrich`` rewrite (and authorize) it first."""
    require_json_content_type(content_type)
    enriched = await enrich(parse_command(body))
    return await passthrough(
        "POST",
        thread_id,
        "commands",
        body=json.dumps(enriched).encode(),
        content_type=content_type,
    )


async def stream_thread_events(
    thread_id: str, body: bytes, content_type: str
) -> AsyncIterator[bytes]:
    url = thread_url(thread_id, "stream/events")
    headers = proxy_headers(content_type=content_type, accept="text/event-stream")

    try:
        async with httpx.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
            async with client.stream("POST", url, content=body, headers=headers) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    payload = {
                        "status": response.status_code,
                        "detail": error_body.decode(errors="replace") or response.reason_phrase,
                    }
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n".encode()
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
    except Exception:
        logger.warning("LangGraph stream/events proxy closed for %s", thread_id, exc_info=True)


async def observe_run_ttft(thread_id: str, run_id: str, started_at_ms: int) -> None:
    """Watch a run's message stream just long enough to time its first token."""
    url = thread_url(thread_id, f"runs/{run_id}/stream")
    headers = proxy_headers(accept="text/event-stream")
    headers["Last-Event-ID"] = "-1"
    detector = AssistantTextEventDetector(run_id)
    try:
        async with httpx.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
            async with client.stream(
                "GET",
                url,
                headers=headers,
                params={"stream_mode": "messages"},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    for observation in detector.feed(chunk):
                        await record_dashboard_thread_ttft(
                            observation,
                            thread_id=thread_id,
                            started_at_ms=started_at_ms,
                        )
                        return
    except Exception:
        logger.warning(
            "Dashboard TTFT observer closed for run %s on thread %s",
            run_id,
            thread_id,
            exc_info=True,
        )


def spawn_ttft_observer(thread_id: str, run_id: str, started_at_ms: int) -> None:
    task = asyncio.create_task(observe_run_ttft(thread_id, run_id, started_at_ms))
    _TTFT_OBSERVER_TASKS.add(task)
    task.add_done_callback(_TTFT_OBSERVER_TASKS.discard)
