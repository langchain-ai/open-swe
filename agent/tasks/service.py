import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import RunStatus
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import delete, text, update

from agent.dashboard.oauth import enforce_github_login_gate
from agent.dashboard.options import available_requested_models
from agent.dashboard.profiles import get_profile
from agent.dashboard.workspace_settings import get_workspace_settings
from agent.database import postgres
from agent.dispatch import COMPLETION_WEBHOOK_URL
from agent.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from agent.prompts import prompt
from agent.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY, SANDBOX_PROXY_CONFIG_METADATA_KEY
from agent.source_context import SourceContext
from agent.tasks import presentation, store
from agent.tasks.flags import require_task_coordination
from agent.tasks.presentation import TaskEventMetadata
from agent.threads.access import resolve_run_email
from agent.threads.creation import create_thread
from agent.threads.handlers import interrupt_transcript_turns
from agent.threads.summary import assert_thread_postable, assert_thread_readable, thread_is_owner
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import PARTICIPANT_EMAILS_KEY, PARTICIPANT_LOGINS_KEY
from agent.webhooks.event_matches import EventMatch
from agent.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class Actor:
    thread_id: str
    login: str
    email: str | None = None


async def authorized_metadata(actor: Actor, thread_id: str | None = None) -> dict[str, JsonValue]:
    if not actor.thread_id or not actor.login:
        raise PermissionError("An authenticated user and thread are required")
    await enforce_github_login_gate(actor.login)
    metadata = _JSON_OBJECT.validate_python(
        thread_metadata(await langgraph_client().threads.get(thread_id or actor.thread_id))
    )
    assert_thread_readable(metadata, actor.login, actor.email)
    assert_thread_postable(metadata, actor.login, actor.email)
    if metadata.get("owner_type") != "user" or not thread_is_owner(metadata, actor.login):
        raise PermissionError(
            "Task operations require the thread owner; another user's credentials cannot be used"
        )
    return metadata


async def authorized_context(actor: Actor, *, coordinator: bool = False) -> store.TaskContext:
    metadata = await authorized_metadata(actor)
    context = await store.load_context(actor.thread_id)
    if context is None:
        raise ValueError("No task exists yet; spawn_worker creates it on first delegation")
    workspace = metadata.get("workspace") or metadata.get("environment")
    if workspace and workspace != context.task.workspace:
        raise PermissionError("The thread no longer belongs to the task's workspace")
    if coordinator and (
        context.membership.role != "coordinator"
        or context.task.coordinator_thread_id != actor.thread_id
    ):
        raise PermissionError("Workers must ask their permanent coordinator for help")
    return context


async def model_choice(
    workspace: str,
    metadata: Mapping[str, object],
    model: str | None,
    effort: str | None,
) -> tuple[str, str]:
    settings = await get_workspace_settings(workspace)
    choices = available_requested_models(fable_enabled=settings.fable_enabled)
    default_model, default_effort = settings.default_model("agent")
    inherited_model = metadata.get("resolved_model")
    chosen_model = model or (
        inherited_model
        if isinstance(inherited_model, str) and inherited_model in choices
        else default_model
    )
    option = choices.get(chosen_model)
    if option is None:
        raise ValueError("The requested model is not available in this workspace")
    inherited_effort = metadata.get("resolved_effort")
    chosen_effort = effort or (
        inherited_effort
        if model is None
        and isinstance(inherited_effort, str)
        and inherited_effort in option["efforts"]
        else default_effort
        if chosen_model == default_model
        else option["default_effort"]
    )
    if chosen_effort not in option["efforts"]:
        raise ValueError("The requested effort is not supported by the selected model")
    return chosen_model, chosen_effort


