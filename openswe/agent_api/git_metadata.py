"""What a turn's trace says of its git work that only the backend knows.

Who the turn acts for goes in the run's catalog. The pull requests a run's
operations open or find go back with each operation's result; the runtime
fingerprints the thread's checkouts itself and reconciles both into the trace.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from langchain_core.runnables import RunnableConfig
from pydantic import JsonValue
from runtime_api.operations import PullRequestHead

_pull_requests: ContextVar[list[PullRequestHead] | None] = ContextVar(
    "git_trace_pr_targets", default=None
)


def trace_actor(config: RunnableConfig) -> dict[str, JsonValue]:
    """Who the turn's git work is attributed to."""
    metadata: dict[str, JsonValue] = {"ls_repo_provider": "github"}
    attribution = config.get("metadata", {}).get("ls_attribution_identifier")
    if isinstance(attribution, str) and attribution:
        metadata["ls_attribution_identifier"] = attribution
    return metadata


@contextmanager
def collect_pr_targets() -> Iterator[list[PullRequestHead]]:
    """The pull requests the block's operation records with ``record_pr_target``."""
    heads: list[PullRequestHead] = []
    token = _pull_requests.set(heads)
    try:
        yield heads
    finally:
        _pull_requests.reset(token)


def record_pr_target(repo: str, branch: str) -> None:
    heads = _pull_requests.get()
    if heads is not None and repo and branch:
        heads.append(PullRequestHead(repo=repo, branch=branch))
