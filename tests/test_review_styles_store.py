from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.review_styles import (
    append_repo_prompt_learning,
    create_review_style,
    get_repo_custom_prompt,
    set_custom_prompt,
)


@pytest.mark.asyncio
async def test_get_repo_custom_prompt_returns_trimmed_text() -> None:
    with patch(
        "agent.dashboard.review_styles.get_review_style",
        new_callable=AsyncMock,
        return_value={"custom_prompt": "  Flag nil deref aggressively.\n"},
    ):
        prompt = await get_repo_custom_prompt("acme", "repo")
    assert prompt == "Flag nil deref aggressively."


@pytest.mark.asyncio
async def test_create_review_style_puts_new_record() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.review_styles._get_value", new_callable=AsyncMock, return_value=None
        ),
        patch("agent.dashboard.review_styles._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await create_review_style("acme/repo", "octo")
    assert record["full_name"] == "acme/repo"
    assert record["status"] == "idle"
    mock_put.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_custom_prompt_updates_store() -> None:
    with (
        patch(
            "agent.dashboard.review_styles.get_review_style",
            new_callable=AsyncMock,
            return_value={"full_name": "acme/repo", "status": "completed"},
        ),
        patch(
            "agent.dashboard.review_styles.update_review_style", new_callable=AsyncMock
        ) as mock_up,
    ):
        await set_custom_prompt("acme/repo", "Use direct tone.")
    mock_up.assert_awaited_once()


@pytest.mark.asyncio
async def test_append_repo_prompt_learning_adds_learned_section() -> None:
    with (
        patch(
            "agent.dashboard.review_styles.get_review_style",
            new_callable=AsyncMock,
            return_value={
                "full_name": "acme/repo",
                "status": "completed",
                "custom_prompt": "Use direct tone.",
            },
        ),
        patch(
            "agent.dashboard.review_styles.update_review_style", new_callable=AsyncMock
        ) as mock_up,
    ):
        await append_repo_prompt_learning(
            "https://github.com/acme/repo",
            "Avoid flagging missing tests for copy-only changes.",
            source="PR #12",
        )

    patch_arg = mock_up.await_args.args[1]
    assert patch_arg["status"] == "completed"
    assert "## Learned from PR comment replies" in patch_arg["custom_prompt"]
    assert "Avoid flagging missing tests" in patch_arg["custom_prompt"]
    assert "source: PR #12" in patch_arg["custom_prompt"]


@pytest.mark.asyncio
async def test_append_repo_prompt_learning_deduplicates_existing_learning() -> None:
    with (
        patch(
            "agent.dashboard.review_styles.get_review_style",
            new_callable=AsyncMock,
            return_value={
                "full_name": "acme/repo",
                "status": "completed",
                "custom_prompt": "## Learned from PR comment replies\n\n- Prefer migrations over ad-hoc SQL.",
            },
        ),
        patch(
            "agent.dashboard.review_styles.update_review_style", new_callable=AsyncMock
        ) as mock_up,
    ):
        await append_repo_prompt_learning("acme/repo", "Prefer migrations over ad-hoc SQL.")

    patch_arg = mock_up.await_args.args[1]
    assert "custom_prompt" not in patch_arg
    assert patch_arg["status"] == "completed"
