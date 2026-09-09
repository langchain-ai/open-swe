"""Tests for agent.utils.repo and Linear webhook repo override behavior."""

from unittest.mock import AsyncMock

import pytest

from agent.linear import sessions
from agent.linear.schema import LinearIssue
from agent.slack.client import extract_channel_description_text
from agent.utils.repo import extract_repo_from_text


class TestExtractRepoFromText:
    def test_repo_colon_with_org(self) -> None:
        result = extract_repo_from_text("please use repo:my-org/my-repo")
        assert result == {"owner": "my-org", "name": "my-repo"}

    def test_repo_space_with_org(self) -> None:
        result = extract_repo_from_text("please use repo langchain-ai/langchainjs")
        assert result == {"owner": "langchain-ai", "name": "langchainjs"}

    def test_repo_colon_name_only_uses_default_owner(self) -> None:
        result = extract_repo_from_text(
            "fix bug in repo:langchainplus", default_owner="langchain-ai"
        )
        assert result == {"owner": "langchain-ai", "name": "langchainplus"}

    def test_repo_space_name_only_uses_default_owner(self) -> None:
        result = extract_repo_from_text("fix bug in repo open-swe", default_owner="langchain-ai")
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


class TestExtractChannelDescriptionText:
    def test_combines_topic_and_purpose(self) -> None:
        channel = {
            "topic": {"value": "repo:my-org/my-repo"},
            "purpose": {"value": "Team channel"},
        }
        assert extract_channel_description_text(channel) == "repo:my-org/my-repo\nTeam channel"

    def test_handles_missing_sections(self) -> None:
        assert extract_channel_description_text({"topic": {"value": "hi"}}) == "hi"

    def test_empty_for_none(self) -> None:
        assert extract_channel_description_text(None) == ""

    def test_empty_for_blank_values(self) -> None:
        channel = {"topic": {"value": "  "}, "purpose": {"value": ""}}
        assert extract_channel_description_text(channel) == ""

    def test_repo_token_extractable_from_description(self) -> None:
        channel = {"topic": {"value": "Use repo:langchain-ai/open-swe here"}, "purpose": {}}
        description = extract_channel_description_text(channel)
        assert extract_repo_from_text(description) == {
            "owner": "langchain-ai",
            "name": "open-swe",
        }


class TestLinearRepoResolution:
    """The precedence a Linear trigger uses to choose a repository."""

    def _issue(self) -> LinearIssue:
        return LinearIssue.model_validate({"id": "issue-456", "team": {"name": "Open SWE"}})

    async def test_text_named_repo_beats_the_team_mapping(self) -> None:
        repo_config = await sessions.resolve_repo_config(
            trigger_text="@openswe please fix this repo:custom-org/custom-repo",
            requester_email="",
            issue=self._issue(),
        )

        assert repo_config == {"owner": "custom-org", "name": "custom-repo"}

    async def test_falls_back_to_the_team_mapping(self) -> None:
        repo_config = await sessions.resolve_repo_config(
            trigger_text="@openswe please fix this bug",
            requester_email="",
            issue=self._issue(),
        )

        assert repo_config == {"owner": "langchain-ai", "name": "open-swe"}

    async def test_dashboard_default_beats_the_team_mapping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sessions, "resolve_login_from_email_async", AsyncMock(return_value="zhen")
        )
        monkeypatch.setattr(
            sessions,
            "get_profile_default_repo",
            AsyncMock(return_value={"owner": "zhen", "name": "profile-repo"}),
        )

        repo_config = await sessions.resolve_repo_config(
            trigger_text="@openswe please fix this bug",
            requester_email="zhen@example.com",
            issue=self._issue(),
        )

        assert repo_config == {"owner": "zhen", "name": "profile-repo"}
