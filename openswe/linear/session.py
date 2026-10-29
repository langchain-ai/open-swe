"""What only the backend knows about a Linear agent session turn: ``start_linear_session``.

MDA's Linear channel reads the turn, builds the run's input and starts it on the
session's own thread, so the conversation's reply, Stop and errors reach Linear.
It asks here for what needs Open SWE's data: whether the author has signed in to
Open SWE, which acts on GitHub as them; the repository and workspace; and the
signed run context the run calls back with. The thread is indexed for the
dashboard before the channel starts the run, which carries the backend's
run-complete webhook. Each agent session is its own thread, so a second session
on the same issue is a new conversation, with the issue's title.
"""

from typing import cast

from openswe_shared.images import extract_image_urls
from pydantic import JsonValue
from runtime_api.linear import (
    DeclineReason,
    LinearSessionDeclined,
    LinearSessionRun,
    LinearSessionStart,
    LinearSessionTurn,
)

from openswe.dashboard.oauth import build_settings_url
from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.source_context import SourceContext
from openswe.threads.router import (
    adopt_channel_run,
    channel_run_metadata,
    channel_run_starting,
)
from openswe.threads.runtimes import RUNTIMES
from openswe.users import User
from openswe.webhooks import common
from openswe.workspaces.routing import resolve_workspace

#: The channel's name in run and thread metadata (``CHANNEL_METADATA_KEY``).
LINEAR_CHANNEL = "linear"


def _declined(
    reason: DeclineReason, *, repository: str | None = None, settings_url: str | None = None
) -> LinearSessionStart:
    return LinearSessionStart(
        answer=LinearSessionDeclined(
            reason=reason, repository=repository, settings_url=settings_url
        )
    )


