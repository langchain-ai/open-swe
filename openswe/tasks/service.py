import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import RunStatus
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import text, update

from openswe.database import postgres
from openswe.dispatch import COMPLETION_WEBHOOK_URL
from openswe.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from openswe.prompts import prompt
from openswe.sandboxes.tool_access import SANDBOX_PROXY_CONFIG_METADATA_KEY
from openswe.source_context import SourceContext
from openswe.tasks import presentation, store
from openswe.tasks.flags import require_task_coordination
from openswe.tasks.messages import TaskMessage
from openswe.tasks.presentation import TaskEventMetadata
from openswe.tasks.schemas import Run, Thread, ThreadMetadata, user_for_login
from openswe.threads.access import resolve_run_email
from openswe.threads.creation import create_thread
from openswe.threads.handlers import interrupt_transcript_turns
from openswe.threads.summary import assert_thread_postable, assert_thread_readable
from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups
from openswe.tools.threads import resolve_thread_actor
from openswe.users import User
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_participants import PARTICIPANT_EMAILS_KEY, PARTICIPANT_LOGINS_KEY
from openswe.utils.thread_settings import thread_model_choice
from openswe.utils.web_links import web_thread_url
from openswe.web.oauth import enforce_github_login_gate
from openswe.web.options import available_requested_models, normalize_model_choice
from openswe.web.profiles import get_profile
from openswe.web.workspace_settings import get_workspace_settings
from openswe.webhooks.event_subscriptions import EventSubscription
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class WorkerLaunchError(Exception):
    """A reserved worker failed to launch; ``retryable`` is False when retrying cannot help."""

    def __init__(self, worker_thread_id: str, error: str, *, retryable: bool) -> None:
        recovery = (
            " Retry this worker with control_worker action=retry."
            if retryable
            else " This worker cannot be retried without correcting the failure."
        )
        super().__init__(f"Worker {worker_thread_id}: {error}.{recovery}")
        self.worker_thread_id = worker_thread_id
        self.retryable = retryable


@dataclass(frozen=True)
class Actor:
    thread_id: str
    login: str
    email: str | None = None

    @classmethod
    async def resolve(cls, state: Actor | dict[str, object]) -> Actor:
        if isinstance(state, Actor):
            return state
        actor = await resolve_thread_actor(state)
        thread_id = get_config().get("configurable", {}).get("thread_id")
        if actor is None or not isinstance(thread_id, str) or not thread_id:
            raise PermissionError("An authenticated user and thread are required")
        return cls(thread_id=thread_id, login=actor.login, email=actor.email)

    async def user(self) -> User:
        user = await user_for_login(self.login)
        if user is None:
            raise PermissionError(
                "Task operations require the thread owner to have an Open SWE account"
            )
        return user


async def authorized_metadata(actor: Actor, thread_id: str | None = None) -> ThreadMetadata:
    if not actor.thread_id or not actor.login:
        raise PermissionError("An authenticated user and thread are required")
    await enforce_github_login_gate(actor.login)
    user = await actor.user()
    target_id = thread_id or actor.thread_id
    metadata = Thread.model_validate(await langgraph_client().threads.get(target_id)).metadata
    owner = await metadata.owner()
    if owner.id != user.id:
        raise PermissionError(
            "Task operations require the thread owner; another user's credentials cannot be used"
        )
    access_metadata = metadata.json_metadata() | {"owner_login": actor.login}
    assert_thread_readable(access_metadata, actor.login, actor.email)
    assert_thread_postable(access_metadata, actor.login, actor.email)
    if metadata.owner_user_id is None:
        await langgraph_client().threads.update(
            target_id, metadata={"owner_user_id": str(owner.id)}
        )
        metadata.owner_user_id = owner.id
    return metadata


async def authorized_context(actor: Actor, *, coordinator: bool = False) -> store.TaskContext:
    metadata = await authorized_metadata(actor)
    context = await store.TaskMembership.context_for_thread(actor.thread_id)
    if context is None:
        raise ValueError("No task exists yet; spawn_worker creates it on first delegation")
    workspace = metadata.workspace or metadata.environment
    if workspace and workspace != context.task.workspace.slug:
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
    inherited_model, inherited_effort = thread_model_choice(metadata)
    chosen_model = model or (inherited_model if inherited_model in choices else default_model)
    option = choices.get(chosen_model)
    if option is None:
        raise ValueError(
            f"The requested model is not available in this workspace; choose one of {', '.join(choices)}"
        )
    chosen_effort = effort or (
        inherited_effort
        if model is None
        and chosen_model == inherited_model
        and inherited_effort in option["efforts"]
        else default_effort
        if chosen_model == default_model
        else option["default_effort"]
    )
    if chosen_effort not in option["efforts"]:
        raise ValueError("The requested effort is not supported by the selected model")
    return chosen_model, chosen_effort


