"""The coding agent, as Managed Deep Agents runs play it."""

import logging
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from openswe.agent_api.graph import RemoteGraph
from openswe.agent_api.preparation import AgentPreparation
from openswe.agent_api.run_parts import RunParts
from openswe.github.thread_token import get_github_token
from openswe.github.token import resolve_github_token
from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.server import (
    PrepareAgentRunMiddleware,
    ThreadFiles,
    agent_preparation,
    agent_tools_by_name,
    integration_tools,
    plan_agent,
)
from openswe.skill_store.run_skills import RunSkillStores

logger = logging.getLogger(__name__)

ASSISTANT_ID: Final = "agent"
_TRANSCRIPT: Final = TranscriptMiddleware()


class AgentParts(RunParts):
    """The agent's pieces, as its plan builds them, for a call answered without it."""

    reporters = (_TRANSCRIPT, record_run_usage)
    shell_guards = (PullRequestCreationGuardMiddleware(), WorkflowPushGuardMiddleware())

    def preparation_plan(self, surface: ToolSurface) -> AgentPreparation | None:
        return next(
            (
                middleware.planning.bounded()
                for middleware in surface.middleware
                if isinstance(middleware, PrepareAgentRunMiddleware)
                and middleware.planning is not None
            ),
            None,
        )

    async def preparation(self, config: RunnableConfig, plan: AgentPreparation) -> ToolSurface:
        return await agent_preparation(config, plan)

    def legacy(self, cfg: RunConfig) -> bool:
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


AGENT: Final = RemoteGraph(ASSISTANT_ID, plan_agent, AgentParts())