class LinearSession:
    """One turn of a Linear agent session, as MDA's channel read it."""

    def __init__(self, turn: LinearSessionTurn) -> None:
        self.turn = turn
        self.thread_id = str(turn.thread_id)
        self.issue = turn.issue
        self.author = turn.author

    @property
    def title(self) -> str:
        return self.issue.title or self.issue.identifier or "Linear issue"

    def _issue_repository(self) -> dict[str, str] | None:
        """The repository the issue names as ``repo:owner/name`` or a GitHub link."""
        return common.extract_repo_from_text(
            f"{self.issue.title}\n{self.issue.description or ''}",
            default_owner=common.DEFAULT_REPO_OWNER,
            explicit_only=True,
        )

    async def repository(
        self, thread: common.ThreadRecord, author: User
    ) -> tuple[dict[str, str], str | None] | LinearSessionStart:
        """The repository the turn works in, when one is named, remembered or preferred.

        A repository the turn names wins; then the one the session's thread already
        works in, so a follow-up needn't name it again; then one the issue names;
        then the author's and the workspace's defaults. With it, the workspace whose
        personal default chose it, if one did. Otherwise why the turn starts nothing.
        """
        repo = (
            common.extract_repo_from_text(self.turn.body, default_owner=common.DEFAULT_REPO_OWNER)
            or thread.repo
            or self._issue_repository()
        )
        workspace: str | None = None
        if not repo:
            try:
                repo, workspace = await self._personal_default(author, thread.workspace)
            except Exception:
                common.logger.exception(
                    "Reading a Linear user's default repository failed",
                    extra={"linear_issue": self.issue.id},
                )
        if not repo:
            repo = (await common.get_workspace_settings()).default_repo
        if not repo:
            common.logger.info(
                "Linear session names no repository and none is a default; starting nothing",
                extra={"linear_issue": self.issue.id},
            )
            return _declined("no_repository")
        if not common.is_repo_allowed(repo):
            common.logger.warning(
                "Linear session names a repository outside the allowlist",
                extra={"linear_issue": self.issue.id},
            )
            return _declined("repository_not_allowed", repository=f"{repo['owner']}/{repo['name']}")
        return repo, workspace

    @staticmethod
    async def _personal_default(
        author: User, thread_workspace: str | None
    ) -> tuple[dict[str, str] | None, str | None]:
        """The author's default for the thread's workspace, else their global one."""
        workspace = thread_workspace or (await resolve_workspace(login=author.github_login)).slug
        personal = await author.default_repo(workspace, fallback_global=False)
        if personal:
            return personal, workspace
        return await author.default_repo(), None

    async def _signed_in_author(self) -> User | LinearSessionStart:
        """The Open SWE user who wrote the turn, if they can run as themselves on GitHub.

        Linear only says who wrote it by email, so an author without one, or whose
        email no Open SWE sign-in carries, is asked to sign in, as Slack asks.
        """
        user = await User.for_email(self.author.email) if self.author.email else None
        reason: common.AccountLinkReason = "unlinked"
        if user is not None:
            gap = await common.account_link_reason(user.github_login)
            if gap is None:
                return user
            reason = gap
        common.logger.info(
            "Linear session author can't run as themselves; asking them to sign in",
            extra={
                "linear_issue": self.issue.id,
                "author_has_email": bool(self.author.email),
                "author_known": user is not None,
                "account_link_reason": reason,
            },
        )
        return _declined(
            "sign_in_again" if reason == "revoked" else "sign_in",
            settings_url=build_settings_url(),
        )

    async def _thread(self) -> common.ThreadRecord:
        """The session's thread as its earlier turns left it; a failed read starts afresh."""
        try:
            return await common.ThreadRecord.read(self.thread_id)
        except Exception:
            common.logger.warning(
                "Reading a Linear session's thread failed; resolving it as new",
                exc_info=True,
                extra={"agent_thread_id": self.thread_id},
            )
            return common.ThreadRecord(exists=False, metadata={})

    async def _vision_model(self, login: str, workspace: str | None) -> tuple[str, str] | None:
        """The model to run with when the turn shows images the configured one can't see."""
        shown = [
            *extract_image_urls(self.issue.description or ""),
            *extract_image_urls(self.turn.body),
        ]
        if not shown:
            return None
        model_id = await common.resolve_agent_model_id(login, workspace=workspace)
        if common.model_supports_images(model_id):
            return None
        return common.default_vision_model_pair()

    async def start(self) -> LinearSessionStart:
        """Index the session's thread and plan the run for its author, or say why not."""
        author = await self._signed_in_author()
        if not isinstance(author, User):
            return author
        thread = await self._thread()
        chosen = await self.repository(thread, author)
        if not isinstance(chosen, tuple):
            return chosen
        repo, default_workspace = chosen
        login = author.github_login
        workspace = (
            thread.workspace or default_workspace or await common.workspace_for_repo_config(repo)
        )
        linear_issue: dict[str, JsonValue] = {
            "id": self.issue.id,
            "title": self.title,
            "url": self.issue.url,
            "identifier": self.issue.identifier,
            "triggering_user_name": self.author.name,
        }
        configurable: dict[str, JsonValue] = {
            "repo": cast(dict[str, JsonValue], repo),
            "linear_issue": linear_issue,
            "user_email": self.author.email,
            "source": "linear",
            "workspace": workspace,
            "environment": workspace,
            "github_login": login,
        }
        if vision := await self._vision_model(login, workspace):
            configurable["agent_model_id"], configurable["agent_effort"] = vision

        # First, so the metadata write below doesn't create the thread on MDA.
        await adopt_channel_run(self.thread_id, LINEAR_CHANNEL)
        await common.upsert_agent_thread_metadata(
            self.thread_id,
            source="linear",
            repo_config=repo,
            github_login=login,
            user_email=self.author.email,
            title=self.title,
            source_context=SourceContext.parse({"linear_issue": linear_issue}),
            workspace=workspace,
        )
        run_config = await prepare_durable_run(
            "agent",
            {"configurable": configurable},
            {**common.AGENT_VERSION_METADATA, CHANNEL_METADATA_KEY: LINEAR_CHANNEL},
            source="linear",
        )
        rewritten = await RUNTIMES.mda_runtime.run_payload(
            self.thread_id,
            {
                "assistant_id": "agent",
                "config": cast(JsonValue, run_config),
                # So the attribution the run's hooks read rides in its run token.
                "metadata": cast(JsonValue, run_config["metadata"]),
            },
            # A channel thread has no Slack thread to link.
            thread_metadata={},
        )
        run = LinearSessionRun(
            repository=f"{repo['owner']}/{repo['name']}",
            title=self.title,
            context=cast(dict[str, JsonValue], rewritten["context"]),
            metadata=channel_run_metadata(run_config["metadata"]),
            webhook=COMPLETION_WEBHOOK_URL or None,
        )
        await channel_run_starting(self.thread_id)
        return LinearSessionStart(answer=run)