async def recipient_config(thread_id: str) -> dict[str, JsonValue]:
    metadata = Thread.model_validate(await langgraph_client().threads.get(thread_id)).metadata
    owner = await metadata.owner()
    login = owner.github_login
    if not login:
        raise PermissionError("Task delivery requires a user-owned recipient")
    await enforce_github_login_gate(login)
    profile = await get_profile(login) or {}
    email = await resolve_run_email(login, profile)
    values = metadata.json_metadata() | {"owner_login": login}
    assert_thread_postable(values, login, email)
    if metadata.owner_user_id is None:
        await langgraph_client().threads.update(
            thread_id, metadata={"owner_user_id": str(owner.id)}
        )
    config: dict[str, JsonValue] = {
        "thread_id": thread_id,
        "github_login": login,
        "user_email": email,
        "source": metadata.source,
        "workspace": metadata.workspace,
        "admin_thread": metadata.admin_thread,
        "repo_explicitly_none": metadata.repo_explicitly_none,
    }
    config.update(_JSON_OBJECT.validate_python(SourceContext.from_metadata(values).dump()))
    if metadata.repo_owner and metadata.repo_name:
        config["repo"] = {"owner": metadata.repo_owner, "name": metadata.repo_name}
    elif metadata.repo is not None:
        config["repo"] = metadata.repo
    if metadata.model_selection is not None:
        config["model_selection"] = metadata.model_selection
    if metadata.model_selection == "explicit":
        model, effort = normalize_model_choice(metadata.model, metadata.effort)
        if model and effort:
            config["agent_model_id"] = model
            config["agent_effort"] = effort
    return config


async def record_event(
    task: store.Task,
    recipient_thread_id: str,
    delivery_id: str,
    content: str,
    *,
    task_event: TaskEventMetadata | None = None,
) -> None:
    config = await recipient_config(recipient_thread_id)
    workspace = config.get("workspace")
    if workspace and workspace != task.workspace.slug:
        raise PermissionError("The recipient no longer belongs to the task's workspace")
    config["workspace"] = task.workspace.slug
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
        await TaskMessage(
            thread_id=recipient_thread_id,
            task_id=task.id,
            delivery_id=delivery_id,
            content=content,
            run_config=config,
            task_event=task_event.model_dump(mode="json") if task_event is not None else None,
        ).record(session)


async def notify(
    task: store.Task,
    recipient_thread_id: str,
    delivery_id: str,
    content: str,
    *,
    task_event: TaskEventMetadata | None = None,
) -> None:
    await record_event(task, recipient_thread_id, delivery_id, content, task_event=task_event)
    await TaskMessage.deliver(recipient_thread_id, "enqueue")


async def owned_worker(
    actor: Actor, worker_thread_id: str
) -> tuple[store.Task, store.TaskDelegation]:
    context = await authorized_context(actor, coordinator=True)
    delegation = await store.TaskDelegation.get(worker_thread_id)
    membership = await store.TaskMembership.context_for_thread(worker_thread_id)
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
        if metadata.workspace != context.task.workspace.slug:
            raise PermissionError("The worker no longer belongs to the task's workspace")
    except NotFoundError:
        logger.info(
            "Worker thread creation is incomplete", extra={"worker_thread_id": worker_thread_id}
        )
    return context.task, delegation


