import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import RunStatus
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import text, update

from openswe.agent_api.client import MDA_GRAPH_METADATA_KEY, MDA_WORKER_GRAPH_ID
from openswe.dashboard.oauth import enforce_github_login_gate
from openswe.dashboard.options import available_requested_models, normalize_model_choice
from openswe.dashboard.profiles import get_profile
from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.database import postgres
from openswe.dispatch import COMPLETION_WEBHOOK_URL
from openswe.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from openswe.prompts import prompt
from openswe.sandboxes.tool_access import SANDBOX_PROXY_CONFIG_METADATA_KEY
from openswe.source_context import SourceContext
from openswe.tasks import presentation, store
from openswe.tasks.messages import TaskMessage
from openswe.tasks.presentation import TaskEventMetadata
from openswe.tasks.schemas import LaunchedThread, Run, Thread, ThreadMetadata, user_for_login
from openswe.threads.access import resolve_run_email
from openswe.threads.creation import create_thread
from openswe.threads.handlers import interrupt_transcript_turns
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.status import refresh_thread_status
from openswe.threads.summary import assert_thread_postable, assert_thread_readable
from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups
from openswe.tools.threads import resolve_thread_actor
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.thread_ops import langgraph_client
from openswe.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    participant_ids,
)
from openswe.utils.thread_settings import thread_model_choice
from openswe.webhooks.event_subscriptions import EventSubscription
from openswe.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
# The async subagent Open SWE runs its workers as.
ASYNC_WORKER_SUBAGENT = "general-purpose"


@dataclass(frozen=True)
class Actor:
    """A run operating on its own thread, which the backend-signed run token binds it to.

    ``login`` is the person the run acts for; a system-owned thread's run may have none.
    """

    thread_id: str
    login: str | None = None
    email: str | None = None

    @classmethod
    async def resolve(cls, state: Actor | dict[str, object]) -> Actor:
        if isinstance(state, Actor):
            return state
        thread_id = get_config().get("configurable", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise PermissionError("Task operations require the run's thread")
        person = await resolve_thread_actor(state)
        if person is None:
            return cls(thread_id=thread_id)
        return cls(thread_id=thread_id, login=person.login, email=person.email)

    async def authorize_owner(
        self, thread_id: str, metadata: ThreadMetadata, *, allow_participants: bool = False
    ) -> None:
        """A user-owned thread acts only for its owner, so nobody uses another's credentials.

        ``allow_participants`` also admits a verified participant of the thread, with
        read/post access checked against the owner's metadata; the participant still
        never acts as anyone but themselves.
        """
        if not self.login:
            raise PermissionError("Task operations on a user-owned thread require its owner")
        await enforce_github_login_gate(self.login)
        user = await user_for_login(self.login)
        if user is None:
            raise PermissionError(
                "Task operations require the thread owner to have an Open SWE account"
            )
        owner = await metadata.owner()
        is_owner = owner.id == user.id
        access_metadata = metadata.json_metadata() | {
            "owner_login": self.login if is_owner else owner.github_login
        }
        if not is_owner and not (
            allow_participants and f"user:{user.id}" in participant_ids(access_metadata)
        ):
            raise PermissionError(
                "Task operations require the thread owner; another user's credentials cannot be used"
            )
        assert_thread_readable(access_metadata, self.login, self.email)
        assert_thread_postable(access_metadata, self.login, self.email)
        if metadata.owner_user_id is None:
            await langgraph_client().threads.update(
                thread_id, metadata={"owner_user_id": str(owner.id)}
            )
            metadata.owner_user_id = owner.id


async def thread_metadata(thread_id: str) -> ThreadMetadata:
    return Thread.model_validate(await langgraph_client().threads.get(thread_id)).metadata


async def authorized_metadata(
    actor: Actor, thread_id: str | None = None, *, allow_participants: bool = False
) -> ThreadMetadata:
    """A task thread's metadata, once the actor's run may operate on it.

    Every thread of a task has its coordinator's owner. A user-owned thread is
    operated only by a run acting for that person. A system-owned thread acts as
    the GitHub App, for no person: its own run is authorized by the run token that
    binds the run to it, and another thread's run only by sharing its task.
    ``allow_participants`` admits a verified participant on the actor's own thread only.
    """
    own = await thread_metadata(actor.thread_id)
    if own.owner_type == "system":
        if own.visibility != "public":
            raise PermissionError("A system-owned thread cannot be private")
    else:
        await actor.authorize_owner(actor.thread_id, own, allow_participants=allow_participants)
    if thread_id is None or thread_id == actor.thread_id:
        return own
    metadata = await thread_metadata(thread_id)
    if metadata.owner_type != own.owner_type:
        raise PermissionError("A task's threads share their coordinator's owner")
    if metadata.owner_type == "system":
        mine = await store.TaskMembership.context_for_thread(actor.thread_id)
        theirs = await store.TaskMembership.context_for_thread(thread_id)
        if mine is None or theirs is None or mine.task.id != theirs.task.id:
            raise PermissionError("A system-owned thread operates only its own task's threads")
    else:
        await actor.authorize_owner(thread_id, metadata)
    return metadata


async def authorized_context(
    actor: Actor, *, coordinator: bool = False, allow_participants: bool = False
) -> store.TaskContext:
    metadata = await authorized_metadata(actor, allow_participants=allow_participants)
    context = await store.TaskMembership.context_for_thread(actor.thread_id)
    if context is None:
        raise ValueError("No task exists yet; launching the first worker creates it")
    if allow_participants and (
        context.membership.role != "coordinator"
        or context.task.coordinator_thread_id != actor.thread_id
    ):
        # Participants steer only from the coordinator; a worker thread stays owner-only.
        await authorized_metadata(actor)
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
    """The run config that wakes a task thread, acting for its owner or, if system-owned, no one."""
    metadata = await thread_metadata(thread_id)
    values = metadata.json_metadata()
    config: dict[str, JsonValue] = {
        "thread_id": thread_id,
        "source": metadata.source,
        "workspace": metadata.workspace,
        "admin_thread": metadata.admin_thread,
        "repo_explicitly_none": metadata.repo_explicitly_none,
    }
    if metadata.owner_type != "system":
        owner = await metadata.owner()
        login = owner.github_login
        if not login:
            raise PermissionError("Task delivery requires the owner's GitHub login")
        await enforce_github_login_gate(login)
        profile = await get_profile(login) or {}
        email = await resolve_run_email(login, profile)
        values |= {"owner_login": login}
        assert_thread_postable(values, login, email)
        if metadata.owner_user_id is None:
            await langgraph_client().threads.update(
                thread_id, metadata={"owner_user_id": str(owner.id)}
            )
        config |= {"github_login": login, "user_email": email}
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
) -> bool:
    """Record a message for the recipient; false when its sender was silenced and owes nothing."""
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
        # Checked under the recipient's lock, which a silencing stop takes to withdraw the
        # sender's messages, so a report racing that stop is either withdrawn or never recorded.
        sender = (
            await session.get(store.TaskDelegation, str(task_event.sender_thread_id))
            if task_event is not None
            else None
        )
        if sender is not None and sender.silenced:
            logger.info(
                "Dropped a message from a silenced worker",
                extra={"worker_thread_id": sender.worker_thread_id, "delivery_id": delivery_id},
            )
            return False
        await TaskMessage(
            thread_id=recipient_thread_id,
            task_id=task.id,
            delivery_id=delivery_id,
            content=content,
            run_config=config,
            task_event=task_event.model_dump(mode="json") if task_event is not None else None,
        ).record(session)
        return True


