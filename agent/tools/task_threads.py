from typing import Annotated, Literal

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.prompts import prompt
from agent.tasks import service
from agent.tools.threads import resolve_thread_actor


async def actor_from_state(state: dict[str, object]) -> service.Actor:
    actor = await resolve_thread_actor(state)
    thread_id = get_config().get("configurable", {}).get("thread_id")
    if actor is None or not isinstance(thread_id, str) or not thread_id:
        raise PermissionError("An authenticated user and thread are required")
    return service.Actor(thread_id=thread_id, login=actor.login, email=actor.email)


async def spawn_worker(
    instructions: str,
    state: Annotated[dict[str, object], InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, object]:
    return await service.spawn_worker(
        await actor_from_state(state),
        instructions=instructions,
        model=model,
        effort=effort,
        request_id=tool_call_id,
    )


async def task_status(state: Annotated[dict[str, object], InjectedState]) -> dict[str, object]:
    return await service.task_status(await actor_from_state(state))


async def message_task_thread(
    message: str,
    state: Annotated[dict[str, object], InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    worker_thread_id: str | None = None,
) -> dict[str, object]:
    return await service.message_task_thread(
        await actor_from_state(state),
        message=message,
        worker_thread_id=worker_thread_id,
        request_id=tool_call_id,
    )


async def control_worker(
    worker_thread_id: str,
    action: Literal["status", "cancel", "retry"],
    state: Annotated[dict[str, object], InjectedState],
) -> dict[str, object]:
    return await service.control_worker(
        await actor_from_state(state),
        worker_thread_id=worker_thread_id,
        action=action,
    )


for _tool in (spawn_worker, task_status, message_task_thread, control_worker):
    _tool.__doc__ = prompt(f"tasks/tool_{_tool.__name__}")
