import logging
from collections.abc import Mapping

from sqlalchemy import text

from agent.database import postgres
from agent.dispatch import create_durable_run
from agent.prompts import prompt
from agent.tasks import store
from agent.tasks.service import (
    dispatch_worker,
    dispatched_run,
    ensure_worker_thread,
    is_duplicate_task_dispatch,
    register_dispatch_run,
    run_config,
    run_invocation,
    system_input,
)
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)
_TERMINAL = frozenset({"success", "error", "timeout", "interrupted"})


async def deliver_events(coordinator_thread_id: str) -> int:
    delivered = 0
    async with (
        store.thread_lock(coordinator_thread_id),
        store.event_delivery_lock(coordinator_thread_id),
    ):
        client = langgraph_client()
        for event in await store.pending_events(coordinator_thread_id):
            key = f"task-event:{event.id}"
            if await dispatched_run(client, coordinator_thread_id, key) is None:
                metadata = thread_metadata(await client.threads.get(coordinator_thread_id))
                content = prompt(
                    "tasks/worker-event",
                    worker_thread_id=event.worker_thread_id,
                    kind=event.kind,
                    content=event.content,
                )
                await create_durable_run(
                    coordinator_thread_id,
                    "agent",
                    input=system_input(content, key),
                    config={"configurable": run_config(coordinator_thread_id, metadata)},
                    source="task_event",
                    thread_title=None,
                    metadata={"task_dispatch_key": key, "task_event_id": event.id},
                    client=client,
                    multitask_strategy="enqueue",
                )
            await store.mark_event_delivered(event.id)
            delivered += 1
    return delivered


def _message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            block["text"]
            for block in value
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


async def worker_result(thread_id: str, run_id: str, status: str, error: object) -> str:
    if status != "success":
        return f"Invocation {run_id} ended with status {status}. Error: {error!s}"[:16000]
    client = langgraph_client()
    run = await client.runs.get(thread_id, run_id)
    dispatch_key = (run.get("metadata") or {}).get("task_dispatch_key")
    invocation_id = run_invocation(run)
    history = await client.threads.get_history(
        thread_id,
        limit=1,
        metadata={"invocation_id": invocation_id}
        if invocation_id is not None
        else {"task_dispatch_key": dispatch_key}
        if isinstance(dispatch_key, str)
        else {"run_id": run_id},
    )
    if history:
        values = history[0].get("values")
        messages = values.get("messages") if isinstance(values, Mapping) else None
        if isinstance(messages, list):
            for message in reversed(messages):
                if (
                    isinstance(message, Mapping)
                    and message.get("type", message.get("role")) in {"ai", "assistant"}
                    and not message.get("tool_calls")
                ):
                    result = _message_text(message.get("content"))
                    if result:
                        return result[:32000]
    return f"Invocation {run_id} completed. Inspect this worker's thread for its full result."