async def recipient_config(thread_id: str) -> dict[str, JsonValue]:
    metadata = _JSON_OBJECT.validate_python(
        thread_metadata(await langgraph_client().threads.get(thread_id))
    )
    login = metadata.get("owner_login")
    if metadata.get("owner_type") != "user" or not isinstance(login, str) or not login:
        raise PermissionError("Task delivery requires a user-owned recipient")
    await enforce_github_login_gate(login)
    profile = await get_profile(login) or {}
    email = await resolve_run_email(login, profile)
    assert_thread_postable(metadata, login, email)
    config: dict[str, JsonValue] = {
        "thread_id": thread_id,
        "github_login": login,
        "user_email": email,
        "source": metadata.get("source", "dashboard"),
    }
    config.update(_JSON_OBJECT.validate_python(SourceContext.from_metadata(metadata).dump()))
    for key in ("workspace", "admin_thread", "repo_explicitly_none"):
        if key in metadata:
            config[key] = metadata[key]
    owner, name = metadata.get("repo_owner"), metadata.get("repo_name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        config["repo"] = {"owner": owner, "name": name}
    elif isinstance(metadata.get("repo"), dict):
        config["repo"] = metadata["repo"]
    if isinstance(metadata.get("resolved_model"), str):
        config["agent_model_id"] = metadata["resolved_model"]
        config["agent_effort"] = metadata.get("resolved_effort")
        config["model_selection"] = metadata.get("model_selection", "explicit")
    return config


async def record_event(
    task: store.CoordinatedTask,
    recipient_thread_id: str,
    delivery_id: str,
    content: str,
    *,
    task_event: TaskEventMetadata | None = None,
) -> None:
    config = await recipient_config(recipient_thread_id)
    workspace = config.get("workspace")
    if workspace and workspace != task.workspace:
        raise PermissionError("The recipient no longer belongs to the task's workspace")
    config["workspace"] = task.workspace
    async with postgres.session() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"event_match:{recipient_thread_id}"},
        )
        membership = await session.get(store.TaskMembership, recipient_thread_id)
        if membership is None or membership.task_id != task.id:
            raise PermissionError("Task messages cannot cross task membership")
        delegation = await session.get(store.TaskDelegation, recipient_thread_id)
        if delegation is not None and delegation.cancelled:
            raise ValueError("This worker was cancelled; create a new worker for further work")
        await EventMatch(
            thread_id=recipient_thread_id,
            subscription_id=task.id,
            source="task",
            delivery_id=delivery_id,
            content=content,
            run_config=config,
            task_event=task_event.model_dump(mode="json") if task_event is not None else None,
        ).record(session)


async def notify(
    task: store.CoordinatedTask,
    recipient_thread_id: str,
    delivery_id: str,
    content: str,
    *,
    task_event: TaskEventMetadata | None = None,
) -> None:
    await record_event(task, recipient_thread_id, delivery_id, content, task_event=task_event)
    await EventMatch.deliver(recipient_thread_id, "enqueue")


async def owned_worker(
    actor: Actor, worker_thread_id: str
) -> tuple[store.CoordinatedTask, store.TaskDelegation]:
    context = await authorized_context(actor, coordinator=True)
    delegation = await store.get_delegation(worker_thread_id)
    membership = await store.load_context(worker_thread_id)
    if (
        delegation is None
        or delegation.task_id != context.task.id
        or delegation.coordinator_thread_id != actor.thread_id
        or membership is None
        or membership.task.id != context.task.id
        or membership.membership.role != "worker"
    ):
        raise PermissionError("Choose a worker belonging to this task")
    try:
        metadata = await authorized_metadata(actor, worker_thread_id)
        if metadata.get("workspace") != context.task.workspace:
            raise PermissionError("The worker no longer belongs to the task's workspace")
    except NotFoundError:
        logger.info(
            "Worker thread creation is incomplete", extra={"worker_thread_id": worker_thread_id}
        )
    return context.task, delegation


async def launch_worker(
    task: store.CoordinatedTask, delegation: store.TaskDelegation, metadata: Mapping[str, JsonValue]
) -> None:
    if delegation.cancelled:
        raise ValueError("This worker was cancelled; create a new worker for further work")
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise ValueError("The coordinator needs an attached sandbox before delegation")
    worker_metadata = {
        key: metadata[key]
        for key in (
            "owner_type",
            "owner_login",
            "visibility",
            "admin_thread",
            "repo",
            "repo_owner",
            "repo_name",
            "repo_explicitly_none",
            "base_branch",
            "branch_prefix",
            "sandbox_id",
            SANDBOX_PROXY_CONFIG_METADATA_KEY,
            GITHUB_TOKEN_REPOSITORIES_KEY,
            PARTICIPANT_LOGINS_KEY,
            PARTICIPANT_EMAILS_KEY,
        )
        if key in metadata
    }
    worker_metadata.update(
        source="dashboard",
        origin="task",
        thread_category="interactive",
        trigger_kind="task_delegation",
        workspace=task.workspace,
        sandbox_host_thread_id=task.coordinator_thread_id,
        task_id=str(task.id),
        model=delegation.model,
        effort=delegation.effort,
        resolved_model=delegation.model,
        resolved_effort=delegation.effort,
        model_selection="explicit",
    )
    client = langgraph_client()
    await create_thread(
        client,
        delegation.worker_thread_id,
        title=delegation.instructions[:80],
        if_exists="do_nothing",
        metadata=worker_metadata,
    )
    persisted = thread_metadata(await client.threads.get(delegation.worker_thread_id))
    for key in (
        "owner_login",
        "owner_type",
        "workspace",
        "task_id",
        SANDBOX_HOST_THREAD_KEY,
        "admin_thread",
        "visibility",
        GITHUB_TOKEN_REPOSITORIES_KEY,
    ):
        if persisted.get(key) != worker_metadata.get(key):
            raise PermissionError("The worker identity conflicts with its persisted delegation")
    await record_event(
        task,
        delegation.worker_thread_id,
        f"initial:{delegation.worker_thread_id}",
        prompt(
            "tasks/assignment",
            coordinator_thread_id=task.coordinator_thread_id,
            instructions=delegation.instructions,
        ),
    )
    await EventMatch.deliver(delegation.worker_thread_id, "enqueue")


