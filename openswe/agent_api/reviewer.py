"""The pull request reviewer, as Managed Deep Agents runs play it."""

import logging
from functools import cache

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.loop import RunStarted

from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.tokens import RunSettings
from openswe.github.thread_token import get_github_token
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.review.diff import compute_diff_line_set
from openswe.review.events import ReviewEvents
from openswe.reviewer import (
    PrepareReviewerRunMiddleware,
    ensure_reviewer_github_token,
    resolve_reviewer_models,
    reviewer_catalog,
    reviewer_sandbox,
    reviewer_tools,
)
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface

logger = logging.getLogger(__name__)


@cache
def _tools_by_name() -> dict[str, BaseTool]:
    return {tool.name: tool for tool in reviewer_tools()}


class ReviewerKind(RunKind):
    """The pull request reviewer's pieces."""

    assistant_id = "reviewer"
    reviews = True
    reporters = (TranscriptMiddleware(), record_run_usage)

    async def catalog(self, config: RunnableConfig, surface: ToolSurface) -> None:
        await reviewer_catalog(config, surface)

    async def preparation(
        self, config: RunnableConfig, settings: RunSettings, *, sandbox_id: str | None
    ) -> list[AgentMiddleware]:
        del settings, sandbox_id
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is None:
            raise ValueError("A review run needs a thread")
        models = await resolve_reviewer_models(cfg)
        return [
            PrepareReviewerRunMiddleware(
                thread_id=cfg.thread_id,
                config=config,
                use_gateway=models.use_gateway,
                model=models.main,
            )
        ]

    def restore(self, state: dict[str, object]) -> None:
        # Sets do not cross to MDA and back; the diff they index does.
        diff_text = state.get("diff_text")
        if isinstance(diff_text, str) and diff_text and "diff_line_set" not in state:
            state["diff_line_set"] = compute_diff_line_set(diff_text)

    async def started(
        self, config: RunnableConfig, settings: RunSettings, started: RunStarted, *, carried: bool
    ) -> None:
        del settings, carried
        cfg = RunConfig.from_config(config)
        events = ReviewEvents.from_config(cfg)
        if events is not None and started.run_id:
            run_id = started.run_id
            await events.append(
                "run.started",
                delivery_id=f"{cfg.thread_id}:{run_id}:started",
                run_id=run_id,
                head_sha=cfg.head_sha,
                re_review=bool(cfg.re_review),
                model_id=started.model_id,
                effort=started.effort,
            )

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
        return None if cfg.thread_id is None else reviewer_sandbox(cfg.thread_id, cfg)

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        return _tools_by_name().get(name)
