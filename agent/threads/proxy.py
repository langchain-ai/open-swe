"""Pass-through endpoints between the dashboard and the LangGraph HTTP API."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2
from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError

from agent.config import ENV
from agent.dashboard.ttft import AssistantTextEventDetector, record_dashboard_thread_ttft
from agent.threads.access import (
    _authorized_thread_metadata,
    _readable_thread_metadata,
)
from agent.threads.principals import Principal
from agent.threads.runs import (
    _ASSISTANT_ID,
    QUEUED_BY_KEY,
    _enrich_run_start_command,
    _enrich_system_run_start_command,
    _extract_run_id_from_command_response,
    _notify_slack_web_handoff,
    offload_requested,
    queue_follow_up_run,
    steer_running_thread,
)
from agent.threads.summary import (
    _assert_thread_postable,
    _now_ms,
    _thread_is_busy,
)
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
    principal: Principal | None = None,
) -> tuple[int, bytes, str | None]:
    """Forward one command, enriched for whoever sent it.

    ``principal`` is how a machine gets in. Without one the sender is the person
    named by ``login``, which is what the dashboard and the agent's own tools pass.
    """
    received_at_ms = _now_ms()
    principal = principal or Principal.of_login(login, email)
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
    # to any allowed user and attributed in ``_enrich_run_start_command``. Input
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
            principal.assert_can_post(metadata)
        else:
            principal.assert_can_read(metadata)
        if method != "run.start" and not (post_command and metadata.get("admin_thread") is True):
            principal.assert_can_read(metadata)
        metadata_run_status = metadata.get("latest_run_status")
        thread_busy = _thread_is_busy(thread) or metadata_run_status in {"pending", "running"}

    start_params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    # The client's queue-or-steer choice rides the run's multitask strategy.
    # LangGraph's commands endpoint does not take it, so it is consumed here.
    enqueue = start_params.pop("multitask_strategy", None) == "enqueue"
    if method == "run.start" and thread_busy:
        if offload_requested(start_params):
            raise HTTPException(409, "offloading requires an idle conversation")
        # Queueing and steering both attribute the message to a person, which a
        # machine has none of; it retries instead.
        if principal.machine:
            raise HTTPException(409, "thread is already running")
        # A follow-up while a run is live either waits for that run as a queued
        # run of its own, or joins it. Either reply keeps the protocol's shape
        # so the client cannot tell them from a plain start.
        handled = await (
            queue_follow_up_run(thread_id, login, parsed, metadata=metadata, email=email)
            if enqueue
            else steer_running_thread(thread_id, login, parsed, metadata=metadata, email=email)
        )
        return 200, json.dumps(handled).encode(), "application/json"

    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/commands"
    headers = langgraph_proxy_headers(content_type=content_type)

    if principal.machine:
        enriched = await _enrich_system_run_start_command(
            thread_id,
            principal,
            parsed,
            metadata=metadata,
            creating=creating,
        )
    else:
        enriched = await _enrich_run_start_command(
            thread_id,
            login,
            parsed,
            metadata=metadata,
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


async def proxy_dashboard_thread_runs_list(
    thread_id: str,
    login: str,
    *,
    limit: int = 10,
    offset: int = 0,
    status: str | None = None,
    select: list[str] | None = None,
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    """Read-only passthrough for the SDK's ``runs.list()``.

    Backs the "stream"-kind `ThreadSource`'s `AgentServerQueueAdapter.hydrate()`/
    ``#refreshPending()``, which call this directly (not through ``commands``)
    to read a thread's pending runs.
    """
    await _readable_thread_metadata(thread_id, login=login, email=email)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs"
    headers = langgraph_proxy_headers()
    params: list[tuple[str, str | int | float | None]] = [
        ("limit", str(limit)),
        ("offset", str(offset)),
    ]
    if status:
        params.append(("status", status))
    for field in select or []:
        params.append(("select", field))
    async with httpx2.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.get(url, headers=headers, params=params)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def _get_thread_tolerating_create_race(client: Any, thread_id: str) -> dict[str, Any] | None:
    """Fetch a thread, tolerating the brief window where a concurrent
    ``run.start`` on this same thread is still lazily creating it.

    The SDK's queue adapter only enqueues once the client believes a run is
    already active on ``thread_id`` — meaning a ``run.start`` dispatch for
    that very thread just went out. On a brand-new thread, that dispatch is
    what creates the thread row; a fast enough follow-up can reach here
    before it lands. Retry briefly rather than 404 what should resolve
    within one HTTP round trip.

    Only retries a genuine ``NotFoundError`` (404). Anything else — an
    outage, a timeout, an auth failure — is a real error, not "not found
    yet", and must propagate instead of being silently retried and then
    reported as a 404.
    """
    for delay in (0.0, 0.15, 0.3, 0.6):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await client.threads.get(thread_id)
        except NotFoundError:
            continue
    return None


async def proxy_dashboard_thread_run_enqueue(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> dict[str, Any]:
    """Create a durable, attributed ``multitask_strategy="enqueue"`` run.

    Backs the "stream"-kind `ThreadSource`'s `AgentServerQueueAdapter.enqueue()`,
    which calls the raw ``client.runs.create()`` REST endpoint directly
    instead of the ``commands`` protocol. Reshapes that call into a
    ``run.start`` command and hands it to ``queue_follow_up_run`` — the same
    function the ``commands`` proxy's queue branch uses — so a queued
    follow-up here gets identical attribution, dedup, and transcript
    bookkeeping. Always enqueues: the raw runs API has no "steer" concept,
    and the client's own ``multitask_strategy`` (if any) is ignored — this
    endpoint must not become a backdoor around ``commands``'s busy-conflict
    check.
    """
    require_json_content_type(content_type)
    try:
        body_dict = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "run body must be a JSON object") from exc
    if not isinstance(body_dict, dict):
        raise HTTPException(400, "run body must be a JSON object")

    client = langgraph_client()
    thread = await _get_thread_tolerating_create_race(client, thread_id)
    if thread is None:
        raise HTTPException(404, "thread not found")
    metadata = thread_metadata(thread)
    _assert_thread_postable(metadata, login, email)

    command = {
        "method": "run.start",
        "params": {
            "input": body_dict.get("input"),
            "config": body_dict.get("config"),
            "metadata": body_dict.get("metadata"),
        },
    }
    queued = await queue_follow_up_run(thread_id, login, command, metadata=metadata, email=email)
    run_id = queued.get("result", {}).get("run_id") if isinstance(queued, dict) else None
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(502, "LangGraph did not return a run id for the queued follow-up")
    # The raw runs REST caller expects a `Run` object back, not the
    # `commands` protocol's `{id, type, result}` envelope `queue_follow_up_run`
    # returns — re-fetch the run it just created to answer in that shape.
    return await client.runs.get(thread_id, run_id)


async def proxy_dashboard_thread_run_cancel(
    thread_id: str,
    run_id: str,
    login: str,
    *,
    wait: str = "0",
    action: str = "interrupt",
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    metadata = await _authorized_thread_metadata(thread_id, login, email=email)
    _assert_thread_postable(metadata, login, email)
    try:
        run = await langgraph_client().runs.get(thread_id, run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "run not found") from exc
    queued_by = (run.get("metadata") or {}).get(QUEUED_BY_KEY)
    if isinstance(queued_by, str) and login not in {queued_by, metadata.get("owner_login")}:
        raise HTTPException(403, "only its sender can withdraw a queued follow-up")
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs/{run_id}/cancel"
    headers = langgraph_proxy_headers()
    async with httpx2.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(
            url,
            headers=headers,
            params={"wait": wait, "action": action},
        )
    if response.status_code in {200, 202, 204}:
        # Cancelling a queued run leaves the live one untouched, so the thread
        # only reads as interrupted when nothing else is still running.
        try:
            still_running = [
                run
                for run in await langgraph_client().runs.list(thread_id, status="running", limit=5)
                if run.get("run_id") != run_id
            ]
            if not still_running:
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