async def dispatch_reserved_worker(
    actor: Actor, task: store.CoordinatedTask, delegation: store.TaskDelegation
) -> dict[str, object]:
    try:
        metadata = await authorized_metadata(actor)
        await launch_worker(task, delegation, metadata)
    except Exception as exc:
        logger.exception(
            "Worker launch is incomplete", extra={"worker_thread_id": delegation.worker_thread_id}
        )
        await store.set_launch_error(delegation.worker_thread_id, str(exc)[:2000])
        return {
            "success": False,
            "worker_thread_id": delegation.worker_thread_id,
            "error": str(exc),
            "recovery": "Use control_worker with action=retry and this worker_thread_id",
        }
    await store.set_launch_error(delegation.worker_thread_id, None)
    return {
        "success": True,
        "worker_thread_id": delegation.worker_thread_id,
        "task_id": str(task.id),
        "thread_url": dashboard_thread_url(delegation.worker_thread_id),
    }


async def spawn_worker(
    actor: Actor, *, instructions: str, model: str | None, effort: str | None, request_id: str
) -> dict[str, object]:
    if not request_id or not instructions.strip():
        raise ValueError("Assignment instructions and a stable tool-call identity are required")
    if not COMPLETION_WEBHOOK_URL:
        raise ValueError("Worker delegation requires a configured completion webhook")
    metadata = await authorized_metadata(actor)
    context = await store.load_context(actor.thread_id)
    if context is not None:
        context = await authorized_context(actor, coordinator=True)
    await require_task_coordination(metadata)
    if metadata.get(SANDBOX_HOST_THREAD_KEY):
        raise PermissionError("Sandbox guests must ask their coordinator for additional workers")
    if not metadata.get("sandbox_id"):
        raise ValueError("The coordinator needs an attached sandbox before delegation")
    workspace_value = metadata.get("workspace") or metadata.get("environment")
    workspace = await resolve_workspace(
        thread_workspace=(
            context.task.workspace
            if context is not None
            else workspace_value
            if isinstance(workspace_value, str)
            else None
        ),
        login=actor.login,
    )
    chosen_model, chosen_effort = await model_choice(workspace.slug, metadata, model, effort)
    title = metadata.get("title")
    worker_id = str(uuid5(NAMESPACE_URL, f"open-swe:task-worker:{actor.thread_id}:{request_id}"))
    task, delegation = await store.reserve_worker(
        actor.thread_id,
        worker_id,
        title=title.strip() if isinstance(title, str) and title.strip() else "Delegated work",
        workspace=workspace.slug,
        instructions=instructions.strip(),
        model=chosen_model,
        effort=chosen_effort,
    )
    return await dispatch_reserved_worker(actor, task, delegation)


async def worker_status(delegation: store.TaskDelegation) -> dict[str, object]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(delegation.worker_thread_id)
        runs = await client.runs.list(delegation.worker_thread_id, limit=1)
        status = thread.get("status")
        latest_run = {"run_id": runs[0]["run_id"], "status": runs[0]["status"]} if runs else None
    except NotFoundError:
        logger.info(
            "Worker thread creation is incomplete",
            extra={"worker_thread_id": delegation.worker_thread_id},
        )
        status, latest_run = "not_created", None
    return {
        "worker_thread_id": delegation.worker_thread_id,
        "thread_url": dashboard_thread_url(delegation.worker_thread_id),
        "instructions": delegation.instructions,
        "model": delegation.model,
        "effort": delegation.effort,
        "launch_error": delegation.launch_error,
        "cancelled": delegation.cancelled,
        "status": status,
        "latest_run": latest_run,
    }


