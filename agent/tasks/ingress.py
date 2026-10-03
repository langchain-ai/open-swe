from collections.abc import Mapping

from fastapi import HTTPException

from agent.tasks.policy import TaskPermissionError, policy_for_thread, tool_denial


async def assert_non_task_graph(thread_id: str) -> None:
    policy = await policy_for_thread(thread_id)
    if policy.role != "unregistered":
        raise TaskPermissionError(
            "Task threads must execute the ordinary agent graph with task permissions."
        )


async def assert_user_facing_thread(thread_id: str) -> None:
    policy = await policy_for_thread(thread_id)
    if policy.role == "worker":
        raise TaskPermissionError(
            f"Worker threads accept instructions only from their coordinator. "
            f"Send this request to coordinator thread {policy.coordinator_thread_id}."
        )


async def require_user_facing_thread(thread_id: str) -> None:
    try:
        await assert_user_facing_thread(thread_id)
    except TaskPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


async def require_sandbox_guest_access(host_thread_id: str) -> None:
    policy = await policy_for_thread(host_thread_id)
    if policy.role != "unregistered":
        raise HTTPException(
            403,
            "Task sandboxes cannot start or control Responses threads. "
            f"Use coordinator thread {policy.coordinator_thread_id} to delegate work.",
        )


async def require_sandbox_tool_access(
    thread_id: str, name: str, arguments: Mapping[str, object]
) -> None:
    policy = await policy_for_thread(thread_id)
    denied = tool_denial(policy, name, arguments, sandbox_capability=True)
    if denied is not None:
        raise HTTPException(403, denied)
