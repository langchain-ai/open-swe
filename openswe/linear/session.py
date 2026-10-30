"""What only the backend knows about a Linear agent session turn, and the run it signs.

MDA's Linear channel reads the turn, picks the repository and workspace, writes
the run's input and starts it on the session's own thread, so the conversation's
reply, Stop and errors reach Linear. It asks here for Open SWE's data
(``linear_session_facts``): whether the author has signed in to Open SWE, which
acts on GitHub as them; what the session's thread already works in; the author's
and the instance's default repositories; and which repositories Open SWE may work
in. With its choice made, it asks for the run (``plan_linear_session``): the
backend checks the author and the repository again, since the channel's choice
gives no access, indexes the thread for the dashboard and signs the run's context,
which carries the backend's run-complete webhook. Each agent session is its own
thread, so a second session on the same issue is a new conversation, with the
issue's title.
"""

from typing import cast

from pydantic import JsonValue
from runtime_api.linear import (
    AuthorStanding,
    DeclineReason,
    LinearSessionDeclined,
    LinearSessionFacts,
    LinearSessionFactsRequest,
    LinearSessionRun,
    LinearSessionStart,
    PlanLinearSession,
    RepositoryChoice,
    RepositoryPolicy,
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


def _full_name(repo: dict[str, str] | None) -> str | None:
    return f"{repo['owner']}/{repo['name']}" if repo else None


def _repo_config(full_name: str) -> dict[str, str]:
    owner, name = full_name.split("/", 1)
    return {"owner": owner, "name": name}


async def _author(email: str) -> tuple[User | None, AuthorStanding]:
    """The Open SWE user with ``email``, and whether they can run as themselves on GitHub.

    Linear only says who wrote a turn by email, so an author without one, or whose
    email no Open SWE sign-in carries, must sign in, as Slack asks.
    """
    user = await User.for_email(email) if email else None
    if user is None:
        return None, "sign_in"
    gap = await common.account_link_reason(user.github_login)
    if gap is None:
        return user, "signed_in"
    return user, "sign_in_again" if gap == "revoked" else "sign_in"


async def _personal_default(user: User, thread_workspace: str | None) -> RepositoryChoice | None:
    """The author's default for the thread's workspace, else their global one."""
    try:
        workspace = thread_workspace or (await resolve_workspace(login=user.github_login)).slug
        personal = await user.default_repo(workspace, fallback_global=False)
        if personal:
            return RepositoryChoice(
                repository=f"{personal['owner']}/{personal['name']}", workspace=workspace
            )
        fallback = await user.default_repo()
    except Exception:
        common.logger.exception("Reading a Linear user's default repository failed")
        return None
    if not fallback:
        return None
    return RepositoryChoice(repository=f"{fallback['owner']}/{fallback['name']}")


async def linear_session_facts(request: LinearSessionFactsRequest) -> LinearSessionFacts:
    """What Open SWE knows about a session turn, for the channel to pick its repository."""
    user, standing = await _author(request.author_email)
    if user is None or standing != "signed_in":
        common.logger.info(
            "Linear session author can't run as themselves; the channel asks them to sign in",
            extra={
                "author_has_email": bool(request.author_email),
                "author_known": user is not None,
            },
        )
        return LinearSessionFacts(
            author=standing, settings_url=build_settings_url(), policy=_policy()
        )
    # A thread it can't read fails the turn: read as new, it would move the session's
    # repository.
    thread = await common.ThreadRecord.read(str(request.thread_id))
    return LinearSessionFacts(
        author=standing,
        settings_url=build_settings_url(),
        thread_repository=_full_name(thread.repo),
        thread_workspace=thread.workspace,
        personal_default=await _personal_default(user, thread.workspace),
        instance_default=_full_name((await common.get_workspace_settings()).default_repo),
        policy=_policy(),
    )


def _policy() -> RepositoryPolicy:
    return RepositoryPolicy(
        default_owner=common.DEFAULT_REPO_OWNER or None,
        allowed_orgs=sorted(common.ALLOWED_GITHUB_ORGS),
        allowed_repos=sorted(common.ALLOWED_GITHUB_REPOS),
    )


def _declined(
    reason: DeclineReason, *, repository: str | None = None, settings_url: str | None = None
) -> LinearSessionStart:
    return LinearSessionStart(
        answer=LinearSessionDeclined(
            reason=reason, repository=repository, settings_url=settings_url
        )
    )


async def plan_linear_session(plan: PlanLinearSession) -> LinearSessionStart:
    """Index the session's thread and sign its run, once the author and repository check out."""
    turn = plan.turn
    thread_id = str(turn.thread_id)
    user, standing = await _author(turn.author.email)
    if user is None or standing != "signed_in":
        reason: DeclineReason = "sign_in_again" if standing == "sign_in_again" else "sign_in"
        return _declined(reason, settings_url=build_settings_url())
    repo = _repo_config(plan.repository)
    if not common.is_repo_allowed(repo):
        common.logger.warning(
            "Linear session names a repository outside the allowlist",
            extra={"linear_issue": turn.issue.id},
        )
        return _declined("repository_not_allowed", repository=plan.repository)
    login = user.github_login
    workspace = plan.workspace or await common.workspace_for_repo_config(repo)
    title = turn.issue.title or turn.issue.identifier or "Linear issue"
    linear_issue: dict[str, JsonValue] = {
        "id": turn.issue.id,
        "title": title,
        "url": turn.issue.url,
        "identifier": turn.issue.identifier,
        "triggering_user_name": turn.author.name,
    }
    configurable: dict[str, JsonValue] = {
        "repo": cast(dict[str, JsonValue], repo),
        "linear_issue": linear_issue,
        "user_email": turn.author.email,
        "source": "linear",
        "workspace": workspace,
        "environment": workspace,
        "github_login": login,
    }

    # First, so the metadata write below doesn't create the thread on MDA.
    await adopt_channel_run(thread_id, LINEAR_CHANNEL)
    await common.upsert_agent_thread_metadata(
        thread_id,
        source="linear",
        repo_config=repo,
        github_login=login,
        user_email=turn.author.email,
        title=title,
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
        thread_id,
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
        repository=plan.repository,
        title=title,
        context=cast(dict[str, JsonValue], rewritten["context"]),
        metadata=channel_run_metadata(run_config["metadata"]),
        webhook=COMPLETION_WEBHOOK_URL or None,
    )
    await channel_run_starting(thread_id)
    return LinearSessionStart(answer=run)
