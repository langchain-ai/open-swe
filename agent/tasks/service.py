import logging
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import RunStatus
from sqlalchemy import text

from agent.dashboard.oauth import enforce_github_login_gate
from agent.dashboard.options import available_requested_models, model_supports_effort
from agent.dashboard.workspace_settings import get_workspace_settings
from agent.database import postgres
from agent.dispatch import create_durable_run
from agent.input_messages import RunInput, build_run_input
from agent.invocation import resolve_invocation_id
from agent.prompts import prompt
from agent.sandboxes.tool_access import (
    SANDBOX_HOST_THREAD_KEY,
    SANDBOX_PROXY_CONFIG_METADATA_KEY,
)
from agent.source_context import SourceContext
from agent.tasks import store
from agent.threads.creation import create_thread
from agent.threads.summary import (
    assert_thread_readable,
    repo_config_from_metadata,
    thread_is_promptable,
)
from agent.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.workspaces.routing import resolve_workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Actor:
    thread_id: str
    login: str
    email: str | None = None


async def authorized_metadata(actor: Actor) -> dict[str, object]:
    if not actor.thread_id or not actor.login:
        raise PermissionError("A verified thread and triggering user are required")
    await enforce_github_login_gate(actor.login)
    metadata = thread_metadata(await langgraph_client().threads.get(actor.thread_id))
    assert_thread_readable(metadata, actor.login, actor.email)
    if not thread_is_promptable(metadata, actor.login):
        raise PermissionError("The triggering user cannot act on this thread")
    return dict(metadata)


async def set_task(actor: Actor, *, title: str, acceptance_criteria: list[str]) -> store.TaskRecord:
    async with store.thread_lock(actor.thread_id):
        metadata = await authorized_metadata(actor)
        existing = await store.task_for_thread(actor.thread_id)
        if existing is not None:
            return await store.update_task(
                actor.thread_id, title=title, acceptance_criteria=acceptance_criteria
            )
        workspace_value = metadata.get("workspace") or metadata.get("environment")
        workspace = await resolve_workspace(
            tag=workspace_value if isinstance(workspace_value, str) else None,
            login=actor.login,
        )
        return await store.ensure_task(
            actor.thread_id,
            workspace=workspace.slug,
            title=title,
            acceptance_criteria=acceptance_criteria,
        )


def require_execution_owner(actor: Actor, metadata: Mapping[str, object]) -> None:
    owner = metadata.get("owner_login") or metadata.get("github_login")
    if not isinstance(owner, str) or owner.casefold() != actor.login.casefold():
        raise PermissionError("Only the task thread's owner can launch work using its credentials")


async def coordinator_task(thread_id: str) -> store.TaskRecord:
    task = await store.task_for_thread(thread_id)
    if task is None or task.coordinator_thread_id != thread_id:
        raise PermissionError("Only the permanent coordinator can control task workers")
    if task.status != "active":
        raise ValueError("This task is complete")
    return task


async def owned_worker(coordinator_thread_id: str, worker_thread_id: str) -> store.Delegation:
    task = await coordinator_task(coordinator_thread_id)
    delegation = await store.delegation_for_worker(worker_thread_id)
    if delegation is None or delegation.task_id != task.id:
        raise PermissionError("This worker does not belong to the coordinator's task")
    if delegation.coordinator_thread_id != coordinator_thread_id:
        raise PermissionError("Only the worker's coordinator can control it")
    return delegation


async def model_choice(
    task: store.TaskRecord, metadata: Mapping[str, object], model: str | None, effort: str | None
) -> tuple[str, str]:
    settings = await get_workspace_settings(task.workspace)
    choices = available_requested_models(fable_enabled=settings.fable_enabled)
    default_model, default_effort = settings.default_model("agent")
    inherited_model = metadata.get("resolved_model")
    inherited_effort = metadata.get("resolved_effort")
    selected_model = model or (
        inherited_model
        if isinstance(inherited_model, str) and inherited_model in choices
        else default_model
    )
    option = choices.get(selected_model)
    if option is None:
        raise ValueError("The requested model is not available in this workspace")
    selected_effort = effort or (
        inherited_effort
        if model is None and isinstance(inherited_effort, str)
        else default_effort
        if selected_model == default_model
        else option["default_effort"]
    )
    if not model_supports_effort(selected_model, selected_effort):
        raise ValueError("The requested effort is not supported by the selected model")
    return selected_model, selected_effort


