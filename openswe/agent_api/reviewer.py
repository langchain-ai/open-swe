"""The pull request reviewer, as Managed Deep Agents runs play it."""

import logging
from functools import cache
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.run_parts import RunParts
from openswe.github.thread_token import get_github_token
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.review.diff import compute_diff_line_set
from openswe.reviewer import (
    ensure_reviewer_github_token,
    plan_reviewer,
    reviewer_box,
    reviewer_tools,
)
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)

ASSISTANT_ID: Final = "reviewer"


@cache
def _tools_by_name() -> dict[str, BaseTool]:
    return {tool.name: tool for tool in reviewer_tools()}


class ReviewerParts(RunParts):
    """The reviewer's pieces, as its plan builds them, for a call answered without it."""

    reporters = (TranscriptMiddleware(), record_run_usage)

    def restore(self, state: dict[str, object]) -> None:
        # Sets do not cross to MDA and back; the diff they index does.
        diff_text = state.get("diff_text")
        if isinstance(diff_text, str) and diff_text and "diff_line_set" not in state:
            state["diff_line_set"] = compute_diff_line_set(diff_text)

    async def credentials(self, config: RunnableConfig) -> None:
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is None or get_github_token(config) is not None:
            return
        try:
            await ensure_reviewer_github_token(cfg.thread_id, cfg)
        except Exception:
            # A tool that needs the token says so itself; settling the check must still run.
            logger.warning("Minting the reviewer's GitHub token failed", exc_info=True)

    async def files(self, config: RunnableConfig) -> BackendProtocol | None:
        cfg = RunConfig.from_config(config)
        return None if cfg.thread_id is None else reviewer_box(cfg.thread_id, cfg)

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        return _tools_by_name().get(name)


REVIEWER: Final = RemoteGraph(ASSISTANT_ID, plan_reviewer, ReviewerParts())
