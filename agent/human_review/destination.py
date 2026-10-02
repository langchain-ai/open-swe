"""Resolve the default review broadcast destination."""

from agent.github.repo_files import RepoSettings
from agent.slack.client import GitHubPrRef
from agent.workspaces.routing import resolve_workspace
from agent.workspaces.store import WORKSPACES


async def default_review_channel(
    pr_ref: GitHubPrRef,
    *,
    token: str,
    head_sha: str,
    workspace: str | None = None,
    slack_channel_id: str = "",
    login: str | None = None,
) -> str:
    """Prefer the resolved workspace's destination to the repository's setting."""
    resolved = await resolve_workspace(
        thread_workspace=workspace,
        slack_channel_id=slack_channel_id,
        repo=(pr_ref.owner, pr_ref.repo),
        login=login,
    )
    definition = await WORKSPACES.get(resolved.slug)
    if definition and definition.review_channel_id:
        return definition.review_channel_id
    return (
        await RepoSettings.fetch(pr_ref.owner, pr_ref.repo, token=token, ref=head_sha)
    ).review_channel.strip()
