"""Nonblocking task orchestration using ordinary durable threads."""

import logging
from uuid import uuid4

from langgraph.config import get_config
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import text

from agent.database import postgres
from agent.dispatch import COMPLETION_WEBHOOK_URL
from agent.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from agent.prompts import prompt
from agent.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY
from agent.tasks import authority, ensure_task, role, update_task
from agent.threads import runs as thread_runs
from agent.threads.creation import create_thread
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import PARTICIPANT_EMAILS_KEY, PARTICIPANT_LOGINS_KEY
from agent.webhooks.event_matches import EventMatch

logger = logging.getLogger(__name__)


def current_thread() -> str:
    value = get_config()["configurable"].get("thread_id")
    if not isinstance(value, str) or not value:
        raise PermissionError("An authenticated thread is required")
    return value


async def configure_task(
    acceptance_criteria: list[str], completed: bool = False, assessment: str = ""
) -> dict[str, object]:
    """Maintain acceptance criteria and explicitly assess task completion."""
    thread_id = current_thread()
    await update_task(thread_id, acceptance_criteria, completed, assessment)
    return {"success": True, "completed": completed}


async def task_status() -> dict[str, object]:
    """Read this task's criteria, assessment, and recoverable worker reservations."""
    current = await role(current_thread())
    if current is None:
        return {"task": None}
    async with postgres.transaction() as conn:
        assessment = await conn.scalar(
            text("SELECT assessment FROM coordinated_task WHERE id = :id"),
            {"id": current.task_id},
        )
        workers = (
            (
                await conn.execute(
                    text(
                        "SELECT worker_id, instructions, dispatched FROM task_delegation "
                        "WHERE coordinator_id = :coordinator ORDER BY worker_id"
                    ),
                    {"coordinator": current.coordinator_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "task_id": str(current.task_id),
        "coordinator_id": current.coordinator_id,
        "acceptance_criteria": current.criteria,
        "completed": current.completed,
        "assessment": assessment,
        "delegated": current.delegated,
        "workers": [dict(worker) for worker in workers],
    }


async def spawn_worker(
    instructions: str, model_id: str | None = None, effort: str | None = None
) -> dict[str, object]:
    """Delegate work without waiting for the worker invocation."""
    thread_id = current_thread()
    if not instructions.strip():
        raise ValueError("Worker instructions are required")
    if not COMPLETION_WEBHOOK_URL:
        raise ValueError("Worker delegation requires the durable completion webhook")
    async with authority(thread_id):
        task = await ensure_task(thread_id)
        if task.role != "coordinator":
            raise PermissionError("Workers request help through their coordinator")
        if task.completed or not task.criteria:
            raise ValueError("Configure acceptance criteria for an active task before delegation")
        client = langgraph_client()
        parent = thread_metadata(await client.threads.get(thread_id))
        if (
            parent.get("owner_type") != "user"
            or not isinstance(parent.get("owner_login"), str)
            or not parent.get("owner_login")
        ):
            raise PermissionError("Worker delegation currently requires a user-owned coordinator")
        if parent.get(SANDBOX_HOST_THREAD_KEY):
            raise PermissionError("Sandbox guest threads cannot start independent tasks")
        workspace = parent.get("workspace")
        model, selected_effort = await thread_runs.resolve_task_model(
            {}, model_id, effort, workspace if isinstance(workspace, str) else None
        )
        sandbox_id = parent.get("sandbox_id")
        if not isinstance(sandbox_id, str) or not sandbox_id:
            raise ValueError("The coordinator sandbox must be provisioned before delegation")
        worker_id = str(uuid4())
        async with postgres.transaction() as conn:
            await conn.execute(
                text("UPDATE coordinated_task SET delegated = true WHERE id = :id"),
                {"id": task.task_id},
            )
            await conn.execute(
                text("INSERT INTO task_membership VALUES (:worker, :task, 'worker')"),
                {"worker": worker_id, "task": task.task_id},
            )
            await conn.execute(
                text("""
                INSERT INTO task_delegation(worker_id, coordinator_id, instructions, model, effort, task_id)
                VALUES (:worker, :coordinator, :instructions, :model, :effort, :task)
            """),
                {
                    "worker": worker_id,
                    "task": task.task_id,
                    "coordinator": thread_id,
                    "instructions": instructions,
                    "model": model,
                    "effort": selected_effort,
                },
            )
        try:
            await resume_worker(worker_id)
        except Exception:
            logger.exception("Worker dispatch failed", extra={"worker_id": worker_id})
            return {
                "success": False,
                "worker_id": worker_id,
                "error": "Dispatch failed; use control_worker retry",
            }
        return {"success": True, "worker_id": worker_id, "task_id": str(task.task_id)}


async def resume_worker(worker_id: str) -> None:
    client = langgraph_client()
    async with postgres.transaction() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM task_delegation WHERE worker_id = :id"), {"id": worker_id}
                )
            )
            .mappings()
            .one()
        )
    parent = thread_metadata(await client.threads.get(row["coordinator_id"]))
    metadata = {
        key: parent[key]
        for key in (
            "owner_type",
            "owner_login",
            "started_by_id",
            "started_by_name",
            "visibility",
            "workspace",
            "repo_owner",
            "repo_name",
            "repo_explicitly_none",
            "base_branch",
            "branch_prefix",
            "sandbox_id",
            "sandbox_base_proxy_config",
            GITHUB_TOKEN_REPOSITORIES_KEY,
            PARTICIPANT_LOGINS_KEY,
            PARTICIPANT_EMAILS_KEY,
        )
        if key in parent
    }
    metadata.update(
        source="dashboard",
        origin="task",
        thread_category="interactive",
        sandbox_host_thread_id=row["coordinator_id"],
        model=row["model"],
        effort=row["effort"],
        resolved_model=row["model"],
        resolved_effort=row["effort"],
        model_selection="explicit",
    )
    await create_thread(
        client, worker_id, title=row["instructions"][:80], metadata=metadata, if_exists="do_nothing"
    )
    await notify(
        worker_id,
        worker_id,
        f"initial:{worker_id}",
        prompt("tasks/assignment", instructions=row["instructions"]),
    )
    async with postgres.transaction() as conn:
        await conn.execute(
            text("UPDATE task_delegation SET dispatched = true WHERE worker_id = :id"),
            {"id": worker_id},
        )


