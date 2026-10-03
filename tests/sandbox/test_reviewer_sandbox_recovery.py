"""The reviewer replaces an unreachable sandbox; the coding agent still fails loudly.

A reviewer sandbox holds only a checkout `prepare_review_repo` re-derives every
run, and reviewer threads (one per PR) outlive their sandbox, so refusing to
replace one bricks reviews on that PR permanently.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig
from langsmith.sandbox import SandboxClientError

from agent.reviewer import PrepareReviewerRunMiddleware, _ensure_reviewer_sandbox_for_thread
from agent.run_config import RunConfig
from agent.sandboxes.lifecycle import SANDBOX_BACKENDS, ensure_sandbox_for_thread
from agent.sandboxes.providers.registry import SandboxGoneError
from agent.sandboxes.state import SandboxUnreachableError, set_sandbox_backend
from agent.tasks.store import TaskRecord


@pytest.mark.asyncio
async def test_replaces_unreachable_sandbox_when_replacement_allowed() -> None:
    thread_id = "thread-reviewer-dead-sandbox"
    SANDBOX_BACKENDS.clear()
    replacement = MagicMock()
    replacement.id = "sandbox-replacement"

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-deleted"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=replacement,
        ) as create_replacement,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(
            thread_id,
            workspace_slug="large",
            allow_replacement=True,
        )

    assert result.id == "sandbox-replacement"
    create_replacement.assert_awaited_once_with(
        thread_id=thread_id,
        github_proxy_repositories=None,
        workspace_slug="large",
        owner_login=None,
    )
    # The stale id is cleared by persisting the replacement, so later runs stop
    # reconnecting to a sandbox that no longer exists.
    assert update_thread.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-replacement"},
    }
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_replaces_unreachable_cached_sandbox_when_replacement_allowed() -> None:
    thread_id = "thread-reviewer-dead-cache"
    SANDBOX_BACKENDS.clear()
    dead = MagicMock()
    dead.id = "sandbox-cached-dead"
    proxy = set_sandbox_backend(thread_id, dead)
    replacement = MagicMock()
    replacement.id = "sandbox-replacement"

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-cached-dead"},
        ),
        patch(
            "agent.sandboxes.lifecycle._refresh_github_proxy",
            new_callable=AsyncMock,
            side_effect=SandboxClientError("sandbox is gone"),
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=replacement,
        ) as create_replacement,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock),
    ):
        result = await ensure_sandbox_for_thread(thread_id, allow_replacement=True)

    create_replacement.assert_awaited_once()
    # Replaced in place, so handles already built around the proxy stay valid.
    assert result is proxy
    assert proxy.current is replacement
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_failed_replacement_still_raises_sandbox_unreachable() -> None:
    thread_id = "thread-reviewer-replacement-fails"
    SANDBOX_BACKENDS.clear()

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-deleted"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            side_effect=RuntimeError("sandbox API outage"),
        ),
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock),
        pytest.raises(SandboxUnreachableError) as excinfo,
    ):
        await ensure_sandbox_for_thread(thread_id, allow_replacement=True)

    # Typed, so the reviewer still recognizes it and notifies on the PR.
    assert excinfo.value.sandbox_id == "sandbox-deleted"
    assert "sandbox API outage" in str(excinfo.value)
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_unreachable_sandbox_still_fails_by_default() -> None:
    thread_id = "thread-agent-dead-sandbox"
    SANDBOX_BACKENDS.clear()

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-deleted"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
        ) as create_replacement,
        patch("agent.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock),
        pytest.raises(SandboxUnreachableError),
    ):
        await ensure_sandbox_for_thread(thread_id)

    create_replacement.assert_not_awaited()
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_reviewer_opts_into_replacement() -> None:
    sandbox_backend = MagicMock()

    with patch(
        "agent.reviewer.ensure_sandbox_for_thread",
        new_callable=AsyncMock,
        return_value=sandbox_backend,
    ) as ensure:
        result, github_token = await _ensure_reviewer_sandbox_for_thread(
            "thread-reviewer",
            RunConfig.parse({"repo": {"owner": "langchain-ai", "name": "open-swe"}}),
        )

    assert result is sandbox_backend
    assert github_token is None
    assert ensure.await_args is not None
    assert ensure.await_args.kwargs["allow_replacement"] is True


@pytest.mark.asyncio
async def test_reviewer_notifies_when_replacement_also_fails() -> None:
    config: RunnableConfig = {
        "configurable": {"repo": {"owner": "langchain-ai", "name": "open-swe"}}
    }
    middleware = PrepareReviewerRunMiddleware(
        thread_id="thread-reviewer", config=config, use_gateway=False
    )

    with (
        patch(
            "agent.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=SandboxUnreachableError("thread-reviewer", "sandbox-deleted", "not found"),
        ),
        patch(
            "agent.reviewer.post_sandbox_unreachable_notification",
            new_callable=AsyncMock,
        ) as notify,
        pytest.raises(SandboxUnreachableError),
    ):
        await middleware._prepare({"messages": []}, MagicMock())

    notify.assert_awaited_once()
    assert notify.await_args is not None
    assert notify.await_args.kwargs == {
        "sandbox_id": "sandbox-deleted",
        "replacement_attempted": True,
    }


@pytest.mark.asyncio
async def test_deleted_sandbox_is_replaced_without_opting_in() -> None:
    thread_id = "thread-agent-gone-sandbox"
    SANDBOX_BACKENDS.clear()
    replacement = MagicMock()
    replacement.id = "sandbox-replacement"
    order: list[str] = []

    with (
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-deleted"},
        ),
        patch(
            "agent.sandboxes.lifecycle.create_sandbox",
            new_callable=AsyncMock,
            side_effect=SandboxGoneError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.sandboxes.lifecycle._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            side_effect=lambda *_a, **_k: (order.append("init"), replacement)[1],
        ) as create_replacement,
        patch(
            "agent.sandboxes.lifecycle.client.threads.update",
            new_callable=AsyncMock,
            side_effect=lambda **_: order.append("bind"),
        ) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id)

    assert result.id == "sandbox-replacement"
    create_replacement.assert_awaited_once()
    assert update_thread.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-replacement"},
    }
    # The thread binds to the sandbox only once it is created and initialized;
    # the identity write is joined inside the creation step.
    assert order == ["init", "bind"]
    SANDBOX_BACKENDS.clear()


@pytest.mark.parametrize("thread_id", ["coordinator", "worker"])
@pytest.mark.parametrize("sandbox_id", [None, "sandbox-deleted"])
async def test_task_shared_sandbox_is_never_replaced(
    thread_id: str, sandbox_id: str | None
) -> None:
    task = TaskRecord(
        id="task-1",
        coordinator_thread_id="coordinator",
        workspace="task-workspace",
        title="Task",
        acceptance_criteria=["Fix the bug"],
        delegated=True,
        status="active",
        completion_evidence=None,
    )
    with (
        patch("agent.sandboxes.lifecycle.task_store.task_for_thread", AsyncMock(return_value=task)),
        patch(
            "agent.sandboxes.lifecycle.get_sandbox_metadata",
            AsyncMock(return_value={"sandbox_id": sandbox_id}),
        ),
        patch(
            "agent.sandboxes.lifecycle._connect_existing_sandbox",
            AsyncMock(side_effect=SandboxGoneError("deleted")),
        ),
        patch("agent.sandboxes.lifecycle._create_sandbox_with_proxy", AsyncMock()) as create,
        pytest.raises(SandboxUnreachableError, match="human remediation"),
    ):
        await ensure_sandbox_for_thread(thread_id, allow_replacement=True)
    create.assert_not_awaited()


async def test_worker_uses_coordinator_sandbox_binding_and_scope() -> None:
    task = TaskRecord(
        id="task-1",
        coordinator_thread_id="coordinator",
        workspace="task-workspace",
        title="Task",
        acceptance_criteria=["Fix the bug"],
        delegated=True,
        status="active",
        completion_evidence=None,
    )
    backend = MagicMock(id="sandbox-shared")

    async def metadata(thread_id: str) -> dict[str, object]:
        return {
            "sandbox_id": "sandbox-shared" if thread_id == "coordinator" else "sandbox-stale",
            "sandbox_host_thread_id": "forged-host",
        }

    with (
        patch("agent.sandboxes.lifecycle.task_store.task_for_thread", AsyncMock(return_value=task)),
        patch("agent.sandboxes.lifecycle.get_sandbox_metadata", side_effect=metadata),
        patch("agent.sandboxes.lifecycle.thread_token_repositories", AsyncMock(return_value=None)),
        patch(
            "agent.sandboxes.lifecycle._connect_existing_sandbox", AsyncMock(return_value=backend)
        ) as connect,
        patch("agent.sandboxes.lifecycle.client.threads.update", AsyncMock()) as update,
        patch("agent.sandboxes.tool_access.provision_tool_url", AsyncMock()) as provision,
    ):
        try:
            result = await ensure_sandbox_for_thread("worker", workspace_slug="forged-workspace")
            assert result.id == "sandbox-shared"
            assert connect.await_args is not None
            assert connect.await_args.args == ("coordinator",)
            assert connect.await_args.kwargs["sandbox_id"] == "sandbox-shared"
            assert connect.await_args.kwargs["workspace_slug"] == "task-workspace"
            update.assert_awaited_once_with(
                thread_id="worker",
                metadata={"sandbox_id": "sandbox-shared", "sandbox_host_thread_id": "coordinator"},
            )
            provision.assert_awaited_once_with("coordinator", backend)
        finally:
            SANDBOX_BACKENDS.pop("worker", None)
