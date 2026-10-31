"""Build a linked pull request's walkthrough in its implementation thread."""

from dataclasses import dataclass

from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import PullRequestClient
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import ensure_sandbox_for_thread
from openswe.slack.client import parse_github_pr_url
from openswe.threads.store import THREADS
from openswe.tools.plan_walkthrough import PLANNING_ERRORS
from openswe.ui_invalidations import Topic
from openswe.walkthrough.checkout import PinnedCheckout
from openswe.walkthrough.plan import FileHunks, FileSplits
from openswe.walkthrough.planner import PlannerUnavailableError, PlanWorkspace
from openswe.walkthrough.record import PlanMovedError


@dataclass
class ImplementationWalkthrough:
    workspace: PlanWorkspace

    @classmethod
    async def open(cls, pr_url: str) -> ImplementationWalkthrough:
        cfg = RunConfig.from_runtime()
        if not cfg.thread_id:
            raise PlannerUnavailableError("an implementation thread is required")
        settings = await cached_workspace_settings(cfg.workspace_slug)
        if settings.get("implementation_walkthrough_enabled") is not True:
            raise PlannerUnavailableError("implementation walkthroughs are disabled")
        ref = parse_github_pr_url(pr_url)
        thread = await THREADS.get(cfg.thread_id)
        if ref is None or thread is None:
            raise PlannerUnavailableError("a linked pull request is required")
        urls = thread.metadata.get("pr_urls")
        linked = isinstance(urls, list) and pr_url in urls
        if pr_url != thread.metadata.get("pr_url") and not linked:
            raise PlannerUnavailableError("the pull request must be linked to this thread first")
        async with PullRequestClient.as_app(ref.owner, ref.repo, ref.number) as pull:
            head_sha = await pull.head_sha()
        backend = await ensure_sandbox_for_thread(cfg.thread_id, workspace_slug=cfg.workspace_slug)
        checkout = await PinnedCheckout.locate(backend, ref.repo)
        await checkout.pin(
            full_name=f"{ref.owner}/{ref.repo}", pr_number=ref.number, head_sha=head_sha
        )
        workspace = await PlanWorkspace.locate(
            checkout.backend, owner=ref.owner, repo=ref.repo, number=ref.number
        )
        return cls(workspace)

    def report(self) -> dict[str, object]:
        return {
            "success": True,
            "complete": self.workspace.complete,
            "head_sha": self.workspace.head_sha,
            "plan": self.workspace.status().model_dump(),
            "diff_command": "git diff --no-color refs/walkthrough/merge-base refs/walkthrough/head",
        }


async def build_pr_walkthrough(
    pr_url: str,
    title: str = "",
    show: list[FileHunks] | None = None,
    explanation: str = "",
    other: list[FileHunks] | None = None,
    splits: list[FileSplits] | None = None,
    other_summary: str | None = None,
    after: int | None = None,
    replace: list[int] | None = None,
) -> dict[str, object]:
    """Plan a linked PR in this thread, returning the remaining hunks after each edit."""
    try:
        planner = await ImplementationWalkthrough.open(pr_url)
        workspace = planner.workspace
        if splits:
            await workspace.split(splits)
        if show:
            await workspace.plan_chunk(
                title=title,
                show=show,
                explanation=explanation,
                other=other,
                after=after,
                replace=replace,
            )
        elif other:
            await workspace.move_to_other(other)
        if other_summary is not None:
            await workspace.describe_other(other_summary)
        pr = workspace.pull_request
        await Topic.PULL_REQUESTS.invalidate(key=PullRequestKey.of(pr.owner, pr.repo, pr.number))
        return {**planner.report(), "guidance": prompt("walkthrough/planning")}
    except (PlanMovedError, *PLANNING_ERRORS) as exc:
        return {"success": False, "error": str(exc)}