async def notify(sender: str, recipient: str, delivery_id: str, content: str) -> None:
    membership = await role(sender)
    if membership is None:
        raise PermissionError("Thread is not a task member")
    target = await role(recipient)
    if target is None or target.task_id != membership.task_id:
        raise PermissionError("Task messages cannot cross task membership")
    metadata = thread_metadata(await langgraph_client().threads.get(recipient))
    login = metadata.get("owner_login")
    if not isinstance(login, str) or not login:
        raise PermissionError("Task thread requires a verified owner")
    config = await thread_runs.build_task_configurable(recipient, login, metadata)
    config = TypeAdapter(dict[str, JsonValue]).validate_python(config)
    async with postgres.session() as session:
        await EventMatch(
            thread_id=recipient,
            subscription_id=membership.task_id,
            source="task",
            delivery_id=delivery_id,
            content=content,
            run_config=config,
        ).record(session)
    await EventMatch.deliver(recipient, "enqueue")


async def message_task_thread(message: str, worker_id: str | None = None) -> dict[str, object]:
    """Send explicit noninterrupting worker instructions, progress, or a request for help."""
    sender = current_thread()
    membership = await role(sender)
    if membership is None:
        raise PermissionError("Configure a task first")
    recipient = membership.coordinator_id
    if membership.role == "coordinator":
        target = await role(worker_id or "")
        if target is None or target.task_id != membership.task_id or target.role != "worker":
            raise PermissionError("Choose a worker belonging to this task")
        recipient = worker_id or ""
    elif worker_id is not None:
        raise PermissionError("Workers may only message their coordinator")
    await notify(
        sender,
        recipient,
        f"message:{uuid4()}",
        prompt("tasks/message", sender=sender, message=message),
    )
    return {"success": True, "recipient": recipient}


async def control_worker(worker_id: str, action: str = "status") -> dict[str, object]:
    """Inspect, cancel, or retry creation of an explicitly selected worker."""
    sender = current_thread()
    current = await role(sender)
    worker = await role(worker_id)
    if (
        current is None
        or current.role != "coordinator"
        or worker is None
        or worker.role != "worker"
        or current.task_id != worker.task_id
    ):
        raise PermissionError("Only this task's coordinator may control its workers")
    client = langgraph_client()
    if action == "retry":
        async with postgres.transaction() as conn:
            await conn.execute(
                text(
                    "UPDATE event_match SET delivery_attempts = 0 "
                    "WHERE thread_id = :worker AND source = 'task' "
                    "AND delivery_id = :delivery"
                ),
                {"worker": worker_id, "delivery": f"initial:{worker_id}"},
            )
        await resume_worker(worker_id)
    elif action == "cancel":
        offset = 0
        while True:
            runs = await client.runs.list(worker_id, limit=100, offset=offset)
            for run in runs:
                if run["status"] in {"pending", "running"}:
                    await client.runs.cancel(worker_id, run["run_id"], action="interrupt")
            if len(runs) < 100:
                break
            offset += len(runs)
    elif action != "status":
        raise ValueError("action must be status, cancel, or retry")
    thread = await client.threads.get(worker_id)
    return {"worker_id": worker_id, "status": thread.get("status")}


async def worker_finished(thread_id: str, run_id: str, status: str) -> bool:
    membership = await role(thread_id)
    if membership is None or membership.role != "worker":
        return False
    try:
        await notify(
            thread_id,
            membership.coordinator_id,
            f"finished:{thread_id}:{run_id}",
            prompt("tasks/finished", worker_id=thread_id, run_id=run_id, status=status),
        )
    except Exception:
        logger.exception(
            "Worker outcome delivery failed", extra={"worker_id": thread_id, "run_id": run_id}
        )
    return True
