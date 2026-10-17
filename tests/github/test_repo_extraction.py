"""Tests for openswe.utils.repo and the repository a Linear session works in."""

from unittest.mock import AsyncMock, patch

import pytest

from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.linear.session import LinearSessionTurn
from openswe.utils.repo import extract_repo_from_text


class TestExtractRepoFromText:
    def test_explicit_repo_beats_github_url(self) -> None:
        result = extract_repo_from_text(
            "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo"
        )
        assert result == {"owner": "my-org", "name": "my-repo"}


class TestLinearSessionRepository:
    """A Linear session turn works in the repository its comment names, else a default."""

    @staticmethod
    def _turn(body: str) -> LinearSessionTurn:
        turn = LinearSessionTurn.parse(
            "AgentSessionEvent.created",
            {
                "type": "AgentSessionEvent",
                "action": "created",
                "agentSession": {
                    "id": "session-1",
                    "issue": {"id": "issue-456", "identifier": "ENG-1", "title": "Test issue"},
                    "comment": {"id": "comment-123", "body": body},
                    "creator": {"id": "user-1", "name": "Test User"},
                },
            },
        )
        assert turn is not None
        return turn

    @pytest.mark.asyncio
    async def test_the_comment_names_the_repository(self) -> None:
        with patch("openswe.webhooks.common.is_repo_allowed", return_value=True):
            repo = await self._turn("please fix this repo:custom-org/custom-repo").repository()
        assert repo == {"owner": "custom-org", "name": "custom-repo"}

    @pytest.mark.asyncio
    async def test_falls_back_to_the_workspace_default(self) -> None:
        with (
            patch(
                "openswe.webhooks.common.get_workspace_settings",
                AsyncMock(
                    return_value=WorkspaceSettings({"default_repo": "langchain-ai/open-swe"})
                ),
            ),
            patch("openswe.webhooks.common.is_repo_allowed", return_value=True),
        ):
            repo = await self._turn("please fix this bug").repository()
        assert repo == {"owner": "langchain-ai", "name": "open-swe"}

    @pytest.mark.asyncio
    async def test_refuses_a_repository_outside_the_allowlist(self) -> None:
        with patch("openswe.webhooks.common.is_repo_allowed", return_value=False):
            assert await self._turn("repo:other-org/other-repo").repository() is None
