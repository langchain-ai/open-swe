"""Tests for openswe.utils.repo and the repository a Linear session works in."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.linear.session import NO_REPOSITORY, LinearSessionTurn
from openswe.utils.repo import extract_repo_from_text
from openswe.webhooks.common import ThreadRecord

_NEW_THREAD = ThreadRecord(exists=False, metadata={})


class TestExtractRepoFromText:
    def test_explicit_repo_beats_github_url(self) -> None:
        result = extract_repo_from_text(
            "see https://github.com/langchain-ai/langgraph-api but use repo:my-org/my-repo"
        )
        assert result == {"owner": "my-org", "name": "my-repo"}


class TestLinearSessionRepository:
    """A Linear session turn works in the repository it names, else the session's, else a default."""

    @staticmethod
    def _turn(
        body: str, email: str | None = None, description: str | None = None
    ) -> LinearSessionTurn:
        creator = {"id": "user-1", "name": "Test User"}
        if email:
            creator["email"] = email
        turn = LinearSessionTurn.parse(
            "AgentSessionEvent.created",
            {
                "type": "AgentSessionEvent",
                "action": "created",
                "organizationId": "org-1",
                "webhookTimestamp": 1_700_000_000_000,
                "agentSession": {
                    "id": "session-1",
                    "issue": {
                        "id": "issue-456",
                        "identifier": "ENG-1",
                        "title": "Test issue",
                        "description": description,
                    },
                    "comment": {"id": "comment-123", "body": body},
                    "creator": creator,
                },
            },
        )
        assert isinstance(turn, LinearSessionTurn)
        return turn

    @pytest.mark.asyncio
    async def test_the_comment_names_the_repository(self) -> None:
        with patch("openswe.webhooks.common.is_repo_allowed", return_value=True):
            chosen = await self._turn("please fix this repo:custom-org/custom-repo").repository(
                _NEW_THREAD
            )
        assert chosen == ({"owner": "custom-org", "name": "custom-repo"}, None)

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
            chosen = await self._turn("please fix this bug").repository(_NEW_THREAD)
        assert chosen == ({"owner": "langchain-ai", "name": "open-swe"}, None)

    @pytest.mark.asyncio
    async def test_tells_the_session_a_repository_is_outside_the_allowlist(self) -> None:
        with patch("openswe.webhooks.common.is_repo_allowed", return_value=False):
            chosen = await self._turn("repo:other-org/other-repo").repository(_NEW_THREAD)
        assert chosen == {
            "decline": "I'm not allowed to work in `other-org/other-repo`. Name a repository "
            "Open SWE may work in, or ask your Open SWE admin to allow this one."
        }

    @pytest.mark.asyncio
    async def test_asks_which_repository_when_none_is_named_or_set(self) -> None:
        with patch(
            "openswe.webhooks.common.get_workspace_settings",
            AsyncMock(return_value=WorkspaceSettings({})),
        ):
            chosen = await self._turn("please fix this bug").repository(_NEW_THREAD)
        assert chosen == NO_REPOSITORY

    @staticmethod
    def _author(personal: dict[str, str], fallback: dict[str, str]) -> SimpleNamespace:
        """The author's Open SWE user, with a default only for the ``personal`` workspace."""

        async def default_repo(
            workspace: str | None = None, *, fallback_global: bool = True
        ) -> dict[str, str] | None:
            if workspace == "personal":
                return personal
            return fallback if fallback_global else None

        return SimpleNamespace(github_login="zhen", default_repo=default_repo)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("thread_workspace", "chosen"),
        [
            # A new thread: the author's default for the workspace they resolve to, which the
            # run then lands in.
            (None, ({"owner": "zhen", "name": "personal"}, "personal")),
            # The thread's workspace has no default of theirs: their global one, and the thread
            # keeps its workspace.
            ("core", ({"owner": "zhen", "name": "global"}, None)),
        ],
    )
    async def test_the_authors_default_for_the_workspace_comes_first(
        self,
        thread_workspace: str | None,
        chosen: tuple[dict[str, str], str | None],
    ) -> None:
        thread = ThreadRecord(True, {"workspace": thread_workspace} if thread_workspace else {})
        author = self._author(
            {"owner": "zhen", "name": "personal"}, {"owner": "zhen", "name": "global"}
        )
        with (
            patch("openswe.users.User.for_email", AsyncMock(return_value=author)),
            patch(
                "openswe.linear.session.resolve_workspace",
                AsyncMock(return_value=SimpleNamespace(slug="personal")),
            ),
            patch("openswe.webhooks.common.is_repo_allowed", return_value=True),
        ):
            assert (
                await self._turn("please fix this bug", "zhen@example.com").repository(thread)
                == chosen
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("thread", "description", "name"),
        [
            # An earlier turn chose it; the thread recorded it.
            (ThreadRecord(True, {"repo": {"owner": "acme", "name": "api"}}), None, "api"),
            # "repo setup" is prose, not a choice; the explicit token is.
            (_NEW_THREAD, "Fix the repo setup. It crashes in repo:acme/web on login.", "web"),
            # An uploaded screenshot or a gist isn't a repository; the link after them is.
            (
                _NEW_THREAD,
                "![shot](https://github.com/user-attachments/assets/0b1c) "
                "https://gist.github.com/ada/123 https://github.com/acme/app/issues/7",
                "app",
            ),
        ],
        ids=["the-thread-remembers", "the-issue-names", "the-issue-links"],
    )
    async def test_a_turn_naming_no_repository_works_where_the_session_does(
        self, thread: ThreadRecord, description: str | None, name: str
    ) -> None:
        with (
            patch(
                "openswe.webhooks.common.get_workspace_settings",
                AsyncMock(return_value=WorkspaceSettings({})),
            ),
            patch("openswe.webhooks.common.is_repo_allowed", return_value=True),
        ):
            chosen = await self._turn("now add a test", description=description).repository(thread)
        assert chosen == ({"owner": "acme", "name": name}, None)