def run_config(thread_id: str, metadata: Mapping[str, object]) -> dict[str, object]:
    login = metadata.get("owner_login") or metadata.get("github_login")
    if not isinstance(login, str) or not login:
        raise PermissionError("The task thread has no persisted owner")
    configurable: dict[str, object] = {
        "thread_id": thread_id,
        "github_login": login,
        "source": metadata.get("source") or "dashboard",
        "background_task_completion": True,
    }
    configurable.update(SourceContext.from_metadata(metadata).dump())
    for key in ("workspace", "repo_explicitly_none", "admin_thread"):
        if key in metadata:
            configurable[key] = metadata[key]
    repo = repo_config_from_metadata(metadata)
    if repo:
        configurable["repo"] = repo
    if isinstance(metadata.get("resolved_model"), str):
        configurable["agent_model_id"] = metadata["resolved_model"]
        configurable["agent_effort"] = metadata.get("resolved_effort")
        configurable["model_selection"] = "explicit"
    return configurable


def system_input(content: str, message_id: str) -> RunInput:
    value = build_run_input(
        content,
        {"sender_id": "system:task-coordinator", "surface": "automation", "kind": "system"},
    )
    value["messages"][-1]["id"] = message_id
    return value


def run_invocation(run: Mapping[str, object]) -> str | None:
    metadata = run.get("metadata")
    config = run.get("config")
    configurable = config.get("configurable") if isinstance(config, Mapping) else None
    return resolve_invocation_id(
        configurable if isinstance(configurable, Mapping) else None,
        metadata if isinstance(metadata, Mapping) else None,
    )


async def is_duplicate_task_dispatch(thread_id: str, run: Mapping[str, object]) -> bool:
    metadata = run.get("metadata")
    dispatch_key = metadata.get("task_dispatch_key") if isinstance(metadata, Mapping) else None
    if not isinstance(dispatch_key, str):
        return False
    winner = await store.task_dispatch_invocation(thread_id, dispatch_key)
    return winner is not None and run_invocation(run) != winner


async def dispatched_run(client: LangGraphClient, thread_id: str, dispatch_key: str) -> str | None:
    winner = await store.task_dispatch_invocation(thread_id, dispatch_key)
    candidate: str | None = None
    offset = 0
    while True:
        runs = await client.runs.list(thread_id, limit=100, offset=offset)
        for run in runs:
            if (run.get("metadata") or {}).get("task_dispatch_key") == dispatch_key:
                if winner is not None and run_invocation(run) == winner:
                    return run["run_id"]
                candidate = run["run_id"]
        if len(runs) < 100:
            if winner is not None:
                raise RuntimeError("The task dispatch's claimed invocation is not available yet")
            return candidate
        offset += 100


async def register_dispatch_run(
    client: LangGraphClient, worker_thread_id: str, dispatch_key: str, run_id: str
) -> None:
    winner = await store.task_dispatch_invocation(worker_thread_id, dispatch_key)
    if winner is None:
        await store.register_worker_dispatch(worker_thread_id, dispatch_key, run_id)
        return
    run = await client.runs.get(worker_thread_id, run_id)
    if run_invocation(run) != winner:
        raise ValueError("Only the claimed invocation can register this task dispatch")
    async with postgres.transaction() as conn:
        previous = (
            await conn.execute(
                text("""
                    SELECT run_id FROM task_worker_dispatch
                    WHERE worker_thread_id = :worker AND dispatch_key = :key FOR UPDATE
                """),
                {"worker": worker_thread_id, "key": dispatch_key},
            )
        ).first()
        if previous is None:
            raise ValueError("The run does not match this worker's persisted dispatch")
        await conn.execute(
            text("""
                UPDATE task_worker_dispatch
                SET run_id = :run_id,
                    settled = CASE WHEN run_id IS DISTINCT FROM :run_id THEN false ELSE settled END
                WHERE worker_thread_id = :worker AND dispatch_key = :key
            """),
            {"worker": worker_thread_id, "key": dispatch_key, "run_id": run_id},
        )
        if previous[0] is not None and previous[0] != run_id:
            await conn.execute(
                text("""
                    UPDATE task_delegation SET run_id = :run_id, status = 'running'
                    WHERE worker_thread_id = :worker AND run_id = :previous
                """),
                {"worker": worker_thread_id, "run_id": run_id, "previous": previous[0]},
            )


