"""Tools that let the review scout build the pull request's shared walkthrough plan."""

from typing import Any

from openswe.github.pull_request_key import PullRequestKey
from openswe.run_config import RunConfig
from openswe.runtime import get_cached_sandbox_backend
from openswe.ui_invalidations import Topic
from openswe.walkthrough.checkout import CheckoutError
from openswe.walkthrough.plan import FileRanges, RangeError
from openswe.walkthrough.planner import PlannerUnavailableError, PlanWorkspace
from openswe.walkthrough.record import PlanMovedError

PLANNING_ERRORS = (CheckoutError, PlannerUnavailableError, PlanMovedError, RangeError)


async def _workspace() -> PlanWorkspace:
    cfg = RunConfig.from_runtime()
    if not cfg.thread_id or cfg.repo is None or cfg.pr_number is None:
        raise PlannerUnavailableError("this run is not planning a pull request")
    return await PlanWorkspace.locate(
        get_cached_sandbox_backend(cfg.thread_id),
        owner=cfg.repo.owner,
        repo=cfg.repo.name,
        number=cfg.pr_number,
    )


async def _planned(workspace: PlanWorkspace, **result: object) -> dict[str, Any]:
    pr = workspace.pull_request
    await Topic.PULL_REQUESTS.invalidate(key=PullRequestKey.of(pr.owner, pr.repo, pr.number))
    return {"success": True, **result, "plan": workspace.status().model_dump()}


async def walkthrough_plan_chunk(
    title: str,
    show: list[FileRanges],
    explanation: str,
    other: list[FileRanges] | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Implement the `walkthrough_plan_chunk` tool."""
    try:
        workspace = await _workspace()
        number = await workspace.plan_chunk(
            title=title, show=show, explanation=explanation, other=other, after=after
        )
    except PLANNING_ERRORS as exc:
        return {"success": False, "error": str(exc)}
    return await _planned(workspace, chunk=number)


async def walkthrough_move_to_other(
    files: list[FileRanges], restore: bool = False
) -> dict[str, Any]:
    """Implement the `walkthrough_move_to_other` tool."""
    try:
        workspace = await _workspace()
        await workspace.move_to_other(files, restore=restore)
    except PLANNING_ERRORS as exc:
        return {"success": False, "error": str(exc)}
    return await _planned(workspace)


async def walkthrough_describe_other(summary: str) -> dict[str, Any]:
    """Implement the `walkthrough_describe_other` tool."""
    try:
        workspace = await _workspace()
        await workspace.describe_other(summary)
    except PLANNING_ERRORS as exc:
        return {"success": False, "error": str(exc)}
    return await _planned(workspace)
