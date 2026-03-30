"""Tests for agent.utils.repo and Linear webhook repo override behavior."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.utils.repo import extract_repo_from_text


class TestExtractRepoFromText:
    def test_repo_colon_with_org(self) -> None:
        result = extract_repo_from_text("please use repo:my-org/my-repo")
        assert result == {"owner": "my-org", "name": "my-repo"}

    def test_repo_space_with_org(self) -> None:
        result = extract_repo_from_text("please use repo langchain-ai/langchainjs")
        assert result == {"owner": "langchain-ai", "name": "langchainjs"}

    def test_repo_colon_name_only_uses_default_owner(self) -> None:
        result = extract_repo_from_text("fix bug in repo:langchainplus")
        assert result == {"owner": "langchain-ai", "name": "langchainplus"}

    def test_repo_space_name_only_uses_default_owner(self) -> None:
        result = extract_repo_from_text("fix bug in repo open-swe")
        assert result == {"owner": "langchain-ai", "name": "open-swe"}

    def test_repo_name_only_custom_default_owner(self) -> None:
        result = extract_repo_from_text("repo:my-repo", default_owner="custom-org")
        assert result == {"owner": "custom-org", "name": "my-repo"}

    def test_github_url(self) -> None:
        result = extract_repo_from_text(
            "check https://github.com/langchain-ai/langgraph-api please"
        )
        assert result == {"owner": "langchain-ai", "name": "langgraph-api"}

    def test_explicit_repo_beats_github_url(self) -> None:
        result = extract_repo_from_text(
            "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo"
        )
        assert result == {"owner": "my-org", "name": "my-repo"}

    def test_no_repo_returns_none(self) -> None:
        result = extract_repo_from_text("please fix the bug")
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = extract_repo_from_text("")
        assert result is None

    def test_trailing_slash_stripped(self) -> None:
        result = extract_repo_from_text("repo:my-org/my-repo/")
        assert result == {"owner": "my-org", "name": "my-repo"}

    def test_repo_syntax_supports_gitlab_subgroups(self) -> None:
        result = extract_repo_from_text("repo:platform/frontend/test-swe")
        assert result == {"owner": "platform/frontend", "name": "test-swe"}

    def test_gitlab_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_URL", "http://gitlab.local")
        result = extract_repo_from_text("check http://gitlab.local/platform/frontend/test-swe please")
        assert result == {"owner": "platform/frontend", "name": "test-swe"}


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
        from agent.webapp import linear_webhook

        with (
            patch("agent.webapp.verify_linear_signature", return_value=True),
            patch(
                "agent.webapp.fetch_linear_issue_details",
                new_callable=AsyncMock,
                return_value={
                    "id": "issue-456",
                    "title": "Test issue",
                    "identifier": "TEST-1",
                    "url": "https://linear.app/test/issue/TEST-1",
                    "team": {"id": "t1", "name": "Some Team", "key": "ST"},
                    "project": {"id": "p1", "name": "Some Project"},
                    "comments": {"nodes": []},
                },
            ),
            patch("agent.webapp._is_repo_org_allowed", return_value=True),
            patch("agent.webapp.BackgroundTasks"),
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
    async def test_falls_back_to_team_mapping_when_no_repo_in_comment(self) -> None:
        from agent.webapp import linear_webhook

        payload = {
            "type": "Comment",
            "action": "create",
            "data": {
                "id": "comment-123",
                "body": "@openswe please fix this bug",
                "issue": {
                    "id": "issue-456",
                    "title": "Test issue",
                },
                "user": {"id": "user-1", "name": "Test User", "email": "test@test.com"},
            },
        }

        with (
            patch("agent.webapp.verify_linear_signature", return_value=True),
            patch(
                "agent.webapp.fetch_linear_issue_details",
                new_callable=AsyncMock,
                return_value={
                    "id": "issue-456",
                    "title": "Test issue",
                    "identifier": "TEST-1",
                    "url": "https://linear.app/test/issue/TEST-1",
                    "team": {"id": "t1", "name": "Open SWE", "key": "OS"},
                    "project": None,
                    "comments": {"nodes": []},
                },
            ),
            patch("agent.webapp._is_repo_org_allowed", return_value=True),
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
