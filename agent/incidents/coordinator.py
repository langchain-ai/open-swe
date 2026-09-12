"""Serialize workspace configuration and durable worker admission."""

import time
from datetime import datetime
from typing import Any

import httpx

from agent.dispatch import create_durable_run
from agent.incidents import documents, service
from agent.incidents.models import CoordinatorState, Incident, IncidentPolicy
from agent.incidents.worker import channel_receipts, debounce_delay
from agent.store import store_client


async def cleanup(records: list[Incident]) -> list[Incident]:
    """Prune content only when no channel worker owns a mutable record."""
    now = time.time()
    for index, record in enumerate(records):
        if (
            not record.expired
            and datetime.fromisoformat(record.created_at).timestamp() <= now - 30 * 86400
        ):
            await documents.preserve_metadata(record)
            from agent.incidents.runtime import PASSES, TOOL_OUTCOMES

            passes = await PASSES.search_all(filter={"incident_id": record.id})
            conversations = {saved.thread_id for saved in passes}
            if record.agent_thread_id:
                conversations.add(record.agent_thread_id)
            for thread_id in conversations:
                try:
                    await store_client().threads.delete(thread_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
            for saved_pass in passes:
                await PASSES.delete(saved_pass.id)
            for outcome in await TOOL_OUTCOMES.search_all(filter={"incident_id": record.id}):
                await TOOL_OUTCOMES.delete(outcome.id)
            record = Incident(
                id=record.id,
                workspace_id=record.workspace_id,
                channel_id=record.channel_id,
                channel_name=record.channel_name,
                title=record.title,
                thread_id=record.thread_id,
                created_at=record.created_at,
                status="completed",
                reason="code_channel" if record.reason == "code_channel" else "retention_expired",
                expired=True,
            )
            await service.INVESTIGATIONS.put(record.id, record)
            records[index] = record
        if record.expired and record.thread_id:
            try:
                await store_client().threads.delete(record.thread_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
            record.thread_id = ""
            await service.INVESTIGATIONS.put(record.id, record)
    by_channel = {(r.workspace_id, r.channel_id): r for r in records}
    for receipt in await service.RECEIPTS.search_all():
        record = by_channel.get((receipt.workspace_id, receipt.channel_id))
        if (
            record and record.expired and receipt.kind not in {"document_edit", "provider_command"}
        ) or (
            receipt.received_at < now - 7 * 86400
            and (not record or receipt.id in record.processed_receipts)
        ):
            await service.RECEIPTS.delete(receipt.id)
    for publication in await service.PUBLICATIONS.search_all():
        record = next((r for r in records if r.id == publication.incident_id), None)
        if not record or record.expired:
            await service.PUBLICATIONS.delete(publication.id)
        elif publication.created_at < now - 7 * 86400 and publication.text:
            # Keep delivery identity until content expiry so an uncertain send cannot be repeated.
            publication.text = ""
            await service.PUBLICATIONS.put(publication.id, publication)
    return records


async def coordinate() -> dict[str, Any]:
    state = await service.COORDINATORS.get("default") or CoordinatorState()
    receipts = sorted(await service.RECEIPTS.search_all(), key=lambda r: (r.received_at, r.id))
    for receipt in receipts:
        if receipt.kind != "settings":
            continue
        current = await service.get_policy()
        candidate = IncidentPolicy.model_validate(receipt.payload["policy"])
        if current.version == receipt.payload["expected_version"]:
            await service.POLICIES.put("default", candidate)
            state.last_operation = {"command_id": receipt.id, "status": "applied", "error": None}
        elif current.model_dump() == candidate.model_dump():
            state.last_operation = {"command_id": receipt.id, "status": "applied", "error": None}
        else:
            state.last_operation = {
                "command_id": receipt.id,
                "status": "failed",
                "error": "Settings version conflict",
            }
        await service.COORDINATORS.put("default", state)
        await service.RECEIPTS.delete(receipt.id)
    policy = await service.get_policy()
    for receipt in receipts:
        if receipt.kind not in {"channel_created", "channel_rename"} or not policy.enabled:
            continue
        id = service.incident_id(receipt.workspace_id, receipt.channel_id)
        if await service.INVESTIGATIONS.get(id):
            continue
        if receipt.workspace_id != policy.workspace_id or receipt.source_time < policy.enabled_at:
            continue
        channel = receipt.payload.get("channel") or {}
        if not isinstance(channel, dict) or not str(channel.get("name", "")).startswith(
            policy.channel_prefix
        ):
            continue
        # Only the coordinator creates a binding; only its channel worker updates it.
        await service.INVESTIGATIONS.put(
            id,
            Incident(
                id=id,
                thread_id=id,
                workspace_id=receipt.workspace_id,
                channel_id=receipt.channel_id,
                watch_started_at=time.time(),
                last_source_activity_at=time.time(),
            ),
        )
    client = store_client()
    if state.active_thread_id:
        try:
            pending = await client.runs.list(state.active_thread_id, status="pending")
            running = await client.runs.list(state.active_thread_id, status="running")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            pending, running = [], []
        if pending or running:
            # A direct mention or command steers the in-flight pass: restart it with the
            # new context, the way a tagged message interrupts a coding run. Plain
            # messages wait for the pass to finish and are picked up right after.
            active = next(
                (
                    r
                    for r in await service.INVESTIGATIONS.search_all()
                    if r.thread_id == state.active_thread_id
                ),
                None,
            )
            if active and any(
                r.kind in {"command", "app_mention"} for r in await channel_receipts(active)
            ):
                await create_durable_run(
                    active.thread_id,
                    "incidents",
                    input={"incident_id": active.id},
                    source="incidents",
                    metadata={"source": "incidents"},
                    multitask_strategy="interrupt",
                )
                state.active_since = time.time()
                await service.COORDINATORS.put("default", state)
                return {"status": "restarted", "incident_id": active.id}
            return {"status": "busy"}
        state.active_thread_id = None
        await service.COORDINATORS.put("default", state)
    records = sorted(await service.INVESTIGATIONS.search_all(), key=lambda r: r.updated_at)
    now = time.time()
    # One worker slot per run: a channel with new messages, requests, publications,
    # or a debounced pass waiting outranks idle re-verification of older channels.
    chosen: Incident | None = None
    idle: Incident | None = None
    for record in await cleanup(records):
        pending_receipts = await channel_receipts(record)
        if record.expired:
            if any(r.kind in {"document_edit", "provider_command"} for r in pending_receipts):
                chosen = record
                break
            continue
        stopped = record.status in {"paused", "completed"}
        pending_work = bool(
            pending_receipts
            or record.pending_requests
            or record.pending_publications
            or (record.pending_since and not stopped)
        )
        if not pending_work and stopped:
            continue
        if (
            not pending_receipts
            and not record.pending_requests
            and not record.pending_publications
            and debounce_delay(record, now) > 0
        ):
            continue
        if record.retry_after > now and not any(
            r.kind in {"command", "app_mention"} for r in pending_receipts
        ):
            continue
        if pending_work:
            chosen = record
            break
        if idle is None and not (
            record.status == "watching" and record.last_verified_at > now - 60
        ):
            idle = record
    record = chosen or idle
    if record is None:
        return {"status": "idle"}
    if not record.thread_id:
        record.thread_id = service.incident_id("worker", record.id)
        await service.INVESTIGATIONS.put(record.id, record)
    await client.threads.create(
        thread_id=record.thread_id,
        if_exists="do_nothing",
        metadata={
            "source": "incidents",
            "incident_id": record.id,
            "owner_type": "system",
            "visibility": "public",
        },
    )
    state.active_thread_id, state.active_since = record.thread_id, time.time()
    await service.COORDINATORS.put("default", state)
    await create_durable_run(
        record.thread_id,
        "incidents",
        input={"incident_id": record.id},
        source="incidents",
        metadata={"source": "incidents"},
        multitask_strategy="enqueue",
    )
    return {"status": "dispatched", "incident_id": record.id}
