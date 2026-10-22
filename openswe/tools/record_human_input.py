"""Tool: ``record_human_input``. Records the review scout's summary of what people asked for."""

from openswe.github.pull_requests import PullRequest
from openswe.run_config import RunConfig
from openswe.walkthrough.record import Walkthrough

MAX_SUMMARY_CHARS = 2_000


async def record_human_input(summary: str) -> str:
    """Implement the `record_human_input` tool."""
    trimmed = summary.strip()[:MAX_SUMMARY_CHARS]
    if not trimmed:
        return "summary is empty; nothing recorded"
    cfg = RunConfig.from_runtime()
    if cfg.repo is None or cfg.pr_number is None:
        return "this run is not planning a pull request; nothing recorded"
    pull_request = await PullRequest.get(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
    # Preparing the run stores the plan first, so a missing one means the run never got that far.
    if pull_request is None or not await Walkthrough.set_human_input(pull_request.id, trimmed):
        return "the pull request has no walkthrough yet; nothing recorded"
    return "Recorded."