async def handle_worker_completion(
    thread_id: str, run_id: str | None, status: object, *, error: object = None
) -> bool:
    delegation = await store.delegation_for_worker(thread_id)
    if delegation is None:
        return False
    if not isinstance(status, str) or status not in _TERMINAL or not run_id:
        return True
    terminal: store.TerminalDelegationStatus = (
        "completed" if status == "success" else "cancelled" if status == "interrupted" else "failed"
    )
    async with store.thread_lock(delegation.coordinator_thread_id):
        client = langgraph_client()
        run = await client.runs.get(thread_id, run_id)
        if await is_duplicate_task_dispatch(thread_id, run):
            return True
        dispatch_key = (run.get("metadata") or {}).get("task_dispatch_key")
        registered = True
        if isinstance(dispatch_key, str):
            try:
                await register_dispatch_run(client, thread_id, dispatch_key, run_id)
            except ValueError:
                if await is_duplicate_task_dispatch(thread_id, run):
                    return True
                if status == "success" or await store.task_dispatch_invocation(
                    thread_id, dispatch_key
                ):
                    raise
                registered = False
                logger.warning(
                    "Unclaimed worker invocation failed before dispatch registration",
                    exc_info=True,
                    extra={"worker_thread_id": thread_id, "run_id": run_id},
                )
        content = await worker_result(thread_id, run_id, status, error)
        await store.record_event(
            thread_id, event_key=f"completion:{run_id}", kind=terminal, content=content
        )
        if registered:
            current = await store.delegation_for_worker(thread_id)
            if current is not None and current.run_id != run_id:
                offset = 0
                latest_run_id: str | None = None
                while latest_run_id is None:
                    latest = await client.runs.list(thread_id, limit=100, offset=offset)
                    for candidate in latest:
                        if not await is_duplicate_task_dispatch(thread_id, candidate):
                            latest_run_id = candidate["run_id"]
                            break
                    if len(latest) < 100:
                        break
                    offset += 100
                if latest_run_id == run_id:
                    await store.set_delegation_run(thread_id, run_id)
            await store.finish_delegation(thread_id, status=terminal, run_id=run_id)
            await store.settle_worker_dispatch(thread_id, run_id)
    try:
        await deliver_events(delegation.coordinator_thread_id)
    except Exception:
        logger.exception(
            "Worker event delivery deferred to reconciliation",
            extra={
                "worker_thread_id": thread_id,
                "coordinator_thread_id": delegation.coordinator_thread_id,
            },
        )
    return True


async def reconcile_tasks() -> dict[str, int]:
    if not postgres.configured():
        return {"task_workers_reconciled": 0, "task_events_delivered": 0}
    async with postgres.connection() as conn:
        rows = await conn.execute(
            text(
                "SELECT worker_thread_id FROM task_delegation WHERE status = 'pending' AND run_id IS NULL"
            )
        )
        worker_ids = [str(row[0]) for row in rows]
    reconciled = 0
    for worker_id in worker_ids:
        try:
            delegation = await store.delegation_for_worker(worker_id)
            if delegation is None:
                continue
            async with store.thread_lock(delegation.coordinator_thread_id):
                delegation = await store.delegation_for_worker(worker_id)
                if delegation is None or delegation.status != "pending" or delegation.run_id:
                    continue
                parent = thread_metadata(
                    await langgraph_client().threads.get(delegation.coordinator_thread_id)
                )
                await ensure_worker_thread(delegation, parent)
                await dispatch_worker(delegation)
                reconciled += 1
        except Exception:
            logger.exception(
                "Task worker reconciliation failed", extra={"worker_thread_id": worker_id}
            )
    for intent in await store.pending_worker_dispatches():
        try:
            delegation = await store.delegation_for_worker(intent.worker_thread_id)
            if delegation is None:
                continue
            async with store.thread_lock(delegation.coordinator_thread_id):
                client = langgraph_client()
                run_id = await dispatched_run(client, intent.worker_thread_id, intent.dispatch_key)
                if run_id is not None:
                    await register_dispatch_run(
                        client, intent.worker_thread_id, intent.dispatch_key, run_id
                    )
                else:
                    run_id = intent.run_id or await dispatch_worker(
                        delegation, message=intent.content, dispatch_key=intent.dispatch_key
                    )
                run = await client.runs.get(intent.worker_thread_id, run_id)
                if run.get("status") in _TERMINAL:
                    await handle_worker_completion(
                        intent.worker_thread_id, run_id, run.get("status"), error=run.get("error")
                    )
                    reconciled += 1
        except Exception:
            logger.exception(
                "Task dispatch reconciliation failed",
                extra={
                    "worker_thread_id": intent.worker_thread_id,
                    "dispatch_key": intent.dispatch_key,
                },
            )
    delivered = 0
    for coordinator_id in await store.coordinators_with_pending_events():
        try:
            delivered += await deliver_events(coordinator_id)
        except Exception:
            logger.exception(
                "Task event reconciliation failed", extra={"coordinator_thread_id": coordinator_id}
            )
    return {"task_workers_reconciled": reconciled, "task_events_delivered": delivered}
