"""A thread whose old sandbox is gone or abandoned is handed to MDA; nothing replaces it here.

A box the backend made before MDA owned them that is deleted holds nothing, and a
reviewer's holds only a checkout `prepare_review_repo` re-derives every run, so the
thread is unbound and its next run starts in the box MDA creates. Any other
unreachable box fails loudly, because a fresh one would lose the thread's work.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig
from langsmith.sandbox import SandboxClientError

from openswe.reviewer import PrepareReviewerRunMiddleware, ensure_reviewer_sandbox_for_thread
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import SANDBOX_BACKENDS, ensure_sandbox_for_thread
from openswe.sandboxes.providers.registry import SandboxGoneError
from openswe.sandboxes.state import (
    SandboxHandedOverError,
    SandboxUnreachableError,
    set_sandbox_backend,
)

_HANDED_OVER = {"sandbox_id": None, "sandbox_base_proxy_config": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "owner", "abandon", "handed_over"),
    [
        (SandboxGoneError("Sandbox 'sandbox-old' not found"), None, False, True),
        (RuntimeError("connection refused"), None, True, True),
        (RuntimeError("connection refused"), None, False, False),
        (SandboxGoneError("Sandbox 'sandbox-old' not found"), "mda", True, False),
    ],
)
async def test_an_old_sandbox_that_cannot_be_reached_is_handed_to_mda_or_fails(
    failure: Exception, owner: str | None, abandon: bool, handed_over: bool
) -> None:
    thread_id = "thread-old-sandbox"
    SANDBOX_BACKENDS.clear()
    metadata = {"sandbox_id": "sandbox-old"} | ({"sandbox_owner": owner} if owner else {})

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value=metadata,
        ),
        patch(
            "openswe.sandboxes.lifecycle.connect_existing_sandbox",
            new_callable=AsyncMock,
            side_effect=failure,
        ),
        patch(
            "openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock
        ) as update_thread,
        pytest.raises(SandboxUnreachableError) as excinfo,
    ):
        await ensure_sandbox_for_thread(thread_id, abandon_unreachable=abandon)

    # Typed, so callers still recognize it and tell the person whether a fresh box follows.
    assert excinfo.value.sandbox_id == "sandbox-old"
    assert isinstance(excinfo.value, SandboxHandedOverError) is handed_over
    writes = [call.kwargs["metadata"] for call in update_thread.await_args_list]
    assert writes == ([_HANDED_OVER] if handed_over else [])
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_an_abandoned_cached_sandbox_is_forgotten() -> None:
    thread_id = "thread-reviewer-dead-cache"
    SANDBOX_BACKENDS.clear()
    proxy = set_sandbox_backend(thread_id, MagicMock(id="sandbox-cached-dead"))

    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value={"sandbox_id": "sandbox-cached-dead"},
        ),
        patch(
            "openswe.sandboxes.lifecycle._refresh_github_proxy",
            new_callable=AsyncMock,
            side_effect=SandboxClientError("sandbox is gone"),
        ),
        patch("openswe.sandboxes.lifecycle.configure_git_identity", new_callable=AsyncMock),
        patch("openswe.sandboxes.lifecycle.client.threads.update", new_callable=AsyncMock),
        pytest.raises(SandboxUnreachableError),
    ):
        await ensure_sandbox_for_thread(thread_id, abandon_unreachable=True)

    # Handles already built around the proxy reach no box until the thread has one again.
    assert not proxy.has_backend
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_reviewer_opts_into_abandoning_an_unreachable_sandbox() -> None:
    sandbox_backend = MagicMock()

    with patch(
        "openswe.reviewer.ensure_sandbox_for_thread",
        new_callable=AsyncMock,
        return_value=sandbox_backend,
    ) as ensure:
        result = await ensure_reviewer_sandbox_for_thread(
            "thread-reviewer",
            RunConfig.parse({"repo": {"owner": "langchain-ai", "name": "open-swe"}}),
        )

    assert result is sandbox_backend
    assert ensure.await_args is not None
    assert ensure.await_args.kwargs["abandon_unreachable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("handed_over", [True, False])
async def test_reviewer_says_whether_its_next_run_gets_a_fresh_sandbox(handed_over: bool) -> None:
    config: RunnableConfig = {
        "configurable": {"repo": {"owner": "langchain-ai", "name": "open-swe"}}
    }
    middleware = PrepareReviewerRunMiddleware(
        thread_id="thread-reviewer", config=config, use_gateway=False
    )

    with (
        patch(
            "openswe.reviewer.ensure_reviewer_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=(SandboxHandedOverError if handed_over else SandboxUnreachableError)(
                "thread-reviewer", "sandbox-deleted", "not found"
            ),
        ),
        patch(
            "openswe.reviewer.post_sandbox_unreachable_notification",
            new_callable=AsyncMock,
        ) as notify,
        pytest.raises(SandboxUnreachableError),
    ):
        await middleware._prepare({"messages": []}, MagicMock())

    notify.assert_awaited_once()
    assert notify.await_args is not None
    assert notify.await_args.kwargs == {
        "sandbox_id": "sandbox-deleted",
        "handed_to_mda": handed_over,
    }
