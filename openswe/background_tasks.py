"""Model-free completion delivery for sandbox background commands.

Two triggers, chosen per launch by the triggering person's
``experimental_background_callbacks`` flag: the runner calling back through the
sandbox tools channel when its command exits, or a per-thread cron polling every
minute. Either one reconciles the commands the owning thread launched, and claims
keep a completion from being delivered twice.
"""

import logging
import shlex
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError

from openswe.dispatch import dispatch_agent_run, follow_up_configurable
from openswe.input_messages import InputMessageContext, SystemIdentity
from openswe.prompts import prompt
from openswe.sandboxes.connect import connect_sandbox
from openswe.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY, ToolAccess
from openswe.slack.thinking import sync_slack_background_status
from openswe.source_context import SourceContext
from openswe.tools.background_execute import TASK_ROOT, control_script, encoded, execute
from openswe.utils.background_task_state import (
    RUNNING_BACKGROUND_TASKS_KEY,
    update_background_task_state,
)
from openswe.utils.thread_ops import langgraph_url

logger = logging.getLogger(__name__)

# Threads whose triggering person has not opted into callbacks poll with a per-thread cron.
CRON_KIND = "background_tasks"
CRON_SCHEDULE = "* * * * *"
# The server drops a `thread_id` key from cron metadata, so crons are tagged with this instead.
CRON_THREAD_KEY = "agent_thread_id"
_CRON_PAGE_SIZE = 1000
TERMINAL_STATES = {"completed", "failed", "timed_out", "stopped", "lost"}
MONITOR_LOCK = f"{TASK_ROOT}/monitor.lock"
_BACKGROUND_TASK_SENDER: SystemIdentity = {
    "id": "system:background-task",
    "display_name": "Background task",
    "platform": "open-swe",
}
_BACKGROUND_TASK_CONTEXT: InputMessageContext = {
    "sender_id": _BACKGROUND_TASK_SENDER["id"],
    "surface": "automation",
    "kind": "system",
}


def _client():
    return get_client(url=langgraph_url())


async def ensure_background_task_cron(thread_id: str) -> str:
    client = _client()
    crons = await client.crons.search(
        metadata={"kind": CRON_KIND, CRON_THREAD_KEY: thread_id},
        limit=10,
    )
    ids = [
        cron_id
        for cron in crons or []
        if isinstance(cron, dict) and isinstance((cron_id := cron.get("cron_id")), str)
    ]
    if ids:
        for duplicate in ids[1:]:
            await client.crons.delete(duplicate)
        return ids[0]
    cron = await client.crons.create(
        "scheduler",
        schedule=CRON_SCHEDULE,
        input={"task": CRON_KIND, "thread_id": thread_id},
        config={"configurable": {"task": CRON_KIND, "thread_id": thread_id}},
        metadata={"kind": CRON_KIND, CRON_THREAD_KEY: thread_id},
        timezone="UTC",
    )
    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if not isinstance(cron_id, str) or not cron_id:
        raise RuntimeError("background-task cron creation did not return a cron_id")
    return cron_id


async def _delete_crons(thread_id: str) -> None:
    client = _client()
    tagged = await client.crons.search(
        metadata={"kind": CRON_KIND, CRON_THREAD_KEY: thread_id}, limit=10
    )
    cron_ids = [cron["cron_id"] for cron in tagged]
    # Crons created before CRON_THREAD_KEY lack it, so match those on the payload instead.
    # Collect every page before deleting so deletes do not shift later offsets.
    offset = 0
    while True:
        page = await client.crons.search(
            metadata={"kind": CRON_KIND}, limit=_CRON_PAGE_SIZE, offset=offset
        )
        for cron in page:
            payload_input = cron.get("payload", {}).get("input")
            if (
                isinstance(payload_input, dict)
                and payload_input.get("thread_id") == thread_id
                and cron["cron_id"] not in cron_ids
            ):
                cron_ids.append(cron["cron_id"])
        if len(page) < _CRON_PAGE_SIZE:
            break
        offset += _CRON_PAGE_SIZE
    for cron_id in cron_ids:
        await client.crons.delete(cron_id)


