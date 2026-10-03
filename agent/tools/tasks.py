from langchain.agents import AgentState
from langchain.tools import ToolRuntime

from agent.run_config import RunConfig
from agent.tasks import service, store


def _actor(runtime: ToolRuntime[None, AgentState]) -> service.Actor:
    cfg = RunConfig.from_config(runtime.config)
    if not cfg.thread_id or not cfg.github_login:
        raise PermissionError("A verified thread and triggering user are required")
    return service.Actor(cfg.thread_id, cfg.github_login, cfg.user_email)


def _task(task: store.TaskRecord) -> dict[str, object]:
    return {
        "id": task.id,
        "coordinator_thread_id": task.coordinator_thread_id,
        "workspace": task.workspace,
        "title": task.title,
        "acceptance_criteria": task.acceptance_criteria,
        "delegated": task.delegated,
        "status": task.status,
        "completion_evidence": task.completion_evidence,
    }


def _worker(delegation: store.Delegation) -> dict[str, object]:
    return {
        "worker_thread_id": delegation.worker_thread_id,
        "instructions": delegation.instructions,
        "model": delegation.model,
        "effort": delegation.effort,
        "status": delegation.status,
        "run_id": delegation.run_id,
    }


async def set_task(
    title: str, acceptance_criteria: list[str], runtime: ToolRuntime[None, AgentState]
) -> dict[str, object]:
    return _task(
        await service.set_task(
            _actor(runtime), title=title, acceptance_criteria=acceptance_criteria
        )
    )


async def get_task(runtime: ToolRuntime[None, AgentState]) -> dict[str, object]:
    actor = _actor(runtime)
    await service.authorized_metadata(actor)
    task = await store.task_for_thread(actor.thread_id)
    if task is None:
        return {"task": None, "workers": []}
    return {
        "task": _task(task),
        "workers": [_worker(item) for item in await store.list_delegations(actor.thread_id)],
    }


async def spawn_worker(
    instructions: str,
    runtime: ToolRuntime[None, AgentState],
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, object]:
    return _worker(
        await service.spawn_worker(
            _actor(runtime),
            instructions=instructions,
            model=model,
            effort=effort,
            request_id=runtime.tool_call_id or "",
        )
    )


async def message_worker(
    worker_thread_id: str, message: str, runtime: ToolRuntime[None, AgentState]
) -> dict[str, object]:
    run_id = await service.message_worker(
        _actor(runtime),
        worker_thread_id=worker_thread_id,
        message=message,
        request_id=runtime.tool_call_id or "",
    )
    return {"worker_thread_id": worker_thread_id, "run_id": run_id, "status": "queued"}


async def cancel_worker(
    worker_thread_id: str, runtime: ToolRuntime[None, AgentState]
) -> dict[str, object]:
    await service.cancel_worker(_actor(runtime), worker_thread_id=worker_thread_id)
    return {"worker_thread_id": worker_thread_id, "status": "cancellation_requested"}


async def report_worker_progress(
    message: str, runtime: ToolRuntime[None, AgentState], request_help: bool = False
) -> dict[str, object]:
    event = await service.report_worker_progress(
        _actor(runtime),
        message=message,
        request_help=request_help,
        request_id=runtime.tool_call_id or "",
    )
    return {"event_id": event.id, "status": "recorded"}


async def complete_task(
    evidence: list[str], runtime: ToolRuntime[None, AgentState]
) -> dict[str, object]:
    return _task(await service.complete_task(_actor(runtime), evidence=evidence))
