"""Approval criteria come from APPROVALS.md at the base commit; the repository's mode gates them."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.review.approvals import (
    approval_mode_for,
)
from agent.review.routes import api_delete_review_style, api_update_review_style_prompt
from agent.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from agent.tools.manage_review_approval_mode import manage_review_approval_mode
from tests.conftest import FakeStore


async def test_failed_mode_lookup_never_approves() -> None:
    with patch.object(REVIEW_STYLES, "get", AsyncMock(side_effect=RuntimeError("store down"))):
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