def _notification(task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "unknown")
    status = str(task.get("status") or "unknown")
    exit_code = task.get("exit_code")
    duration = task.get("duration_seconds")
    output_path = str(task.get("output_path") or "")
    return prompt(
        "runs/background-task-completion",
        task_id=task_id,
        status=status,
        exit_code=exit_code,
        durations=f"{duration}s",
        output_path=output_path,
    )


async def _claim(backend: Any, task_id: str) -> bool:
    claim = f"{TASK_ROOT}/{task_id}/notify.claim"
    response = await backend.aexecute(f"mkdir {shlex.quote(claim)} 2>/dev/null", timeout=10)
    return getattr(response, "exit_code", None) == 0


async def _unclaim(backend: Any, task_id: str) -> None:
    await backend.aexecute(
        f"rmdir {shlex.quote(f'{TASK_ROOT}/{task_id}/notify.claim')} 2>/dev/null || true",
        timeout=10,
    )


async def _mark_delivered(backend: Any, task_id: str) -> None:
    task_dir = f"{TASK_ROOT}/{task_id}"
    response = await backend.aexecute(
        f"mv {shlex.quote(task_dir + '/notify.claim')} {shlex.quote(task_dir + '/notify.done')}",
        timeout=10,
    )
    if getattr(response, "exit_code", None) != 0:
        raise RuntimeError("failed to persist background-task notification")


async def _list_tasks(backend: Any, *, required: bool = False) -> list[dict[str, Any]]:
    script = control_script("list", None)
    result = await execute(
        backend, f"printf %s {shlex.quote(encoded(script))} | base64 -d | python3"
    )
    tasks = result.get("tasks") if isinstance(result, dict) else None
    if isinstance(tasks, list):
        return tasks
    if required:
        raise RuntimeError("Background-task list could not be read")
    return []


class _Reconciled(NamedTuple):
    result: dict[str, Any]
    backend: Any | None
    tracked: bool
    owns_legacy_tasks: bool = True


def _belongs_to_thread(
    task: Mapping[str, object], thread_id: str, *, owns_legacy_tasks: bool
) -> bool:
    owner = task.get("owner_thread_id")
    # Commands created before ownership tracking belong to the sandbox's host.
    return owner == thread_id or (owner is None and owns_legacy_tasks)


async def reconcile_background_tasks(thread_id: str) -> dict[str, Any]:
    return (await _reconcile(thread_id)).result


async def reconcile_background_task_callback(task_id: str, access: ToolAccess) -> dict[str, object]:
    backend = await connect_sandbox(access.sandbox_id, thread_id=access.thread_id)
    # An unreadable list must fail with a 5xx so the runner retries instead of giving up.
    for task in await _list_tasks(backend, required=True):
        if task.get("task_id") == task_id:
            owner = task.get("owner_thread_id", access.thread_id)
            if not isinstance(owner, str) or not owner:
                raise PermissionError("Background task has invalid ownership")
            return (await _reconcile(owner, access=access)).result
    raise LookupError("No such background task")


