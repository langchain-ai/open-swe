"""Approval policy edits stay separate from reviewer style and preserve authorization."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    api_put_instance_settings,
    get_workspace_settings,
    upsert_instance_settings,
)
from agent.review.routes import api_delete_review_style, api_update_review_style_prompt
from agent.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from agent.reviewer import _review_approval_policy
from agent.tools.manage_review_approval_policy import manage_review_approval_policy
from tests.conftest import FakeStore


async def test_policy_edits_override_and_reset_without_changing_review_style(
    fake_store: FakeStore,
) -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(org_guidelines="Flag concrete bugs", pr_summaries=False)
    )
    await REVIEW_STYLES.set_custom_prompt("o/r", "Keep comments brief")
    with (
        patch(
            "agent.tools.manage_review_approval_policy.require_private_admin_surface",
            AsyncMock(return_value=None),
        ),
        patch("agent.reviewer.cached_workspace_settings", side_effect=get_workspace_settings),
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("agent.dashboard.deps.session_is_admin", return_value=True),
    ):
        await manage_review_approval_policy("save", policy="Docs only")
        assert await _review_approval_policy("o", "r", None) == "Docs only"
        saved = await api_update_review_style_prompt(
            "o/r",
            ReviewStylePromptUpdate(approval_policy="Allow small tested refactors"),
            {"sub": "admin"},
        )
        assert saved.custom_prompt == "Keep comments brief"
        assert await _review_approval_policy("o", "r", None) == "Allow small tested refactors"
        await REVIEW_STYLES.set_custom_prompt("o/r", "New analyzer output")
        assert await _review_approval_policy("o", "r", None) == "Allow small tested refactors"
        await api_update_review_style_prompt(
            "o/r", ReviewStylePromptUpdate(approval_policy=None), {"sub": "admin"}
        )
        assert await _review_approval_policy("o", "r", None) == "Docs only"
    settings = await get_workspace_settings()
    assert settings["org_guidelines"] == "Flag concrete bugs"
    assert settings["pr_summaries"] is False


async def test_non_admin_cannot_edit_repo_policy(fake_store: FakeStore) -> None:
    await REVIEW_STYLES.create("o/r", "reader")
    with (
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("agent.dashboard.deps.session_is_admin", return_value=False),
        pytest.raises(HTTPException) as error,
    ):
        await api_update_review_style_prompt(
            "o/r", ReviewStylePromptUpdate(approval_policy="Any change"), {"sub": "reader"}
        )
    assert error.value.status_code == 403
    record = await REVIEW_STYLES.get("o/r")
    assert record is not None and record.approval_policy is None
    await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_policy="Docs only"))
    with (
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch("agent.dashboard.deps.session_is_admin", return_value=False),
        pytest.raises(HTTPException) as error,
    ):
        await api_delete_review_style("o/r", {"sub": "reader"})
    assert error.value.status_code == 403
    record = await REVIEW_STYLES.get("o/r")
    assert record is not None and record.approval_policy == "Docs only"


async def test_instance_reset_returns_the_effective_default(fake_store: FakeStore) -> None:
    original = await get_workspace_settings()
    await upsert_instance_settings(WorkspaceSettingsUpdate(approval_policy="Docs only"))
    reset = await api_put_instance_settings(
        WorkspaceSettingsUpdate(approval_policy=None), {"sub": "admin"}
    )
    assert reset["approval_policy"] == original["approval_policy"]
    assert reset["approval_policy"]


async def test_tool_rechecks_private_admin_and_repo_access(fake_store: FakeStore) -> None:
    with (
        patch(
            "agent.tools.manage_review_approval_policy.require_private_admin_surface",
            AsyncMock(return_value="Private admin required"),
        ),
        pytest.raises(ValueError, match="Private admin"),
    ):
        await manage_review_approval_policy("save", policy="Any change")
    with (
        patch(
            "agent.tools.manage_review_approval_policy.require_private_admin_surface",
            AsyncMock(return_value=None),
        ),
        patch(
            "agent.tools.manage_review_approval_policy.private_credential_login",
            AsyncMock(return_value="admin"),
        ),
        patch(
            "agent.tools.manage_review_approval_policy.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "No access")),
        ),
        pytest.raises(HTTPException),
    ):
        await manage_review_approval_policy("save", policy="Any change", repository="private/repo")
    assert await REVIEW_STYLES.get("private/repo") is None