async def dispatch_worker(
    delegation: store.Delegation, *, message: str | None = None, dispatch_key: str | None = None
) -> str:
    async with store.thread_lock(delegation.coordinator_thread_id):
        await owned_worker(delegation.coordinator_thread_id, delegation.worker_thread_id)
        client = langgraph_client()
        key = dispatch_key or f"task-delegation:{delegation.id}"
        content = message or prompt("tasks/worker-start", instructions=delegation.instructions)
        intent = await store.record_worker_dispatch(
            delegation.worker_thread_id, dispatch_key=key, content=content
        )
        if intent.settled and intent.run_id is None:
            raise ValueError("This worker dispatch was cancelled before launch")
        existing_run = (
            await dispatched_run(client, delegation.worker_thread_id, key) or intent.run_id
        )
        if existing_run is not None:
            await register_dispatch_run(client, delegation.worker_thread_id, key, existing_run)
            latest = await client.runs.list(delegation.worker_thread_id, limit=1)
            if delegation.run_id is None or (latest and latest[0]["run_id"] == existing_run):
                await store.set_delegation_run(delegation.worker_thread_id, existing_run)
            return existing_run
        metadata = thread_metadata(await client.threads.get(delegation.worker_thread_id))
        run = await create_durable_run(
            delegation.worker_thread_id,
            "agent",
            input=system_input(content, key),
            config={"configurable": run_config(delegation.worker_thread_id, metadata)},
            source="task_worker",
            thread_title=None,
            metadata={"task_dispatch_key": key, "task_id": delegation.task_id},
            client=client,
            multitask_strategy="enqueue",
            task_worker_dispatch=True,
        )
        run_id = await dispatched_run(client, delegation.worker_thread_id, key) or run["run_id"]
        await register_dispatch_run(client, delegation.worker_thread_id, key, run_id)
        await store.set_delegation_run(delegation.worker_thread_id, run_id)
        return run_id


async def ensure_worker_thread(
    delegation: store.Delegation, metadata: Mapping[str, object]
) -> None:
    client = langgraph_client()
    task = await coordinator_task(delegation.coordinator_thread_id)
    owner = metadata.get("owner_login") or metadata.get("github_login")
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(owner, str) or not owner:
        raise PermissionError("Delegation requires a persisted coordinator owner")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise ValueError("The coordinator needs an attached sandbox before delegation")
    worker_metadata: dict[str, object] = {
        "source": "dashboard",
        "origin": "task_worker",
        "owner_type": "user",
        "owner_login": owner,
        "visibility": metadata.get("visibility", "public"),
        "workspace": task.workspace,
        "thread_category": "interactive",
        "trigger_kind": "task_delegation",
        "sandbox_id": sandbox_id,
        SANDBOX_HOST_THREAD_KEY: delegation.coordinator_thread_id,
        "task_id": task.id,
        "model": delegation.model,
        "effort": delegation.effort,
        "resolved_model": delegation.model,
        "resolved_effort": delegation.effort,
        "model_selection": "explicit",
    }
    for key in (
        "repo_owner",
        "repo_name",
        "repo_explicitly_none",
        "base_branch",
        "branch_prefix",
        "admin_thread",
        SANDBOX_PROXY_CONFIG_METADATA_KEY,
    ):
        if key in metadata:
            worker_metadata[key] = metadata[key]
    repo = repo_config_from_metadata(metadata)
    if repo:
        worker_metadata["repo_owner"] = repo["owner"]
        worker_metadata["repo_name"] = repo["name"]
    await create_thread(
        client,
        delegation.worker_thread_id,
        title=delegation.instructions[:80],
        metadata=worker_metadata,
        if_exists="do_nothing",
    )
    persisted = thread_metadata(await client.threads.get(delegation.worker_thread_id))
    if (
        persisted.get("owner_login") != owner
        or persisted.get("workspace") != task.workspace
        or persisted.get(SANDBOX_HOST_THREAD_KEY) != delegation.coordinator_thread_id
    ):
        raise PermissionError("The worker identity conflicts with the persisted delegation")


async def spawn_worker(
    actor: Actor, *, instructions: str, model: str | None, effort: str | None, request_id: str
) -> store.Delegation:
    if not request_id or not instructions.strip():
        raise ValueError("Delegation requires instructions and a stable tool call identity")
    worker_thread_id = str(uuid5(NAMESPACE_URL, f"open-swe:{actor.thread_id}:{request_id}"))
    async with store.thread_lock(actor.thread_id):
        metadata = await authorized_metadata(actor)
        require_execution_owner(actor, metadata)
        task = await coordinator_task(actor.thread_id)
        existing = await store.delegation_for_worker(worker_thread_id)
        if existing is not None:
            await owned_worker(actor.thread_id, worker_thread_id)
            if existing.status != "pending":
                return existing
            delegation = existing
        else:
            if metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
                raise ValueError("Finish or explicitly stop background commands before delegating")
            if not metadata.get("sandbox_id"):
                raise ValueError("Attach a sandbox before delegating")
            if not metadata.get("owner_login") and not metadata.get("github_login"):
                raise PermissionError("Delegation requires a persisted coordinator owner")
            chosen_model, chosen_effort = await model_choice(task, metadata, model, effort)
            delegation = await store.create_delegation(
                actor.thread_id,
                worker_thread_id=worker_thread_id,
                instructions=instructions,
                model=chosen_model,
                effort=chosen_effort,
            )
        await ensure_worker_thread(delegation, metadata)
        await dispatch_worker(delegation)
        return await owned_worker(actor.thread_id, worker_thread_id)