async def launch_worker(
    task: store.Task, delegation: store.TaskDelegation, metadata: ThreadMetadata
) -> None:
    if delegation.cancelled:
        raise ValueError("This worker was cancelled; create a new worker for further work")
    if not metadata.sandbox_id:
        raise ValueError("The coordinator needs an attached sandbox before delegation")
    if metadata.owner_user_id is None:
        metadata.owner_user_id = (await metadata.owner()).id
    values = metadata.json_metadata()
    worker_metadata = {
        key: values[key]
        for key in (
            "owner_type",
            "owner_user_id",
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
            "sandbox_kind",
            "sandbox_bridge_client",
            SANDBOX_PROXY_CONFIG_METADATA_KEY,
            GITHUB_TOKEN_REPOSITORIES_KEY,
            PARTICIPANT_LOGINS_KEY,
            PARTICIPANT_EMAILS_KEY,
        )
        if key in values
    }
    worker_metadata.update(
        source="dashboard",
        origin="task",
        thread_category="interactive",
        trigger_kind="task_delegation",
        workspace=task.workspace.slug,
        sandbox_host_thread_id=delegation.coordinator_thread_id,
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
    persisted = Thread.model_validate(
        await client.threads.get(delegation.worker_thread_id)
    ).metadata
    expected = ThreadMetadata.model_validate(worker_metadata)
    if (
        persisted.owner_user_id != expected.owner_user_id
        or persisted.owner_type != expected.owner_type
        or persisted.workspace != expected.workspace
        or persisted.task_id != expected.task_id
        or persisted.sandbox_host_thread_id != expected.sandbox_host_thread_id
        or persisted.admin_thread != expected.admin_thread
        or persisted.visibility != expected.visibility
        or persisted.github_token_repositories != expected.github_token_repositories
    ):
        raise PermissionError("The worker identity conflicts with its persisted delegation")
    await record_event(
        task,
        delegation.worker_thread_id,
        f"initial:{delegation.worker_thread_id}",
        prompt(
            "tasks/assignment",
            coordinator_thread_id=delegation.coordinator_thread_id,
            instructions=delegation.instructions,
        ),
    )
    await TaskMessage.deliver(delegation.worker_thread_id, "enqueue")


async def dispatch_reserved_worker(
    actor: Actor, task: store.Task, delegation: store.TaskDelegation
) -> dict[str, object]:
    try:
        metadata = await authorized_metadata(actor)
        await launch_worker(task, delegation, metadata)
    except Exception as exc:
        logger.exception(
            "Worker launch is incomplete", extra={"worker_thread_id": delegation.worker_thread_id}
        )
        await store.TaskDelegation.set_launch_error(delegation.worker_thread_id, str(exc)[:2000])
        # A conflicting identity or a cancelled worker fails the same way on every retry.
        raise WorkerLaunchError(
            delegation.worker_thread_id,
            str(exc),
            retryable=not isinstance(exc, (PermissionError, ValueError)),
        ) from exc
    await store.TaskDelegation.set_launch_error(delegation.worker_thread_id, None)
    return {
        "success": True,
        "worker_thread_id": delegation.worker_thread_id,
        "task_id": str(task.id),
        "thread_url": web_thread_url(delegation.worker_thread_id),
    }


async def spawn_worker(
    actor: Annotated[Actor | dict[str, object], InjectedState],
    instructions: str,
    request_id: Annotated[str, InjectedToolCallId],
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, object]:
    actor = await Actor.resolve(actor)
    if not request_id or not instructions.strip():
        raise ValueError("Assignment instructions and a stable tool-call identity are required")
    if not COMPLETION_WEBHOOK_URL:
        raise ValueError("Worker delegation requires a configured completion webhook")
    metadata = await authorized_metadata(actor)
    context = await store.TaskMembership.context_for_thread(actor.thread_id)
    if context is not None:
        context = await authorized_context(actor, coordinator=True)
    await require_task_coordination(metadata.json_metadata())
    if metadata.sandbox_host_thread_id:
        raise PermissionError("Sandbox guests must ask their coordinator for additional workers")
    if not metadata.sandbox_id:
        raise ValueError("The coordinator needs an attached sandbox before delegation")
    workspace_value = metadata.workspace or metadata.environment
    workspace = await resolve_workspace(
        thread_workspace=(context.task.workspace.slug if context is not None else workspace_value),
        login=actor.login,
    )
    chosen_model, chosen_effort = await model_choice(
        workspace.slug, metadata.json_metadata(), model, effort
    )
    title = metadata.title
    worker_id = str(uuid5(NAMESPACE_URL, f"open-swe:task-worker:{actor.thread_id}:{request_id}"))
    task, delegation = await store.Task.reserve_worker(
        actor.thread_id,
        worker_id,
        title=title.strip() if title and title.strip() else "Delegated work",
        workspace=workspace.slug,
        instructions=instructions.strip(),
        model=chosen_model,
        effort=chosen_effort,
    )
    return await dispatch_reserved_worker(actor, task, delegation)


async def worker_status(delegation: store.TaskDelegation) -> dict[str, object]:
    client = langgraph_client()
    try:
        thread = Thread.model_validate(await client.threads.get(delegation.worker_thread_id))
        runs = [
            Run.model_validate(run)
            for run in await client.runs.list(delegation.worker_thread_id, limit=1)
        ]
        status = thread.status
        latest_run = {"run_id": runs[0].run_id, "status": runs[0].status} if runs else None
    except NotFoundError:
        logger.info(
            "Worker thread creation is incomplete",
            extra={"worker_thread_id": delegation.worker_thread_id},
        )
        status, latest_run = "not_created", None
    return {
        "worker_thread_id": delegation.worker_thread_id,
        "thread_url": web_thread_url(delegation.worker_thread_id),
        "instructions": delegation.instructions,
        "model": delegation.model,
        "effort": delegation.effort,
        "launch_error": delegation.launch_error,
        "cancelled": delegation.cancelled,
        "status": status,
        "latest_run": latest_run,
    }


async def task_status(
    actor: Annotated[Actor | dict[str, object], InjectedState],
) -> dict[str, object]:
    actor = await Actor.resolve(actor)
    context = await authorized_context(actor)
    workers = []
    for delegation in await store.TaskDelegation.for_task(context.task.id):
        try:
            await authorized_metadata(actor, delegation.worker_thread_id)
        except NotFoundError:
            logger.info(
                "Worker thread creation is incomplete",
                extra={"worker_thread_id": delegation.worker_thread_id},
            )
        workers.append(await worker_status(delegation))
    return {**task_details(context.task), "workers": workers}


def task_details(task: store.Task) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "coordinator_thread_id": task.coordinator_thread_id,
        "delegated": task.delegated,
    }


