"""The coding agent, as Managed Deep Agents runs play it."""

from typing import Final, cast

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.loop import RunLoop
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
from openswe.middleware.model_selection import ModelSelectionMiddleware
from openswe.middleware.prepare_run import RenderedSystemPromptMiddleware
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model
from openswe.middleware.require_cli_result import RequireCliResultMiddleware
from openswe.middleware.require_user_reply import WEB_REPLY_SURFACE, RequireUserReplyMiddleware
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.review_guide.middleware import ReviewGuideMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.skill_store.run_skills import RunSkillStores

ASSISTANT_ID: Final = "agent"


async def _plan(config: RunnableConfig, surface: ToolSurface) -> None:
    from openswe.server import plan_agent

    await plan_agent(config, surface)


async def _loop(config: RunnableConfig, rules: LoopRules, models: RunModels) -> RunLoop | None:
    """The agent's hooks around each model call, in the order its plan has them."""
    cfg = RunConfig.from_config(config)
    if cfg.offload_conversation or cfg.source == "incidents_agent" or cfg.thread_id is None:
        # A requested offload needs the conversation's model and files, and an incident
        # the evidence its plan collects.
        return None
    coordination = (
        await TaskCoordinationMiddleware.for_thread(cfg.thread_id)
        if rules.task_coordination
        else None
    )
    # Middleware types are invariant in the state they extend; a run merges them all.
    middleware = cast(
        list[AgentMiddleware],
        [
            RenderedSystemPromptMiddleware(),
            *(
                [
                    ReviewGuideMiddleware(
                        thread_id=cfg.thread_id, approve_ts=cfg.review_guide_approve_ts
                    )
                ]
                if rules.review_guide
                else []
            ),
            *([ClientToolsMiddleware(cfg.client_tools)] if cfg.client_tools else []),
            ExcludeToolsMiddleware(excluded=frozenset(rules.hidden_tools)),
            *([coordination] if coordination else []),
            *(
                []
                if coordination and coordination.is_worker
                else [refresh_github_proxy_before_model]
            ),
            *(
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
            ),
            *([RequireCliResultMiddleware(rules.cli_result_tool)] if rules.cli_result_tool else []),
            *(
                [
                    ModelSelectionMiddleware(
                        rules.routing.route_model_ids, routing_mode=rules.routing.mode
                    )
                ]
                if rules.routing
                else []
            ),
        ],
    )
    return RunLoop(middleware, models)


AGENT: Final = RemoteGraph(
    ASSISTANT_ID,
    _plan,
    skills=RunSkillStores.for_run,
    reporters=(TranscriptMiddleware(), record_run_usage),
    loop=_loop,
)