async def notify(
    task: store.Task,
    recipient_thread_id: str,
    delivery_id: str,
    content: str,
    *,
    task_event: TaskEventMetadata | None = None,
) -> None:
    if await record_event(task, recipient_thread_id, delivery_id, content, task_event=task_event):
        await TaskMessage.deliver(recipient_thread_id, "enqueue")


async def _status_owner_actor(actor: Actor) -> Actor:
    """The coordinator owner's identity, solely to validate read-only worker status access."""
    own = await thread_metadata(actor.thread_id)
    if own.owner_type == "system" or not actor.login:
        return actor
    user = await user_for_login(actor.login)
    if user is not None and user.id == (await own.owner()).id:
        return actor
    config = await recipient_config(actor.thread_id)
    email = config.get("user_email")
    return Actor(
        actor.thread_id, str(config["github_login"]), email if isinstance(email, str) else None
    )


@dataclass(frozen=True)
class WorkerAccess:
    actor: Actor
    context: store.TaskContext
    coordinator_metadata: ThreadMetadata

    async def worker(self, worker_thread_id: str) -> store.TaskDelegation:
        delegation = await store.TaskDelegation.get(worker_thread_id)
        membership = await store.TaskMembership.context_for_thread(worker_thread_id)
        if (
            self.context.membership.role != "coordinator"
            or self.context.task.coordinator_thread_id != self.actor.thread_id
            or delegation is None
            or delegation.task_id != self.context.task.id
            or delegation.coordinator_thread_id != self.actor.thread_id
            or membership is None
            or membership.task.id != self.context.task.id
            or membership.membership.role != "worker"
        ):
            raise PermissionError("Choose a worker belonging to this task")
        try:
            metadata = await thread_metadata(worker_thread_id)
            if metadata.owner_type != self.coordinator_metadata.owner_type:
                raise PermissionError("A task's threads share their coordinator's owner")
            if metadata.owner_type != "system":
                await self.actor.authorize_owner(worker_thread_id, metadata)
            if metadata.workspace != self.context.task.workspace.slug:
                raise PermissionError("The worker no longer belongs to the task's workspace")
        except NotFoundError:
            logger.info(
                "Worker thread creation is incomplete",
                extra={"worker_thread_id": worker_thread_id},
            )
        return delegation


