import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from agent import completion
from agent.dashboard import workspace_mcps
from agent.github import token as auth
from agent.linear import notifications
from agent.mcp import MCPConnectionUpdate, runtime
from agent.middleware import sandbox_circuit_breaker


@dataclass
class LinearMCP:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    is_error: bool = False

    async def initialize(self) -> None:
        pass

    async def call_tool(
        self, name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> CallToolResult:
        self.calls.append((name, arguments))
        return CallToolResult(
            isError=self.is_error,
            content=[TextContent(type="text", text="rejected" if self.is_error else "created")],
        )


@pytest.fixture
def comment_tool() -> str:
    return "save_comment"


@pytest.fixture
async def linear_mcp(fake_store, monkeypatch: pytest.MonkeyPatch, comment_tool: str) -> LinearMCP:
    await workspace_mcps.save_workspace_mcp(
        "linear",
        MCPConnectionUpdate(
            name="linear", url="https://mcp.linear.app/mcp", allowed_tools=[comment_tool]
        ),
    )
    definition = Tool(
        name=comment_tool,
        inputSchema={
            "type": "object",
            "properties": {"issueId": {"type": "string"}, "body": {"type": "string"}},
            "required": ["issueId", "body"],
        },
    )
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[definition]))
    remote = LinearMCP()

    @asynccontextmanager
    async def session(connection, **kwargs):
        assert connection["url"] == "https://mcp.linear.app/mcp"
        yield remote

    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)
    return remote


@pytest.mark.parametrize("comment_tool", ["save_comment", "create_comment"])
async def test_failed_linear_run_posts_through_mcp(
    linear_mcp: LinearMCP, comment_tool: str
) -> None:
    posted = await completion._post_failure_reply(
        "thread-1",
        {"source": "linear", "source_context": {"linear_issue": {"id": "issue-1"}}},
        "timeout",
    )

    assert posted is True
    assert len(linear_mcp.calls) == 1
    name, arguments = linear_mcp.calls[0]
    assert name == comment_tool
    assert arguments["issueId"] == "issue-1"
    assert "timed out" in arguments["body"]


async def test_linear_auth_failure_posts_token_free_notice(
    linear_mcp: LinearMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"linear_issue": {"id": "issue-1"}}},
    )
    await auth.leave_failure_comment("linear", "Sign in at https://auth.example/private-token")

    assert len(linear_mcp.calls) == 1
    _, arguments = linear_mcp.calls[0]
    assert arguments["issueId"] == "issue-1"
    assert "GitHub" in arguments["body"]
    assert "private-token" not in arguments["body"]


async def test_linear_sandbox_failure_posts_through_mcp(
    linear_mcp: LinearMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sandbox_circuit_breaker, "get_active_slack_thread", AsyncMock(return_value=None)
    )
    await sandbox_circuit_breaker.post_sandbox_unreachable_notification(
        {"configurable": {"linear_issue": {"id": "issue-1"}}}, sandbox_id="sb-dead"
    )

    assert linear_mcp.calls == [
        (
            "save_comment",
            {
                "issueId": "issue-1",
                "body": sandbox_circuit_breaker.sandbox_unreachable_message(sandbox_id="sb-dead"),
            },
        )
    ]


async def test_rejected_mcp_comment_is_not_reported_as_delivered(linear_mcp: LinearMCP) -> None:
    linear_mcp.is_error = True
    posted = await completion._post_failure_reply(
        "thread-1",
        {"source": "linear", "source_context": {"linear_issue": {"id": "issue-1"}}},
        "error",
    )

    assert posted is False
    assert len(linear_mcp.calls) == 1


@pytest.mark.parametrize("change", ["disabled", "unselected", "deleted"])
async def test_unavailable_linear_mcp_does_not_send(linear_mcp: LinearMCP, change: str) -> None:
    if change == "deleted":
        await workspace_mcps.delete_workspace_mcp("linear")
    else:
        await workspace_mcps.save_workspace_mcp(
            "linear",
            MCPConnectionUpdate(
                name="linear",
                url="https://mcp.linear.app/mcp",
                enabled=change != "disabled",
                allowed_tools=[] if change == "unselected" else ["save_comment"],
            ),
        )

    posted = await completion._post_failure_reply(
        "thread-1",
        {"source": "linear", "source_context": {"linear_issue": {"id": "issue-1"}}},
        "error",
    )

    assert posted is False
    assert linear_mcp.calls == []


async def test_notification_only_discovers_the_linear_connection(
    linear_mcp: LinearMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    await workspace_mcps.save_workspace_mcp(
        "other",
        MCPConnectionUpdate(
            name="other", url="https://other.example/mcp", allowed_tools=["save_comment"]
        ),
    )
    discover = runtime._discover_tools
    discovered = []

    async def discover_one(record, namespace):
        discovered.append(record.name)
        return await discover(record, namespace)

    monkeypatch.setattr(runtime, "_discover_tools", discover_one)
    assert await completion._post_failure_reply(
        "thread-1",
        {"source": "linear", "source_context": {"linear_issue": {"id": "issue-1"}}},
        "error",
    )
    assert discovered == ["linear"]
    assert len(linear_mcp.calls) == 1


async def test_completion_only_deduplicates_delivered_notices(
    linear_mcp: LinearMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = {"source": "linear", "source_context": {"linear_issue": {"id": "issue-1"}}}

    async def get_thread(thread_id):
        return {"metadata": metadata}

    async def update_thread(*, thread_id, metadata: dict):
        stored_metadata.update(metadata)

    stored_metadata = metadata
    threads = SimpleNamespace(get=get_thread, update=update_thread)
    monkeypatch.setattr(completion, "langgraph_client", lambda: SimpleNamespace(threads=threads))
    payload = {"thread_id": "thread-1", "run_id": "run-1", "status": "timeout"}

    linear_mcp.is_error = True
    assert await completion.handle_run_completion(payload) == {
        "status": "ignored",
        "reason": "no reply posted",
    }
    assert "failure_reply_posted_run_ids" not in metadata

    linear_mcp.is_error = False
    assert await completion.handle_run_completion(payload) == {
        "status": "ok",
        "reason": "failure reply posted",
    }
    assert metadata["failure_reply_posted_run_ids"] == ["run-1"]
    assert await completion.handle_run_completion(payload) == {
        "status": "ignored",
        "reason": "failure reply already posted for run",
    }
    assert len(linear_mcp.calls) == 2


async def test_notification_rechecks_permission_after_discovery(
    linear_mcp: LinearMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    discover = runtime._discover_tools

    async def revoke_during_discovery(record, namespace):
        definitions = await discover(record, namespace)
        await workspace_mcps.save_workspace_mcp(
            "linear",
            MCPConnectionUpdate(name="linear", url="https://mcp.linear.app/mcp", allowed_tools=[]),
        )
        return definitions

    monkeypatch.setattr(runtime, "_discover_tools", revoke_during_discovery)
    assert not await notifications.post_linear_notification("issue-1", "Run failed")
    assert linear_mcp.calls == []


async def test_notification_times_out_without_retrying(
    linear_mcp: LinearMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    deadline = asyncio.timeout(None)
    monkeypatch.setattr(notifications.asyncio, "timeout", lambda _: deadline)
    calls = []

    async def never_finishes(name, arguments, **kwargs):
        calls.append((name, arguments))
        deadline.reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()

    monkeypatch.setattr(linear_mcp, "call_tool", never_finishes)
    assert not await notifications.post_linear_notification("issue-1", "Run failed")
    assert calls == [("save_comment", {"issueId": "issue-1", "body": "Run failed"})]
