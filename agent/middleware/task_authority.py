"""Apply persisted task authority without changing the tool or shared prompt prefix."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.tasks import authority, role
from agent.tools.task_threads import current_thread

_COORDINATION_TOOLS = frozenset(
    {
        "configure_task",
        "task_status",
        "spawn_worker",
        "message_task_thread",
        "control_worker",
        "get_thread",
        "list_threads",
        "read_file",
        "ls",
        "glob",
        "grep",
        "web_search",
        "fetch_url",
        "slack_reply",
        "slack_no_reply_needed",
        "slack_read_thread_messages",
        "slack_read_channel_messages",
        "write_todos",
        "read_todos",
        "load_integration_tools",
        "search_pull_requests",
    }
)
_ALTERNATE_SPAWN = frozenset(
    {
        "task",
        "start_thread",
        "request_pr_review",
        "slack_start_new_thread",
        "create_automation",
        "update_automation",
        "trigger_automation",
        "auto_assign_human_reviewer",
    }
)


async def reject_tool(thread_id: str, name: str) -> str | None:
    current = await role(thread_id)
    if name == "task":
        return "Synchronous delegation is disabled; use ordinary worker threads"
    if current is None:
        return None
    if name in _ALTERNATE_SPAWN or any(
        token in name.casefold()
        for token in ("spawn_session", "spawn-session", "investigation_start", "analysis_start")
    ):
        return "Delegate through spawn_worker so task membership and depth are enforced"
    if current.role == "worker" and name in {
        "spawn_worker",
        "configure_task",
        "control_worker",
        "manage_thread",
    }:
        return "Workers request help through message_task_thread"
    if current.role == "coordinator" and current.delegated and name not in _COORDINATION_TOOLS:
        return "After delegation, all implementation and tests must be performed by workers"
    return None


class TaskAuthorityMiddleware(OpenSWEMiddleware):
    def __init__(self, *, client_tool_names: frozenset[str] = frozenset()) -> None:
        self.client_tool_names = client_tool_names

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        thread_id = current_thread()
        name = request.tool_call["name"]
        async with authority(
            thread_id, tool_call=True, exclusive=name in {"spawn_worker", "configure_task"}
        ):
            error = await reject_tool(thread_id, name)
            current = await role(thread_id)
            if name in self.client_tool_names and current is not None:
                error = "Task members must use server-authorized tools, not client replacements"
            if error:
                return ToolMessage(
                    content=error, tool_call_id=request.tool_call["id"], status="error"
                )
            return await handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        current = await role(current_thread())
        message = prompt(
            "tasks/role",
            role=current.role if current else "coordinator",
            delegated=current.delegated if current else False,
            coordinator_id=current.coordinator_id if current else current_thread(),
            criteria=current.criteria if current else [],
            permitted=", ".join(sorted(_COORDINATION_TOOLS)),
        )
        return await handler(
            request.override(messages=[SystemMessage(content=message), *request.messages])
        )
