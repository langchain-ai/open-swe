"""Which workspace an inbound event belongs to.

Resolution order, first match wins: the thread's recorded workspace, a
``workspace:<slug>`` tag on the opening message, the repository's owner, the
Slack channel's owner, the user's default, then ``default``. The order is the
one OEP-0003 specifies; callers never guess on their own.

A store failure is not an answer. Every lookup here raises
:class:`WorkspaceLookupError` rather than reporting an empty workspace list,
because "nothing owns this repository" and "we could not find out" lead to
opposite decisions: the first may be a deliberate drop, the second must be
retried. Callers that can retry — the GitHub webhook route — propagate it;
callers that cannot fall back to ``default`` and log at error.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from agent.config import ENV
from agent.dashboard.user_preferences import get_user_preferences
from agent.utils import ttl_cache
from agent.workspaces.cache import (
    WORKSPACE_LIST_CACHE_KEY,
    WORKSPACE_LIST_CACHE_TTL_SECONDS,
)
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES, Workspace, slugify

logger = logging.getLogger(__name__)

ResolvedBy = Literal["thread", "tag", "repo", "channel", "user_default", "instance_default"]


class WorkspaceLookupError(RuntimeError):
    """The workspace list could not be read, so ownership is unknown."""


@dataclass(frozen=True)
class WorkspaceResolution:
    slug: str
    resolved_by: ResolvedBy


def invalidate_routing_cache() -> None:
    ttl_cache.invalidate(WORKSPACE_LIST_CACHE_KEY)


async def _all_workspaces() -> list[Workspace]:
    """Every workspace, cached briefly: webhooks call this on every delivery."""
    try:
        return await ttl_cache.cached(
            WORKSPACE_LIST_CACHE_KEY, WORKSPACE_LIST_CACHE_TTL_SECONDS, WORKSPACES.list_all
        )
    except Exception as exc:
        raise WorkspaceLookupError("workspace listing failed") from exc


async def _slug_exists(slug: str) -> bool:
    return any(record.slug == slug for record in await _all_workspaces())


async def workspace_for_repo(owner: str, name: str) -> str | None:
    wanted = f"{owner}/{name}".strip().lower()
    if wanted == "/":
        return None
    for record in await _all_workspaces():
        if any(repo.lower() == wanted for repo in record.repos):
            return record.slug
    return None


async def workspace_for_slack_channel(channel_id: str) -> str | None:
    wanted = (channel_id or "").strip().upper()
    if not wanted:
        return None
    for record in await _all_workspaces():
        if wanted in record.slack_channel_ids:
            return record.slug
    return None


def _unassigned_policy() -> str:
    value = ENV.OPEN_SWE_UNASSIGNED_REPO_WORKSPACE.get("default").strip().lower()
    return value if value in ("default", "ignore") else "default"


async def repo_is_routable(owner: str, name: str) -> bool:
    """Whether a GitHub event for this repository should be handled at all.

    Raises :class:`WorkspaceLookupError` when ownership cannot be read: under
    the ``ignore`` policy a false answer drops the delivery for good, and
    GitHub only retries a 5xx.
    """
    if await workspace_for_repo(owner, name) is not None:
        return True
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
        owner = await workspace_for_repo(*repo)
        if owner is not None:
            return WorkspaceResolution(owner, "repo")
    if slack_channel_id:
        owner = await workspace_for_slack_channel(slack_channel_id)
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