async def message_worker(
    actor: Actor, *, worker_thread_id: str, message: str, request_id: str
) -> str:
    if not message.strip() or not request_id:
        raise ValueError("A message and a stable tool call identity are required")
    async with store.thread_lock(actor.thread_id):
        metadata = await authorized_metadata(actor)
        require_execution_owner(actor, metadata)
        delegation = await owned_worker(actor.thread_id, worker_thread_id)
        return await dispatch_worker(
            delegation,
            message=message,
            dispatch_key=f"task-message:{actor.thread_id}:{request_id}",
        )


async def live_worker_runs(client: LangGraphClient, worker_thread_id: str) -> list[str]:
    result: list[str] = []
    statuses: tuple[RunStatus, ...] = ("pending", "running")
    for status in statuses:
        offset = 0
        while True:
            try:
                runs = await client.runs.list(
                    worker_thread_id, status=status, limit=100, offset=offset
                )
            except NotFoundError:
                logger.warning(
                    "Worker thread is not present on the run server",
                    extra={"worker_thread_id": worker_thread_id},
                )
                return []
            result.extend(run["run_id"] for run in runs)
            if len(runs) < 100:
                break
            offset += 100
    return result


async def relay_events(coordinator_thread_id: str) -> None:
    from agent.tasks.delivery import deliver_events

    try:
        await deliver_events(coordinator_thread_id)
    except Exception:
        logger.exception(
            "Worker event delivery deferred to reconciliation",
            extra={"coordinator_thread_id": coordinator_thread_id},
        )


async def cancel_worker(actor: Actor, *, worker_thread_id: str) -> None:
    async with store.thread_lock(actor.thread_id):
        await authorized_metadata(actor)
        delegation = await owned_worker(actor.thread_id, worker_thread_id)
        client = langgraph_client()
        for intent in await store.pending_worker_dispatches():
            if intent.worker_thread_id == worker_thread_id and intent.run_id is None:
                accepted = await dispatched_run(client, worker_thread_id, intent.dispatch_key)
                if accepted is not None:
                    await store.register_worker_dispatch(
                        worker_thread_id, intent.dispatch_key, accepted
                    )
        await store.cancel_pending_dispatches(worker_thread_id)
        live = await live_worker_runs(client, worker_thread_id)
        if live:
            await client.runs.cancel_many(
                thread_id=worker_thread_id, run_ids=live, action="interrupt"
            )
        else:
            await store.finish_delegation(worker_thread_id, status="cancelled")
        await store.record_event(
            worker_thread_id,
            event_key=f"cancel:{delegation.run_id or delegation.id}",
            kind="cancellation_requested" if live else "cancelled",
            content="The coordinator explicitly requested cancellation of this worker.",
        )
    await relay_events(actor.thread_id)


async def report_worker_progress(
    actor: Actor, *, message: str, request_help: bool, request_id: str
) -> store.TaskEvent:
    await authorized_metadata(actor)
    delegation = await store.delegation_for_worker(actor.thread_id)
    if delegation is None:
        raise PermissionError("Only a task worker can report progress")
    if not message.strip() or not request_id:
        raise ValueError("A progress message and a stable tool call identity are required")
    event = await store.record_event(
        actor.thread_id,
        event_key=f"progress:{request_id}",
        kind="help" if request_help else "progress",
        content=message,
    )
    await relay_events(delegation.coordinator_thread_id)
    return event


async def complete_task(actor: Actor, *, evidence: list[str]) -> store.TaskRecord:
    async with store.thread_lock(actor.thread_id):
        await authorized_metadata(actor)
        await coordinator_task(actor.thread_id)
        for delegation in await store.list_delegations(actor.thread_id):
            if await live_worker_runs(langgraph_client(), delegation.worker_thread_id):
                raise ValueError("Cannot complete a task while worker invocations are active")
        return await store.complete_task(actor.thread_id, evidence=evidence)