async def owned_worker(
    actor: Actor, worker_thread_id: str, *, allow_participants: bool = False
) -> tuple[store.Task, store.TaskDelegation]:
    context = await authorized_context(
        actor, coordinator=True, allow_participants=allow_participants
    )
    metadata = await thread_metadata(actor.thread_id)
    if allow_participants:
        actor = await _status_owner_actor(actor)
    access = WorkerAccess(actor, context, metadata)
    return context.task, await access.worker(worker_thread_id)


def _worker_metadata(
    task: store.Task, delegation: store.TaskDelegation, metadata: ThreadMetadata
) -> dict[str, JsonValue]:
    """What a worker thread inherits from its coordinator, and what makes it the task's worker."""
    values = metadata.json_metadata()
    # A system-owned coordinator's workers are system-owned too, never a person's.
    owner_keys = ("owner_user_id", "owner_login") if metadata.owner_type == "user" else ()
    worker_metadata = {
        key: values[key]
        for key in (
            "owner_type",
            *owner_keys,
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
    return worker_metadata


async def index_worker(
    task: store.Task,
    delegation: store.TaskDelegation,
    metadata: ThreadMetadata,
    extra: Mapping[str, JsonValue] | None = None,
) -> None:
    """Make the reserved worker an indexed thread with its coordinator's owner and sandbox."""
    if delegation.cancelled:
        raise ValueError("This worker was cancelled; create a new worker for further work")
    if not metadata.sandbox_id:
        raise ValueError("The coordinator needs an attached sandbox before delegation")
    if metadata.owner_type == "user" and metadata.owner_user_id is None:
        metadata.owner_user_id = (await metadata.owner()).id
    worker_metadata = {**_worker_metadata(task, delegation, metadata), **(extra or {})}
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
        or any(persisted.json_metadata().get(key) != value for key, value in (extra or {}).items())
    ):
        raise PermissionError("The worker identity conflicts with its persisted delegation")


def worker_reply(task: store.Task, delegation: store.TaskDelegation) -> dict[str, object]:
    return {
        "success": True,
        "worker_thread_id": delegation.worker_thread_id,
        "task_id": str(task.id),
        "thread_url": dashboard_thread_url(delegation.worker_thread_id),
    }


@dataclass(frozen=True)
class Delegation:
    """A coordinator cleared to delegate, with the workspace and model its next worker gets."""

    actor: Actor
    metadata: ThreadMetadata
    workspace: str
    model: str
    effort: str

    @classmethod
    async def authorize(
        cls, actor: Actor, *, model: str | None = None, effort: str | None = None
    ) -> Delegation:
        if not COMPLETION_WEBHOOK_URL:
            raise ValueError("Worker delegation requires a configured completion webhook")
        metadata = await authorized_metadata(actor)
        context = await store.TaskMembership.context_for_thread(actor.thread_id)
        if context is not None:
            context = await authorized_context(actor, coordinator=True)
        if not await metadata.delegation_supported():
            raise ValueError("Delegation needs the machine serving this thread to be connected")
        if metadata.sandbox_host_thread_id:
            raise PermissionError(
                "Sandbox guests must ask their coordinator for additional workers"
            )
        if not metadata.sandbox_id:
            raise ValueError("The coordinator needs an attached sandbox before delegation")
        workspace_value = metadata.workspace or metadata.environment
        workspace = await resolve_workspace(
            thread_workspace=(
                context.task.workspace.slug if context is not None else workspace_value
            ),
            login=actor.login,
        )
        chosen_model, chosen_effort = await model_choice(
            workspace.slug, metadata.json_metadata(), model, effort
        )
        return cls(actor, metadata, workspace.slug, chosen_model, chosen_effort)

    async def reserve(
        self, worker_thread_id: str, instructions: str, request_id: str
    ) -> tuple[store.Task, store.TaskDelegation]:
        title = self.metadata.title
        return await store.Task.reserve_worker(
            self.actor.thread_id,
            worker_thread_id,
            title=title.strip() if title and title.strip() else "Delegated work",
            workspace=self.workspace,
            instructions=instructions,
            model=self.model,
            effort=self.effort,
            launch_request_id=request_id,
        )

    async def launched_by(self, request_id: str) -> tuple[store.Task, store.TaskDelegation] | None:
        """The async worker this coordinator's tool call ``request_id`` already launched."""
        delegation = await store.TaskDelegation.launched(self.actor.thread_id, request_id)
        context = await store.TaskMembership.context_for_thread(self.actor.thread_id)
        if delegation is None or context is None:
            return None
        return context.task, delegation


async def authorize_async_worker(
    actor: Actor, request_id: str, instructions: str
) -> dict[str, object] | None:
    """Check the coordinator may launch an async worker; the worker this launch already made, if any."""
    if not request_id or not instructions.strip():
        raise ValueError("Assignment instructions and a stable tool-call identity are required")
    launched = await (await Delegation.authorize(actor)).launched_by(request_id)
    return worker_reply(*launched) if launched is not None else None


async def register_async_worker(
    actor: Actor, request_id: str, worker_thread_id: str, instructions: str
) -> dict[str, object]:
    """Make the async subagent a coordinator's tool call launched a task worker.

    MDA wrote the subagent thread's parent when it launched it, so only a thread
    launched from the coordinator's own thread qualifies. One launch, one worker:
    a replay that started a second thread raises ``WorkerAlreadyLaunchedError``
    naming the first.
    """
    if not request_id or not instructions.strip():
        raise ValueError("Assignment instructions and a stable tool-call identity are required")
    child = await RUNTIMES.mda.get(worker_thread_id)
    launched = LaunchedThread.model_validate((child or {}).get("metadata") or {})
    if (
        launched.parent_thread_id != actor.thread_id
        or launched.subagent_name != ASYNC_WORKER_SUBAGENT
    ):
        raise PermissionError("The thread is not an async worker this coordinator launched")
    delegation = await Delegation.authorize(actor)
    task, reserved = await delegation.reserve(worker_thread_id, instructions.strip(), request_id)
    await index_worker(
        task, reserved, delegation.metadata, {MDA_GRAPH_METADATA_KEY: MDA_WORKER_GRAPH_ID}
    )
    # MDA started its run, not the thread router, which marks the threads it runs busy.
    await refresh_thread_status(worker_thread_id)
    return worker_reply(task, reserved)


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
        "thread_url": dashboard_thread_url(delegation.worker_thread_id),
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
    context = await authorized_context(actor, allow_participants=True)
    access = None
    if context.membership.role == "coordinator":
        access = WorkerAccess(
            await _status_owner_actor(actor), context, await thread_metadata(actor.thread_id)
        )
    workers = []
    for delegation in await store.TaskDelegation.for_task(context.task.id):
        try:
            if access is not None:
                await access.worker(delegation.worker_thread_id)
            else:
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
    sender_label = await presentation.sender_label(actor.thread_id)
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
            sender_label=sender_label,
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


async def live_runs(thread_id: str) -> list[str]:
    """The thread's running runs, then its pending ones."""
    client = langgraph_client()
    run_ids: list[str] = []
    statuses: tuple[RunStatus, ...] = ("running", "pending")
    for status in statuses:
        offset = 0
        while True:
            runs = await client.runs.list(thread_id, status=status, limit=100, offset=offset)
            run_ids.extend(run["run_id"] for run in runs)
            if len(runs) < 100:
                break
            offset += 100
    return run_ids


async def cancel_worker(worker_thread_id: str, *, silently: bool = False) -> None:
    """Stop a worker for good: its live runs, the messages owed to it, and its wake-ups.

    A worker cancelled ``silently`` also owes its coordinator nothing: what it has yet to
    deliver is withdrawn, and its later reports, the interruption included, are dropped.
    """
    async with postgres.session() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"event_match:{worker_thread_id}"},
        )
        coordinator_thread_id = await session.scalar(
            update(store.TaskDelegation)
            .where(store.TaskDelegation.worker_thread_id == worker_thread_id)
            .values(cancelled=True, **({"silenced": True} if silently else {}))
            .returning(store.TaskDelegation.coordinator_thread_id)
        )
        await EventSubscription.forget(worker_thread_id, session=session)
        try:
            run_ids = await live_runs(worker_thread_id)
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
    if silently and coordinator_thread_id is not None:
        await TaskMessage.withdraw(coordinator_thread_id, sender_thread_id=worker_thread_id)
    await interrupt_transcript_turns(worker_thread_id, run_ids)
    await cancel_thread_wakeups(worker_thread_id)


async def control_worker(
    actor: Annotated[Actor | dict[str, object], InjectedState],
    worker_thread_id: str,
    action: Literal["status", "cancel"],
) -> dict[str, object]:
    actor = await Actor.resolve(actor)
    task, delegation = await owned_worker(
        actor, worker_thread_id, allow_participants=action == "status"
    )
    if action == "cancel":
        await cancel_worker(worker_thread_id)
        delegation.cancelled = True
        return {**await worker_status(delegation), "cancellation_requested": True}
    return await worker_status(delegation)
