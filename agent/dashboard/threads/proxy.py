"""Pass-through endpoints between the dashboard and the LangGraph HTTP API."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2
from fastapi import HTTPException

from agent.config import ENV
from agent.dashboard.threads.access import (
    _authorized_thread_metadata,
    _readable_thread_metadata,
)
from agent.dashboard.threads.runs import (
    _ASSISTANT_ID,
    _enrich_run_start_command,
    _extract_run_id_from_command_response,
    _notify_slack_web_handoff,
)
from agent.dashboard.threads.summary import (
    _assert_thread_postable,
    _assert_thread_readable,
    _now_ms,
    _thread_is_busy,
)
from agent.dashboard.ttft import AssistantTextEventDetector, record_dashboard_thread_ttft
from agent.utils.json_types import thread_metadata
from agent.utils.streaming import TERMINAL_LIFECYCLE_EVENTS, root_lifecycle
from agent.utils.thread_ops import langgraph_client, langgraph_url

logger = logging.getLogger(__name__)

_TTFT_OBSERVER_TASKS: set[asyncio.Task[None]] = set()
_PROXY_REQUEST_TIMEOUT = httpx2.Timeout(30.0, connect=5.0)
_PROXY_STREAM_TIMEOUT = httpx2.Timeout(None)
_DISCOVERY_HISTORY_LIMIT = 5
_THREAD_POST_COMMAND_METHODS = frozenset(
    {"run.start", "input.respond", "input.inject", "state.fork"}
)


def require_json_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def langgraph_proxy_headers(
    *, content_type: str = "application/json", accept: str | None = None
) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if accept:
        headers["Accept"] = accept
    api_key = ENV.LANGSMITH_API_KEY.optional()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


async def proxy_dashboard_thread_stream_events(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> AsyncIterator[bytes]:
    # Preflight here (not in the generator) so auth/content-type failures
    # surface as real HTTP errors before the SSE response starts streaming.
    require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    return stream_thread_events(thread_id, body, content_type)


async def stream_thread_events(
    thread_id: str,
    body: bytes,
    content_type: str,
) -> AsyncIterator[bytes]:
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/stream/events"
    headers = langgraph_proxy_headers(content_type=content_type, accept="text/event-stream")

    try:
        async with httpx2.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
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


async def _observe_dashboard_run_ttft(
    thread_id: str,
    run_id: str,
    started_at_ms: int,
) -> None:
    detector = AssistantTextEventDetector(run_id)
    try:
        async with langgraph_client().threads.stream(
            thread_id, assistant_id=_ASSISTANT_ID
        ) as thread_stream:
            async for event in thread_stream.subscribe(
                ["lifecycle", "messages"], namespaces=[[]], depth=10
            ):
                lifecycle = root_lifecycle(event)
                if (
                    lifecycle is not None
                    and lifecycle[0] == run_id
                    and lifecycle[1] in TERMINAL_LIFECYCLE_EVENTS
                ):
                    return
                observation = detector.observe(event)
                if observation is None:
                    continue
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


async def proxy_dashboard_thread_commands(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    received_at_ms = _now_ms()
    require_json_content_type(content_type)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "command body must be a JSON object")

    # The dashboard mints the thread id client-side and submits straight away,
    # so the very first ``run.start`` may target a thread that doesn't exist
    # yet. That command lazily creates + stamps + owns the thread (in
    # ``_enrich_run_start_command``); any other command against a missing thread
    # is a 404. On an existing thread, ``run.start`` (the posting path) is open
    # to any org member and attributed in ``_enrich_run_start_command``. Input
    # commands on admin threads require an admin; other threads keep unattributed
    # commands such as ``input.respond`` owner-only.
    method = parsed.get("method")
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception:  # noqa: BLE001
        thread = None

    creating = False
    if thread is None:
        if method != "run.start":
            raise HTTPException(404, "thread not found")
        creating = True
        metadata: dict[str, Any] = {}
        thread_busy = False
    else:
        metadata = thread_metadata(thread)
        post_command = method in _THREAD_POST_COMMAND_METHODS
        if post_command:
            _assert_thread_postable(metadata, login, email)
        else:
            _assert_thread_readable(metadata)
        if method != "run.start" and not (post_command and metadata.get("admin_thread") is True):
            _assert_thread_readable(metadata)
        metadata_run_status = metadata.get("latest_run_status")
        thread_busy = _thread_is_busy(thread) or metadata_run_status in {"pending", "running"}

    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/commands"
    headers = langgraph_proxy_headers(content_type=content_type)

    enriched = await _enrich_run_start_command(
        thread_id,
        login,
        parsed,
        metadata=metadata,
        thread_busy=thread_busy,
        creating=creating,
        email=email,
    )
    outgoing = json.dumps(enriched).encode()

    if method == "run.start":
        params = enriched.get("params")
        if isinstance(params, dict):
            run_metadata = params.get("metadata")
            if not isinstance(run_metadata, dict):
                run_metadata = {}
                params["metadata"] = run_metadata
            run_metadata["dashboard_ttft_started_at_ms"] = received_at_ms
            outgoing = json.dumps(enriched).encode()

    async with httpx2.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, content=outgoing, headers=headers)

    try:
        response_payload = json.loads(response.content) if response.content else None
    except json.JSONDecodeError:
        response_payload = None
    run_id = _extract_run_id_from_command_response(response_payload)
    run_start_succeeded = (
        parsed.get("method") == "run.start"
        and response.status_code in {200, 202, 204}
        and isinstance(response_payload, dict)
        and response_payload.get("type") == "success"
        and run_id is not None
    )
    if run_start_succeeded and not creating:
        try:
            await _notify_slack_web_handoff(thread_id, metadata, langgraph_client())
        except Exception:
            logger.exception(
                "Failed to update Slack message for dashboard handoff on %s", thread_id
            )

    if run_start_succeeded and run_id is not None:
        task = asyncio.create_task(
            _observe_dashboard_run_ttft(
                thread_id,
                run_id,
                received_at_ms,
            )
        )
        _TTFT_OBSERVER_TASKS.add(task)
        task.add_done_callback(_TTFT_OBSERVER_TASKS.discard)
        try:
            await langgraph_client().threads.update(
                thread_id=thread_id,
                metadata={
                    "latest_run_id": run_id,
                    "latest_run_status": "pending",
                    "updated_at_ms": _now_ms(),
                },
            )
        except Exception:
            logger.warning(
                "Failed to persist started dashboard run %s on thread %s",
                run_id,
                thread_id,
                exc_info=True,
            )
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def proxy_dashboard_thread_history(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "history body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "history body must be a JSON object")
    limit = payload.get("limit", _DISCOVERY_HISTORY_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise HTTPException(400, "history limit must be a positive integer")
    if not any(payload.get(key) for key in ("before", "checkpoint", "metadata")):
        payload["limit"] = min(limit, _DISCOVERY_HISTORY_LIMIT)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/history"
    headers = langgraph_proxy_headers(content_type=content_type)
    async with httpx2.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=payload, headers=headers)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def proxy_dashboard_thread_run_cancel(
    thread_id: str,
    run_id: str,
    login: str,
    *,
    wait: str = "0",
    action: str = "interrupt",
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    await _authorized_thread_metadata(thread_id, login, email=email)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs/{run_id}/cancel"
    headers = langgraph_proxy_headers()
    async with httpx2.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(
            url,
            headers=headers,
            params={"wait": wait, "action": action},
        )
    if response.status_code in {200, 202, 204}:
        try:
            await langgraph_client().threads.update(
                thread_id=thread_id,
                metadata={
                    "latest_run_status": "interrupted",
                    "updated_at_ms": _now_ms(),
                },
            )
        except Exception:
            logger.debug(
                "Could not update thread metadata after run cancel for %s",
                thread_id,
                exc_info=True,
            )
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type
