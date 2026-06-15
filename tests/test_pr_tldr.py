"""Tests for the PR TLDR feature: comment rendering and settings migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.team_settings import TeamSettingsUpdate
from agent.reviewer_publish import (
    pr_tldr_marker,
    render_pr_tldr_comment,
    upsert_pr_tldr_comment,
)


def test_pr_tldr_marker_is_pr_scoped():
    assert pr_tldr_marker(42) == "<!-- open-swe-pr-tldr pr=42 -->"


def test_render_pr_tldr_comment_includes_marker_and_body():
    body = render_pr_tldr_comment(markdown="- caches user lookups (TTL 60s)", pr_number=7)
    assert "## Open SWE TLDR" in body
    assert "- caches user lookups (TTL 60s)" in body
    assert body.endswith(pr_tldr_marker(7))


def test_settings_migrate_legacy_pr_summaries_to_pr_tldr():
    # A stored record written before the rename should preserve its toggle.
    update = TeamSettingsUpdate.model_validate({"pr_summaries": False})
    assert update.pr_tldr is False
    assert update.code_review is True


def test_settings_pr_tldr_wins_over_legacy_when_both_present():
    update = TeamSettingsUpdate.model_validate({"pr_summaries": False, "pr_tldr": True})
    assert update.pr_tldr is True


@pytest.mark.asyncio
async def test_upsert_updates_existing_comment_in_place():
    response = AsyncMock()
    response.status_code = 200
    response.raise_for_status = lambda: None
    client = AsyncMock()
    client.patch.return_value = response
    with patch("agent.reviewer_publish.httpx.AsyncClient") as mk:
        mk.return_value.__aenter__.return_value = client
        result = await upsert_pr_tldr_comment(
            owner="o", repo="r", pr_number=1, body="b", token="t", existing_comment_id=99
        )
    assert result == 99
    client.patch.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_recreates_when_existing_comment_gone():
    patch_response = AsyncMock()
    patch_response.status_code = 404
    client = AsyncMock()
    client.patch.return_value = patch_response
    with (
        patch("agent.reviewer_publish.httpx.AsyncClient") as mk,
        patch(
            "agent.reviewer_publish.post_status_comment",
            new=AsyncMock(return_value=123),
        ) as post,
    ):
        mk.return_value.__aenter__.return_value = client
        result = await upsert_pr_tldr_comment(
            owner="o", repo="r", pr_number=1, body="b", token="t", existing_comment_id=99
        )
    assert result == 123
    post.assert_awaited_once()
