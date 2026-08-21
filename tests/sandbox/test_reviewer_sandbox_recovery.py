"""The reviewer replaces an unreachable sandbox; the coding agent still fails loudly.

A reviewer sandbox holds only a checkout `prepare_review_repo` re-derives every
run, and reviewer threads (one per PR) outlive their sandbox, so refusing to
replace one bricks reviews on that PR permanently.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig
from langsmith.sandbox import SandboxClientError

from agent.graphs.reviewer import PrepareReviewerRunMiddleware, _ensure_reviewer_sandbox_for_thread
from agent.runtime.sandbox import ensure_sandbox_for_thread
from agent.utils.sandbox import SandboxGoneError, SandboxUnreachableError
from agent.utils.sandbox_registry import set_sandbox_backend


@pytest.mark.asyncio
async def test_replaces_unreachable_sandbox_when_replacement_allowed() -> None:
    thread_id = "thread-reviewer-dead-sandbox"
    replacement = MagicMock()
    replacement.id = "sandbox-replacement"

    with (
        patch(
            "agent.runtime.sandbox.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-deleted",
        ),
        patch(
            "agent.runtime.sandbox.create_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=replacement,
        ) as create_replacement,
        patch("agent.runtime.sandbox._configure_git_identity", new_callable=AsyncMock),
        patch(
            "agent.runtime.sandbox.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
    ):
        result = await ensure_sandbox_for_thread(thread_id, allow_replacement=True)

    assert result.id == "sandbox-replacement"
    create_replacement.assert_awaited_once()
    # The stale id is cleared by persisting the replacement, so later runs stop
    # reconnecting to a sandbox that no longer exists.
    assert update_thread.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-replacement"},
    }


@pytest.mark.asyncio
async def test_replaces_unreachable_cached_sandbox_when_replacement_allowed() -> None:
    thread_id = "thread-reviewer-dead-cache"
    dead = MagicMock()
    dead.id = "sandbox-cached-dead"
    proxy = set_sandbox_backend(thread_id, dead)
    replacement = MagicMock()
    replacement.id = "sandbox-replacement"

    with (
        patch(
            "agent.runtime.sandbox.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-cached-dead",
        ),
        patch(
            "agent.runtime.sandbox.configure_proxy_for_sandbox",
            new_callable=AsyncMock,
            side_effect=SandboxClientError("sandbox is gone"),
        ),
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=replacement,
        ) as create_replacement,
        patch("agent.runtime.sandbox._configure_git_identity", new_callable=AsyncMock),
        patch("agent.runtime.sandbox.client.threads.update", new_callable=AsyncMock),
    ):
        result = await ensure_sandbox_for_thread(thread_id, allow_replacement=True)

    create_replacement.assert_awaited_once()
    # Replaced in place, so handles already built around the proxy stay valid.
    assert result is proxy
    assert proxy.current is replacement


@pytest.mark.asyncio
async def test_failed_replacement_still_raises_sandbox_unreachable() -> None:
    thread_id = "thread-reviewer-replacement-fails"
    with (
        patch(
            "agent.runtime.sandbox.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-deleted",
        ),
        patch(
            "agent.runtime.sandbox.create_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            side_effect=RuntimeError("sandbox API outage"),
        ),
        patch("agent.runtime.sandbox._configure_git_identity", new_callable=AsyncMock),
        patch("agent.runtime.sandbox.client.threads.update", new_callable=AsyncMock),
        pytest.raises(SandboxUnreachableError) as excinfo,
    ):
        await ensure_sandbox_for_thread(thread_id, allow_replacement=True)

    # Typed, so the reviewer still recognizes it and notifies on the PR.
    assert excinfo.value.sandbox_id == "sandbox-deleted"
    assert "sandbox API outage" in str(excinfo.value)


@pytest.mark.asyncio
async def test_unreachable_sandbox_still_fails_by_default() -> None:
    thread_id = "thread-agent-dead-sandbox"
    with (
        patch(
            "agent.runtime.sandbox.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-deleted",
        ),
        patch(
            "agent.runtime.sandbox.create_sandbox",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy",
            new_callable=AsyncMock,
        ) as create_replacement,
        patch("agent.runtime.sandbox._configure_git_identity", new_callable=AsyncMock),
        patch("agent.runtime.sandbox.client.threads.update", new_callable=AsyncMock),
        pytest.raises(SandboxUnreachableError),
    ):
        await ensure_sandbox_for_thread(thread_id)

    create_replacement.assert_not_awaited()


@pytest.mark.asyncio
async def test_reviewer_opts_into_replacement() -> None:
    sandbox_backend = MagicMock()

    with patch(
        "agent.graphs.reviewer.ensure_sandbox_for_thread",
        new_callable=AsyncMock,
        return_value=sandbox_backend,
    ) as ensure:
        result, github_token = await _ensure_reviewer_sandbox_for_thread(
            "thread-reviewer", {"repo": {"owner": "langchain-ai", "name": "open-swe"}}
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
            "agent.graphs.reviewer._ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=SandboxUnreachableError("thread-reviewer", "sandbox-deleted", "not found"),
        ),
        patch(
            "agent.graphs.reviewer.post_sandbox_unreachable_notification",
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
    replacement = MagicMock()
    replacement.id = "sandbox-replacement"
    order: list[str] = []

    with (
        patch(
            "agent.runtime.sandbox.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value="sandbox-deleted",
        ),
        patch(
            "agent.runtime.sandbox.create_sandbox",
            new_callable=AsyncMock,
            side_effect=SandboxGoneError("Sandbox 'sandbox-deleted' not found"),
        ),
        patch(
            "agent.runtime.sandbox._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=replacement,
        ) as create_replacement,
        patch(
            "agent.runtime.sandbox._configure_git_identity",
            new_callable=AsyncMock,
            side_effect=lambda *_: order.append("init"),
        ),
        patch(
            "agent.runtime.sandbox.client.threads.update",
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
    # The thread binds to the sandbox only once it is initialized.
    assert order == ["init", "bind"]
