from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from agent.tasks import store

TaskRole = Literal["unregistered", "coordinator", "worker"]

READ_TOOLS = frozenset(
    {
        "get_task",
        "get_thread",
        "list_threads",
        "read_file",
        "ls",
        "glob",
        "grep",
        "fetch_url",
        "web_search",
        "read_only_sql",
        "read_user_settings",
        "search_pull_requests",
        "list_workspaces",
        "list_automations",
        "list_event_types",
        "slack_read_thread_messages",
        "slack_read_channel_messages",
        "slack_list_channels",
        "slack_list_channel_members",
    }
)
COORDINATOR_TOOLS = frozenset(
    {"set_task", "spawn_worker", "message_worker", "cancel_worker", "complete_task"}
)
SPAWN_TOOLS = frozenset(
    {
        "spawn_worker",
        "start_thread",
        "slack_start_new_thread",
        "request_pr_review",
        "create_automation",
    }
)
COORDINATION_TOOLS = (
    READ_TOOLS
    | COORDINATOR_TOOLS
    | frozenset(
        {
            "slack_reply",
            "slack_no_reply_needed",
            "write_todos",
            "compact_conversation",
            "cli_result",
        }
    )
)
WORKER_TOOLS = READ_TOOLS | frozenset(
    {
        "execute",
        "write_file",
        "edit_file",
        "delete",
        "background_task",
        "write_todos",
        "compact_conversation",
        "report_worker_progress",
        "open_pull_request",
        "link_pull_request",
        "create_sandbox_file_download_url",
    }
)
TASK_MUTATION_TOOLS = COORDINATOR_TOOLS | frozenset({"report_worker_progress"})


class TaskPermissionError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    role: TaskRole
    task_id: str | None = None
    coordinator_thread_id: str | None = None
    delegated: bool = False
    title: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    status: str = ""


async def policy_for_thread(thread_id: str) -> TaskPolicy:
    async with store.thread_lock(thread_id, shared=True):
        task = await store.task_for_thread(thread_id)
        membership = await store.membership_for_thread(thread_id)
        if task is None and membership is None:
            return TaskPolicy(role="unregistered")
        if task is None or membership is None or str(task.id) != str(membership.task_id):
            raise TaskPermissionError("Task membership is inconsistent; execution is disabled.")
        role: TaskRole = "coordinator" if task.coordinator_thread_id == thread_id else "worker"
        if membership.role != role or membership.thread_id != thread_id:
            raise TaskPermissionError("Task role is inconsistent; execution is disabled.")
        return TaskPolicy(
            role=role,
            task_id=str(task.id),
            coordinator_thread_id=task.coordinator_thread_id,
            delegated=task.delegated,
            title=task.title,
            acceptance_criteria=tuple(task.acceptance_criteria),
            status=task.status,
        )


def tool_denial(
    policy: TaskPolicy,
    tool_name: str,
    arguments: Mapping[str, object],
    *,
    client_tool: bool = False,
    sandbox_capability: bool = False,
) -> str | None:
    if tool_name == "task":
        return "Synchronous subagents are disabled. Delegate with spawn_worker instead."
    if sandbox_capability and tool_name in TASK_MUTATION_TOOLS | SPAWN_TOOLS:
        return "Shared sandbox credentials cannot delegate or change tasks."
    restricted = policy.role == "worker" or policy.delegated or sandbox_capability
    if client_tool and restricted:
        return "Client-defined tools cannot execute with this task role or sandbox credential."
    read_only = tool_name in READ_TOOLS or (
        tool_name == "background_task" and arguments.get("action") in ("status", "list")
    )
    if sandbox_capability and policy.role != "unregistered":
        if not read_only:
            return "Shared sandbox credentials permit only read-only task operations."
        return None
    if policy.role == "worker" and tool_name not in WORKER_TOOLS:
        return "Workers may use only the permitted implementation and read tools; request additional tools or help from the coordinator."
    if tool_name == "report_worker_progress" and policy.role != "worker":
        return "Only a task worker can report progress to its coordinator."
    if policy.role == "coordinator" and tool_name in SPAWN_TOOLS - {"spawn_worker"}:
        return "Task delegation must use spawn_worker so membership and results are tracked."
    if policy.role == "coordinator" and policy.delegated:
        if not read_only and tool_name not in COORDINATION_TOOLS:
            return "This coordinator has delegated permanently. Delegate implementation, integration, conflict resolution, and tests to workers."
    return None


@asynccontextmanager
async def authorize_tool(
    thread_id: str,
    tool_name: str,
    arguments: Mapping[str, object],
    *,
    client_tool: bool = False,
    sandbox_capability: bool = False,
) -> AsyncIterator[None]:
    if tool_name == "task":
        raise TaskPermissionError(
            "Synchronous subagents are disabled. Delegate with spawn_worker instead."
        )
    if not thread_id:
        raise TaskPermissionError("A persisted thread identity is required to execute tools.")
    async with store.thread_lock(thread_id, shared=tool_name not in TASK_MUTATION_TOOLS):
        policy = await policy_for_thread(thread_id)
        denied = tool_denial(
            policy,
            tool_name,
            arguments,
            client_tool=client_tool,
            sandbox_capability=sandbox_capability,
        )
        if denied is not None:
            raise TaskPermissionError(denied)
        yield
