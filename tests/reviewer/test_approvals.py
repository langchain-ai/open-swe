"""Approval criteria come from APPROVALS.md at the base commit; the repository's mode gates them."""

from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from fastapi import HTTPException

from agent.review.approvals import (
    APPROVALS_MAX_CHARS,
    approval_mode_for,
    approval_policy_for_review,
    fetch_approvals_md,
)
from agent.review.routes import api_delete_review_style, api_update_review_style_prompt
from agent.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from agent.tools.manage_review_approval_mode import manage_review_approval_mode
from tests.conftest import FakeStore


def _github(status: int, text: str = "") -> AsyncMock:
    return AsyncMock(return_value=httpx2.Response(status, text=text))


async def test_fetch_reads_the_file_at_the_requested_ref() -> None:
    request = _github(200, "  Docs-only changes may be approved.\n")
    with patch("agent.review.approvals.github_request", request):
        policy = await fetch_approvals_md("o", "r", "a" * 40, token="t")
    assert policy == "Docs-only changes may be approved."
    _client, method, url = request.await_args.args
    assert (method, url) == ("GET", "https://api.github.com/repos/o/r/contents/APPROVALS.md")
    assert request.await_args.kwargs["params"] == {"ref": "a" * 40}


@pytest.mark.parametrize(
    ("status", "text"),
    [(404, ""), (200, "   \n"), (200, "x" * (APPROVALS_MAX_CHARS + 1)), (500, "boom")],
    ids=["missing", "empty", "oversized", "error"],
)
async def test_fetch_yields_no_policy_for_unusable_files(status: int, text: str) -> None:
    with patch("agent.review.approvals.github_request", _github(status, text)):
        assert await fetch_approvals_md("o", "r", "main", token="t") is None


async def test_fetch_yields_no_policy_when_github_is_unreachable() -> None:
    failing = AsyncMock(side_effect=httpx2.ConnectError("down"))
    with patch("agent.review.approvals.github_request", failing):
        assert await fetch_approvals_md("o", "r", "main", token="t") is None


async def test_unset_mode_is_dry_run_and_off_skips_the_file(fake_store: FakeStore) -> None:
    assert await approval_mode_for("o", "r") == "dry_run"
    fetch = AsyncMock(return_value="Docs only")
    with patch("agent.review.approvals.fetch_approvals_md", fetch):
        assert await approval_policy_for_review("o", "r", "b" * 40, token="t") == "Docs only"
        fetch.assert_awaited_once_with("o", "r", "b" * 40, token="t")

        await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="off"))
        fetch.reset_mock()
        assert await approval_policy_for_review("o", "r", "b" * 40, token="t") is None
        fetch.assert_not_awaited()


async def test_review_without_a_base_commit_has_no_policy(fake_store: FakeStore) -> None:
    fetch = AsyncMock(return_value="Docs only")
    with patch("agent.review.approvals.fetch_approvals_md", fetch):
        assert await approval_policy_for_review("o", "r", "", token="t") is None
    fetch.assert_not_awaited()


async def test_failed_mode_lookup_never_approves() -> None:
    with patch.object(REVIEW_STYLES, "get", AsyncMock(side_effect=RuntimeError("store down"))):
        assert await approval_mode_for("o", "r") == "dry_run"


async def test_mode_edits_keep_the_review_style(fake_store: FakeStore) -> None:
    await REVIEW_STYLES.set_custom_prompt("o/r", "Keep comments brief")
    with (
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("agent.dashboard.deps.session_is_admin", return_value=True),
    ):
        saved = await api_update_review_style_prompt(
            "o/r", ReviewStylePromptUpdate(approval_mode="approve"), {"sub": "admin"}
        )
        assert (saved.custom_prompt, saved.approval_mode) == ("Keep comments brief", "approve")
        await REVIEW_STYLES.set_custom_prompt("o/r", "New analyzer output")
        assert await approval_mode_for("o", "r") == "approve"
        reset = await api_update_review_style_prompt(
            "o/r", ReviewStylePromptUpdate(approval_mode=None), {"sub": "admin"}
        )
    assert reset.approval_mode is None
    assert await approval_mode_for("o", "r") == "dry_run"


async def test_only_admins_change_or_discard_a_mode(fake_store: FakeStore) -> None:
    await REVIEW_STYLES.create("o/r", "reader")
    reader = (
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("agent.dashboard.deps.session_is_admin", return_value=False),
    )
    with reader[0], reader[1], pytest.raises(HTTPException) as error:
        await api_update_review_style_prompt(
            "o/r", ReviewStylePromptUpdate(approval_mode="off"), {"sub": "reader"}
        )
    assert error.value.status_code == 403
    assert await approval_mode_for("o", "r") == "dry_run"

    await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="approve"))
    with reader[0], reader[1], pytest.raises(HTTPException) as error:
        await api_delete_review_style("o/r", {"sub": "reader"})
    assert error.value.status_code == 403
    assert await approval_mode_for("o", "r") == "approve"


async def test_tool_sets_mode_and_reports_the_default_branch_file(fake_store: FakeStore) -> None:
    fetch = AsyncMock(return_value="Docs only")
    with (
        patch(
            "agent.tools.manage_review_approval_mode.require_private_admin_surface",
            AsyncMock(return_value=None),
        ),
        patch(
            "agent.tools.manage_review_approval_mode.private_credential_login",
            AsyncMock(return_value="admin"),
        ),
        patch(
            "agent.tools.manage_review_approval_mode.require_repo_access_for_user",
            AsyncMock(return_value="user-token"),
        ),
        patch("agent.tools.manage_review_approval_mode.fetch_approvals_md", fetch),
    ):
        assert await manage_review_approval_mode("read", "O/R") == {
            "repository": "O/R",
            "mode": "dry_run",
            "approvals_file_on_default_branch": True,
        }
        fetch.assert_awaited_with("O", "R", None, token="user-token")
        result = await manage_review_approval_mode("set", "O/R", "approve")
        assert result["mode"] == "approve"
        result = await manage_review_approval_mode("set", "O/R", None)
        assert result["mode"] == "dry_run"


async def test_tool_rechecks_private_admin_and_repo_access(fake_store: FakeStore) -> None:
    with (
        patch(
            "agent.tools.manage_review_approval_mode.require_private_admin_surface",
            AsyncMock(return_value="Private admin required"),
        ),
        pytest.raises(ValueError, match="Private admin"),
    ):
        await manage_review_approval_mode("set", "o/r", "approve")
    with (
        patch(
            "agent.tools.manage_review_approval_mode.require_private_admin_surface",
            AsyncMock(return_value=None),
        ),
        patch(
            "agent.tools.manage_review_approval_mode.private_credential_login",
            AsyncMock(return_value="admin"),
        ),
        patch(
            "agent.tools.manage_review_approval_mode.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "No access")),
        ),
        pytest.raises(HTTPException),
    ):
        await manage_review_approval_mode("set", "private/repo", "approve")
    assert await REVIEW_STYLES.get("private/repo") is None
