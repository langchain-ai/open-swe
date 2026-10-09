"""Tests for openswe.utils.repo and Linear webhook repo override behavior."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from openswe.utils.repo import extract_repo_from_text
from openswe.web.workspace_settings import WorkspaceSettings


class TestExtractRepoFromText:
    def test_explicit_repo_beats_github_url(self) -> None:
        result = extract_repo_from_text(
            "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo"
        )
        assert result == {"owner": "my-org", "name": "my-repo"}


class TestLinearWebhookRepoOverride:
    """Test that the Linear webhook handler checks comment body for repo config first."""

    @pytest.fixture()
    def _base_payload(self) -> dict:
        return {
            "type": "Comment",
            "action": "create",
            "data": {
                "id": "comment-123",
                "body": "@openswe please fix this repo:custom-org/custom-repo",
                "issue": {
                    "id": "issue-456",
                    "title": "Test issue",
                },
                "user": {"id": "user-1", "name": "Test User", "email": "test@test.com"},
            },
        }

    @pytest.mark.asyncio
    async def test_comment_repo_overrides_team_mapping(self, _base_payload: dict) -> None:
        from openswe.linear.routes import linear_webhook

        with (
            patch("openswe.webhooks.common.verify_linear_signature", return_value=True),
            patch("openswe.webhooks.common.is_repo_allowed", return_value=True),
            patch("openswe.webhooks.common.BackgroundTasks"),
        ):
            mock_request = AsyncMock()
            mock_request.body.return_value = json.dumps(_base_payload).encode()
            mock_request.headers = {"Linear-Signature": "valid"}

            bg_tasks = AsyncMock()
            result = await linear_webhook(mock_request, bg_tasks)

            assert result["status"] == "accepted"
            assert "custom-org/custom-repo" in result["message"]

            call_args = bg_tasks.add_task.call_args
            repo_config = call_args[0][2]
            assert repo_config == {"owner": "custom-org", "name": "custom-repo"}

    @pytest.mark.asyncio
    async def test_falls_back_to_default_repo_with_standard_comment_payload(self) -> None:
        from openswe.linear.routes import linear_webhook

        payload = {
            "type": "Comment",
            "action": "create",
            "actor": {
                "id": "user-1",
                "name": "Test User",
                "email": "test@test.com",
            },
            "data": {
                "id": "comment-123",
                "body": "@openswe please fix this bug",
                "issueId": "issue-456",
                "userId": "user-1",
            },
        }

        with (
            patch("openswe.webhooks.common.verify_linear_signature", return_value=True),
            patch(
                "openswe.webhooks.common.get_workspace_settings",
                AsyncMock(
                    return_value=WorkspaceSettings({"default_repo": "langchain-ai/open-swe"})
                ),
            ),
            patch("openswe.webhooks.common.is_repo_allowed", return_value=True),
        ):
            mock_request = AsyncMock()
            mock_request.body.return_value = json.dumps(payload).encode()
            mock_request.headers = {"Linear-Signature": "valid"}

            bg_tasks = AsyncMock()
            result = await linear_webhook(mock_request, bg_tasks)

            assert result["status"] == "accepted"
            assert "langchain-ai/open-swe" in result["message"]

            call_args = bg_tasks.add_task.call_args
            repo_config = call_args[0][2]
            assert repo_config == {"owner": "langchain-ai", "name": "open-swe"}