async def _reconcile(thread_id: str, *, access: ToolAccess | None = None) -> _Reconciled:
    client = _client()
    try:
        thread = await client.threads.get(thread_id)
    except NotFoundError:
        logger.info("Background-task thread is gone", extra={"agent_thread_id": thread_id})
        return _Reconciled({"status": "missing_thread"}, None, tracked=True)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    owns_legacy_tasks = not metadata.get(SANDBOX_HOST_THREAD_KEY)
    if access is not None and (
        metadata.get("sandbox_id") != access.sandbox_id
        or (
            thread_id != access.thread_id
            and metadata.get(SANDBOX_HOST_THREAD_KEY) != access.thread_id
        )
    ):
        raise PermissionError("Background task owner does not belong to this sandbox")
    tracked = metadata.get(RUNNING_BACKGROUND_TASKS_KEY)
    tracked_ids = (
        [task_id for task_id in tracked if isinstance(task_id, str)]
        if isinstance(tracked, list)
        else []
    )
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        if tracked_ids:
            metadata = await update_background_task_state(client, thread_id, reset=True)
        await sync_slack_background_status(client, thread_id, metadata=metadata)
        return _Reconciled({"status": "missing_sandbox"}, None, tracked=True)
    backend = await connect_sandbox(sandbox_id, thread_id=thread_id)
    tasks = [
        task
        for task in await _list_tasks(backend)
        if _belongs_to_thread(task, thread_id, owns_legacy_tasks=owns_legacy_tasks)
    ]
    running = [task for task in tasks if task.get("status") == "running"]
    terminal = [task for task in tasks if task.get("status") in TERMINAL_STATES]
    running_ids = [task_id for task in running if isinstance((task_id := task.get("task_id")), str)]
    finished_ids = [
        task_id for task in terminal if isinstance((task_id := task.get("task_id")), str)
    ]
    tracked_successfully = False
    status_metadata: dict[str, object] | None = None
    # Only take the thread lock when the tracked set changes.
    if set(running_ids) - set(finished_ids) == set(tracked_ids):
        status_metadata = metadata
        tracked_successfully = True
    else:
        try:
            status_metadata = await update_background_task_state(
                client,
                thread_id,
                running=running_ids,
                finished=[
                    *finished_ids,
                    *(task_id for task_id in tracked_ids if task_id not in running_ids),
                ],
            )
            tracked_successfully = True
        except Exception:
            logger.warning(
                "Could not track background commands",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )
    status_context = SourceContext.from_metadata(status_metadata or metadata)
    delivered = 0
    for task in terminal:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or task.get("notification") == "done":
            continue
        if not await _claim(backend, task_id):
            continue
        message = _notification(task)
        try:
            if metadata.get("task_id"):
                from openswe.tasks.service import recipient_config

                configurable = await recipient_config(thread_id)
            else:
                configurable = follow_up_configurable(metadata, thread_id)
            configurable["background_task_completion"] = True
            # A completion run can change task state before delivery finishes.
            status_metadata = None
            await dispatch_agent_run(
                thread_id,
                message,
                configurable,
                source=str(configurable.get("source") or "dashboard"),
                thread_title=None,
                context=_BACKGROUND_TASK_CONTEXT,
                systems=[_BACKGROUND_TASK_SENDER],
                metadata={},
                multitask_strategy="enqueue",
                source_context=status_context,
            )
            await _mark_delivered(backend, task_id)
            task["notification"] = "done"
            delivered += 1
        except Exception:
            await _unclaim(backend, task_id)
            logger.warning("Failed to deliver background task %s", task_id, exc_info=True)
    pending = sum(task.get("notification") != "done" for task in terminal)
    await sync_slack_background_status(
        client, thread_id, metadata=status_metadata, source_context=status_context
    )
    result = {
        "status": "running" if running or pending else "idle",
        "delivered": delivered,
        "pending": pending,
    }
    return _Reconciled(result, backend, tracked_successfully, owns_legacy_tasks)


HeartbeatOutcome = Literal["running", "finished", "unknown"]


async def keep_sandbox_alive(sandbox_id: str, task_id: str) -> HeartbeatOutcome:
    """Heartbeat for one command; the listing exec is the activity that holds off idle stop."""
    backend = await connect_sandbox(sandbox_id)
    # An unreadable list must fail with a 5xx so the runner retries instead of giving up.
    for task in await _list_tasks(backend, required=True):
        if task.get("task_id") == task_id:
            return "running" if task.get("status") == "running" else "finished"
    return "unknown"


async def monitor_background_tasks(thread_id: str) -> dict[str, Any]:
    """One polling-cron tick: deliver completions, then delete the cron once nothing is left."""
    reconciled = await _reconcile(thread_id)
    backend = reconciled.backend
    if backend is None:
        await _delete_crons(thread_id)
    elif reconciled.result["status"] == "idle" and reconciled.tracked:
        lock = await backend.aexecute(
            f"mkdir -p {shlex.quote(TASK_ROOT)} && mkdir {shlex.quote(MONITOR_LOCK)} 2>/dev/null",
            timeout=10,
        )
        if getattr(lock, "exit_code", None) == 0:
            try:
                fresh = await _list_tasks(backend)
                if not any(
                    task.get("status") == "running"
                    or (
                        task.get("status") in TERMINAL_STATES and task.get("notification") != "done"
                    )
                    for task in fresh
                    if _belongs_to_thread(
                        task, thread_id, owns_legacy_tasks=reconciled.owns_legacy_tasks
                    )
                ):
                    await _delete_crons(thread_id)
            finally:
                await backend.aexecute(
                    f"rmdir {shlex.quote(MONITOR_LOCK)} 2>/dev/null || true", timeout=10
                )
    return reconciled.result