async def message_task_thread(
    actor: Annotated[Actor | dict[str, object], InjectedState],
    message: str,
    request_id: Annotated[str, InjectedToolCallId],
    worker_thread_id: str | None = None,
) -> dict[str, object]:
    actor = await Actor.resolve(actor)
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
        recipient = context.task.require_coordinator()
        await authorized_metadata(actor, recipient)
    await record_event(
        context.task,
        recipient,
        f"message:{actor.thread_id}:{request_id}",
        prompt(
            "tasks/message",
            sender_thread_id=actor.thread_id,
            sender_role=context.membership.role,
            message=message.strip(),
        ),
        task_event=TaskEventMetadata(
            task_id=context.task.id,
            sender_thread_id=UUID(actor.thread_id),
            sender_role=context.membership.role,
            sender_label=await presentation.sender_label(actor.thread_id),
            kind="message",
            content=message,
        ),
    )
    try:
        await TaskMessage.deliver(recipient, "enqueue")
    except Exception:
        # The message is saved, so reporting failure would invite a duplicate resend.
        logger.exception(
            "Task message saved but not yet delivered", extra={"recipient_thread_id": recipient}
        )
        return {"success": True, "recipient_thread_id": recipient, "delivery": "pending"}
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
    actor: Annotated[Actor | dict[str, object], InjectedState],
    worker_thread_id: str,
    action: Literal["status", "cancel", "retry"],
) -> dict[str, object]:
    actor = await Actor.resolve(actor)
    task, delegation = await owned_worker(actor, worker_thread_id)
    if action == "retry":
        if delegation.cancelled:
            raise ValueError("Only an uncancelled worker launch can be retried")
        await require_task_coordination((await authorized_metadata(actor)).json_metadata())
        async with postgres.session() as session:
            await session.execute(
                update(TaskMessage)
                .where(
                    TaskMessage.thread_id == worker_thread_id,
                    TaskMessage.delivery_id == f"initial:{worker_thread_id}",
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
            await EventSubscription.forget(worker_thread_id, session=session)
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
        await TaskMessage.forget(worker_thread_id)
        await interrupt_transcript_turns(worker_thread_id, run_ids)
        await cancel_thread_wakeups(worker_thread_id)
        delegation.cancelled = True
        return {**await worker_status(delegation), "cancellation_requested": True}
    return await worker_status(delegation)
