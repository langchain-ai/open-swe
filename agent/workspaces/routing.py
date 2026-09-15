"""Which workspace an inbound event belongs to.

Resolution order, first match wins: the thread's recorded workspace, a
``workspace:<slug>`` tag on the opening message, the repository's owner, the
Slack channel's owner, the user's default, then ``default``. The order is the
one OEP-0003 specifies; callers never guess on their own.

A storage failure is not an answer: "nothing owns this repository" and "we
could not find out" lead to opposite decisions, so the lookups here never
report an unreadable binding as a missing one. They raise
:class:`WorkspaceLookupError` internally, and this module decides per entry
point who sees it. :func:`repo_is_routable` propagates, because the GitHub
webhook route can answer 503 and have the delivery retried; a false answer
there would drop it for good. Everything else — :func:`workspace_for_repo`,
:func:`workspace_for_slack_channel`, :func:`resolve_workspace` — falls back to
``default`` and logs at error, since those decide where work runs and a run in
``default`` beats no run.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from agent.config import ENV
from agent.dashboard.user_preferences import get_user_preferences
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES, slugify

logger = logging.getLogger(__name__)

ResolvedBy = Literal["thread", "tag", "repo", "channel", "user_default", "instance_default"]


class WorkspaceLookupError(RuntimeError):
    """Workspaces could not be read, so ownership is unknown."""


@dataclass(frozen=True)
class WorkspaceResolution:
    slug: str
    resolved_by: ResolvedBy


async def _slug_exists(slug: str) -> bool:
    """Whether this name is a workspace; raises when the lookup itself fails."""
    try:
        return await WORKSPACES.slug_exists(slug)
    except Exception as exc:
        raise WorkspaceLookupError("workspace slug lookup failed") from exc


async def _repo_owner(owner: str, name: str) -> str | None:
    """The workspace owning this repository; raises when ownership is unreadable.

    A repository that no workspace owns but a not-yet-imported Store record
    names is unreadable too: its owner exists, just not in PostgreSQL yet.
    """
    full_name = f"{owner}/{name}"
    try:
        found = await WORKSPACES.owner_of_repo(full_name)
    except Exception as exc:
        raise WorkspaceLookupError("workspace repository lookup failed") from exc
    if found is None and WORKSPACES.repo_import_is_pending(full_name):
        raise WorkspaceLookupError("the workspace owning this repository has not been imported yet")
    return found


async def _channel_owner(channel_id: str) -> str | None:
    """The workspace this Slack channel is bound to; raises when unreadable."""
    try:
        return await WORKSPACES.owner_of_slack_channel(channel_id)
    except Exception as exc:
        raise WorkspaceLookupError("workspace Slack channel lookup failed") from exc


async def workspace_for_repo(owner: str, name: str) -> str | None:
    """Which workspace owns this repository, or ``None`` when none does.

    A failed lookup reads as ``None`` as well, logged at error: every caller of
    this is picking where work runs or labelling work that already ran, and
    landing in ``default`` beats refusing to answer. :func:`repo_is_routable`
    is the one place that trade goes the other way.
    """
    try:
        return await _repo_owner(owner, name)
    except WorkspaceLookupError:
        logger.error(
            "workspace lookup failed for a repository; treating it as unowned",
            extra={"repository": f"{owner}/{name}"},
            exc_info=True,
        )
        return None


async def workspace_for_slack_channel(channel_id: str) -> str | None:
    """Which workspace this Slack channel is bound to, if any.

    Fails soft like :func:`workspace_for_repo`: a failed lookup reads as an
    unbound channel.
    """
    try:
        return await _channel_owner(channel_id)
    except WorkspaceLookupError:
        logger.error(
            "workspace lookup failed for a Slack channel; treating it as unbound",
            extra={"slack_channel_id": channel_id},
            exc_info=True,
        )
        return None


def _unassigned_policy() -> str:
    value = ENV.OPEN_SWE_UNASSIGNED_REPO_WORKSPACE.get("default").strip().lower()
    return value if value in ("default", "ignore") else "default"


async def repo_is_routable(owner: str, name: str) -> bool:
    """Whether a GitHub event for this repository should be handled at all.

    Raises :class:`WorkspaceLookupError` when ownership cannot be read, and when
    the workspaces are not populated at all: under the ``ignore`` policy a false
    answer drops the delivery for good, and GitHub only retries a 5xx. A startup
    import that failed leaves an empty table behind, in which every repository
    reads as unowned, so "no workspaces yet" is not an answer either.
    """
    if await _repo_owner(owner, name) is not None:
        return True
    try:
        populated = await WORKSPACES.routing_is_populated()
    except Exception as exc:
        raise WorkspaceLookupError("workspace population lookup failed") from exc
    if not populated:
        raise WorkspaceLookupError("workspaces have not been imported into PostgreSQL yet")
    return _unassigned_policy() == "default"


async def _resolve_from_store(
    *,
    tag: str | None,
    repo: tuple[str, str] | None,
    slack_channel_id: str | None,
    login: str | None,
) -> WorkspaceResolution | None:
    if tag:
        try:
            tagged = slugify(tag)
        except ValueError:
            tagged = ""
        if tagged and await _slug_exists(tagged):
            return WorkspaceResolution(tagged, "tag")
    if repo is not None:
        owner = await _repo_owner(*repo)
        if owner is not None:
            return WorkspaceResolution(owner, "repo")
    if slack_channel_id:
        owner = await _channel_owner(slack_channel_id)
        if owner is not None:
            return WorkspaceResolution(owner, "channel")
    if login:
        preferred = (await get_user_preferences(login)).get("default_workspace")
        if isinstance(preferred, str) and preferred and await _slug_exists(preferred):
            return WorkspaceResolution(preferred, "user_default")
    return None


async def resolve_workspace(
    *,
    thread_workspace: str | None = None,
    tag: str | None = None,
    repo: tuple[str, str] | None = None,
    slack_channel_id: str | None = None,
    login: str | None = None,
) -> WorkspaceResolution:
    if thread_workspace and thread_workspace.strip():
        return WorkspaceResolution(thread_workspace.strip(), "thread")
    try:
        resolved = await _resolve_from_store(
            tag=tag, repo=repo, slack_channel_id=slack_channel_id, login=login
        )
    except WorkspaceLookupError:
        # Fail soft here on purpose: this decides where work runs, and a run in
        # `default` beats no run. The GitHub route fails closed instead, since a
        # delivery it drops is never retried.
        logger.error("workspace routing failed; using the instance default", exc_info=True)
        return WorkspaceResolution(DEFAULT_WORKSPACE_SLUG, "instance_default")
    return resolved or WorkspaceResolution(DEFAULT_WORKSPACE_SLUG, "instance_default")
