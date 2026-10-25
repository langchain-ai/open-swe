"""The coding agent, as Managed Deep Agents runs play it."""

import logging
from collections.abc import Sequence
from typing import Final, cast

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.loop import RunLoop
from openswe.agent_api.run_parts import RunParts
from openswe.github.thread_token import get_github_token
from openswe.github.token import resolve_github_token
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.middleware.model_selection import ModelSelectionMiddleware
from openswe.middleware.notify_step_limit import notify_step_limit_reached
from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
from openswe.middleware.prepare_run import RenderedSystemPromptMiddleware
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model
from openswe.middleware.require_cli_result import RequireCliResultMiddleware
from openswe.middleware.require_user_reply import WEB_REPLY_SURFACE, RequireUserReplyMiddleware
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware
from openswe.review_guide.middleware import ReviewGuideMiddleware
from openswe.run_config import RunConfig
from openswe.server import ThreadFiles, agent_tools_by_name, integration_tools, plan_agent
from openswe.skill_store.run_skills import RunSkillStores

logger = logging.getLogger(__name__)

ASSISTANT_ID: Final = "agent"
_TRANSCRIPT: Final = TranscriptMiddleware()


class AgentParts(RunParts):
    """The agent's pieces, as its plan builds them, for a call answered without it."""

    reporters = (_TRANSCRIPT, record_run_usage)
    shell_guards = (PullRequestCreationGuardMiddleware(), WorkflowPushGuardMiddleware())

    def needs_plan(self, cfg: RunConfig) -> bool:
        # A requested offload needs the conversation's model, and an incident the
        # evidence its plan collects in memory.
        return cfg.offload_conversation or cfg.source == "incidents_agent"

    async def credentials(self, config: RunnableConfig) -> None:
        thread_id = RunConfig.from_config(config).thread_id
        if thread_id is None or get_github_token(config) is not None:
            return
        try:
            await resolve_github_token(config, thread_id)
        except Exception:
            # A tool that needs the token says so itself.
            logger.warning("Resolving the run's GitHub token failed", exc_info=True)

    async def skills(self, config: RunnableConfig) -> RunSkillStores:
        return await RunSkillStores.for_run(config)

    async def files(self, config: RunnableConfig) -> BackendProtocol:
        return (await ThreadFiles.for_run(config)).backend

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:
        static = agent_tools_by_name().get(name)
        return static if static is not None else (await integration_tools(config)).get(name)

    async def loop(self, config: RunnableConfig, rules: LoopRules, models: RunModels) -> RunLoop:
        """The agent's hooks around each model call, in the order its plan has them."""
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is None:
            raise ValueError("An agent run needs a thread")
        coordination = (
            await TaskCoordinationMiddleware.for_thread(cfg.thread_id)
            if rules.task_coordination
            else None
        )
        guide = (
            [ReviewGuideMiddleware(thread_id=cfg.thread_id, approve_ts=cfg.review_guide_approve_ts)]
            if rules.review_guide
            else []
        )
        reply = (
            [
                RequireUserReplyMiddleware(
                    rules.reply.tool,
                    rules.reply.no_reply_tool,
                    # Where the run starts answering, which only preparation reads.
                    initial_surface=WEB_REPLY_SURFACE,
                    replies=frozenset(rules.reply.replies),
                )
            ]
            if rules.reply
            else []
        )
        routing = (
            [
                ModelSelectionMiddleware(
                    rules.routing.route_model_ids, routing_mode=rules.routing.mode
                )
            ]
            if rules.routing
            else []
        )
        # Middleware types are invariant in the state they extend; a run merges them all.
        middleware = cast(
            list[AgentMiddleware],
            [
                RenderedSystemPromptMiddleware(),
                *guide,
                *([ClientToolsMiddleware(cfg.client_tools)] if cfg.client_tools else []),
                ExcludeToolsMiddleware(excluded=frozenset(rules.hidden_tools)),
                *([coordination] if coordination else []),
                *(
                    []
                    if coordination and coordination.is_worker
                    else [refresh_github_proxy_before_model]
                ),
                *reply,
                *(
                    [RequireCliResultMiddleware(rules.cli_result_tool)]
                    if rules.cli_result_tool
                    else []
                ),
                *routing,
            ],
        )
        return RunLoop(middleware, models)

    def end_hooks(self, config: RunnableConfig) -> Sequence[AgentMiddleware]:  # noqa: ARG002
        return (_TRANSCRIPT, notify_step_limit_reached, record_run_usage)


AGENT: Final = RemoteGraph(ASSISTANT_ID, plan_agent, AgentParts())