async def task_status(actor: Actor) -> dict[str, object]:
    context = await authorized_context(actor)
    workers = []
    for delegation in await store.list_delegations(context.task.id):
        try:
            await authorized_metadata(actor, delegation.worker_thread_id)
        except NotFoundError:
            logger.info(
                "Worker thread creation is incomplete",
                extra={"worker_thread_id": delegation.worker_thread_id},
            )
        workers.append(await worker_status(delegation))
    return {**task_details(context.task), "workers": workers}


def task_details(task: store.CoordinatedTask) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "coordinator_thread_id": task.coordinator_thread_id,
        "delegated": task.delegated,
    }


async def message_task_thread(
    actor: Actor, *, message: str, worker_thread_id: str | None, request_id: str
) -> dict[str, object]:
    if not message.strip() or not request_id:
        raise ValueError("A message and stable tool-call identity are required")
    context = await authorized_context(actor)
    if context.membership.role == "coordinator":
        if not worker_thread_id:
            raise ValueError("Choose an explicit worker_thread_id")
        await owned_worker(actor, worker_thread_id)
        recipient = worker_thread_id
    else:
        if worker_thread_id is not None:
            raise PermissionError("Workers can only message their coordinator")
        recipient = context.task.coordinator_thread_id
        await authorized_metadata(actor, recipient)
    await notify(
        context.task,
        recipient,
        f"message:{actor.thread_id}:{request_id}",
        prompt("tasks/message", sender_thread_id=actor.thread_id, message=message.strip()),
        task_event=TaskEventMetadata(
            task_id=context.task.id,
            sender_thread_id=UUID(actor.thread_id),
            sender_role=context.membership.role,
            sender_label=await presentation.sender_label(actor.thread_id),
            kind="message",
            content=message,
        ),
    )
    return {"success": True, "recipient_thread_id": recipient}


async def live_worker_runs(worker_thread_id: str) -> list[str]:
    client = langgraph_client()
    run_ids: list[str] = []
    statuses: tuple[RunStatus, ...] = ("pending", "running")
    for status in statuses:
        offset = 0
        while True:
            runs = await client.runs.list(worker_thread_id, status=status, limit=100, offset=offset)
            run_ids.extend(run["run_id"] for run in runs)
            if len(runs) < 100:
                break
            offset += 100
    return run_ids


async def control_worker(
    actor: Actor, *, worker_thread_id: str, action: Literal["status", "cancel", "retry"]
) -> dict[str, object]:
    task, delegation = await owned_worker(actor, worker_thread_id)
    if action == "retry":
        if delegation.cancelled:
            raise ValueError("Only an uncancelled worker launch can be retried")
        await require_task_coordination(await authorized_metadata(actor))
        async with postgres.session() as session:
            await session.execute(
                update(EventMatch)
                .where(
                    EventMatch.thread_id == worker_thread_id,
                    EventMatch.source == "task",
                    EventMatch.delivery_id == f"initial:{worker_thread_id}",
                )
                .values(delivery_attempts=0)
            )
        return await dispatch_reserved_worker(actor, task, delegation)
    if action == "cancel":
        async with postgres.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"event_match:{worker_thread_id}"},
            )
            await session.execute(
                update(store.TaskDelegation)
                .where(store.TaskDelegation.worker_thread_id == worker_thread_id)
                .values(cancelled=True)
            )
            await session.execute(
                delete(EventMatch).where(
                    EventMatch.thread_id == worker_thread_id, EventMatch.source == "task"
                )
            )
            try:
                run_ids = await live_worker_runs(worker_thread_id)
            except NotFoundError:
                logger.info(
                    "Cancelled worker before thread creation",
                    extra={"worker_thread_id": worker_thread_id},
                )
                run_ids = []
            if run_ids:
                await langgraph_client().runs.cancel_many(
                    thread_id=worker_thread_id, run_ids=run_ids, action="interrupt"
                )
        await interrupt_transcript_turns(worker_thread_id, run_ids)
        delegation.cancelled = True
        return {**await worker_status(delegation), "cancellation_requested": True}
    return await worker_status(delegation)
