"""Snapshot-to-live handoff for dashboard threads."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import HTTPException

_MAX_EVENTS = 10_000
_MAX_BYTES = 16 * 1024 * 1024
_STREAM_BODY = {
    "channels": [
        "values",
        "updates",
        "messages",
        "tools",
        "checkpoints",
        "custom",
        "lifecycle",
        "input",
        "tasks",
    ]
}


def _frame(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, default=str, separators=(",", ":")) + "\n").encode()


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    yield payload
                data.clear()
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())


def _root(event: dict[str, Any]) -> bool:
    params = event.get("params")
    return isinstance(params, dict) and params.get("namespace") == []


def _checkpoint(event: dict[str, Any]) -> tuple[str | None, int | None]:
    if event.get("method") != "checkpoints" or not _root(event):
        return None, None
    data = event.get("params", {}).get("data")
    if not isinstance(data, dict):
        return None, None
    checkpoint_id = data.get("id")
    step = data.get("step")
    return (
        checkpoint_id if isinstance(checkpoint_id, str) else None,
        step if isinstance(step, int) and not isinstance(step, bool) else None,
    )


async def snapshot_live_events(
    thread_id: str,
    get_state: Callable[[], Awaitable[dict[str, Any]]],
    *,
    upstream_url: str,
    headers: dict[str, str],
) -> AsyncIterator[bytes]:
    client = httpx.AsyncClient(timeout=httpx.Timeout(None))
    context = client.stream(
        "POST",
        f"{upstream_url.rstrip('/')}/threads/{thread_id}/stream/events",
        json=_STREAM_BODY,
        headers=headers,
    )
    try:
        response = await context.__aenter__()
        if response.status_code >= 400:
            detail = (await response.aread()).decode(errors="replace") or response.reason_phrase
            raise HTTPException(response.status_code, detail)
        state = await get_state()
    except BaseException:
        await context.__aexit__(None, None, None)
        await client.aclose()
        raise

    checkpoint = state.get("checkpoint")
    checkpoint_id = checkpoint.get("checkpoint_id") if isinstance(checkpoint, dict) else None
    metadata = state.get("metadata")
    snapshot_step = metadata.get("step") if isinstance(metadata, dict) else None
    if not isinstance(snapshot_step, int) or isinstance(snapshot_step, bool):
        snapshot_step = None

    async def stream() -> AsyncIterator[bytes]:
        live = checkpoint_id is None and snapshot_step is None
        pending_checkpoint: dict[str, Any] | None = None
        count = 0
        size = 0
        try:
            yield _frame({"type": "snapshot", "state": state})
            async for event in _events(response):
                encoded_size = len(json.dumps(event, default=str).encode())
                count += 1
                size += encoded_size
                if count > _MAX_EVENTS or size > _MAX_BYTES:
                    yield _frame({"type": "reset", "reason": "overflow"})
                    return
                if live:
                    yield _frame({"type": "event", "event": event})
                    continue

                if pending_checkpoint is not None:
                    if event.get("method") != "values" or not _root(event):
                        continue
                    boundary_id, boundary_step = _checkpoint(pending_checkpoint)
                    matches_checkpoint = boundary_id == checkpoint_id
                    comparable_steps = snapshot_step is not None and boundary_step is not None
                    covered = matches_checkpoint or (
                        comparable_steps and boundary_step <= snapshot_step
                    )
                    if not covered:
                        yield _frame({"type": "event", "event": pending_checkpoint})
                        yield _frame({"type": "event", "event": event})
                    pending_checkpoint = None
                    live = matches_checkpoint or (
                        comparable_steps and boundary_step >= snapshot_step
                    )
                    continue

                event_checkpoint_id, event_step = _checkpoint(event)
                if event_checkpoint_id is not None or event_step is not None:
                    pending_checkpoint = event
            yield _frame({"type": "reset", "reason": "source_closed"})
        finally:
            await context.__aexit__(None, None, None)
            await client.aclose()

    return stream()
