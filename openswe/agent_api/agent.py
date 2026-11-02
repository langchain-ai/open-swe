"""The coding agent, as Managed Deep Agents runs play it."""

import logging
from typing import Final, cast

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.loop import RunSender, RunStarted

from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.tokens import RunSettings
from openswe.github.thread_token import get_github_token
from openswe.github.token import resolve_github_token
from openswe.incidents.runtime import (
    INCIDENT_SOURCE,
    INCIDENT_TOOL_NAMES,
    IncidentSession,
    load_incident_session,
)
from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware
from openswe.prompts import apply_tool_descriptions
from openswe.review_guide.middleware import ReviewGuideMiddleware
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.server import (
    PrepareAgentRunMiddleware,
    ThreadFiles,
    agent_catalog,
    agent_sender,
    agent_tools_by_name,
    client,
    integration_tools,
    record_agent_run,
)
from openswe.skill_store.run_skills import RunSkillStores
from openswe.tools.review_walkthrough import walkthrough_tools
from openswe.utils.thread_settings import load_thread_settings

logger = logging.getLogger(__name__)

_TRANSCRIPT: Final = TranscriptMiddleware()


class AgentKind(RunKind):
    """The coding agent's pieces."""

    assistant_id = "agent"
    reporters = (_TRANSCRIPT, record_run_usage)
    shell_guards = (PullRequestCreationGuardMiddleware(), WorkflowPushGuardMiddleware())

    async def catalog(self, config: RunnableConfig, surface: ToolSurface) -> None:
        await agent_catalog(config, surface)

    async def preparation(
        self, config: RunnableConfig, settings: RunSettings, *, sandbox_id: str | None
    ) -> list[AgentMiddleware]:
        turn = settings.agent
        if turn is None:
            raise ValueError("An agent run's settings name its turn")
        thread_id = RunConfig.from_config(config).thread_id
        if thread_id is None:
            raise ValueError("An agent run needs a thread")
        stored = await load_thread_settings(client, thread_id)
        repo_instructions = stored.get("repo_instructions")
        prepare = PrepareAgentRunMiddleware(
            config,
            turn,
            repo_instructions=repo_instructions if isinstance(repo_instructions, str) else None,
            sandbox_id=None if turn.reaches_sandbox else sandbox_id,
        )
        if not (turn.review_guide or turn.review_guide_closed):
            return [prepare]
        cfg = RunConfig.from_config(config)
        guide = ReviewGuideMiddleware(thread_id=thread_id, approve_ts=cfg.review_guide_approve_ts)
        # Middleware types are invariant in the state they extend; a graph merges them all.
        return cast(list[AgentMiddleware], [prepare, guide])

    def sender(self, config: RunnableConfig, settings: RunSettings) -> RunSender | None:
        if settings.agent is None:
            raise ValueError("An agent run's settings name its turn")
        return agent_sender(config, settings.agent)

    async def started(
        self, config: RunnableConfig, settings: RunSettings, started: RunStarted, *, carried: bool
    ) -> None:
        cfg = RunConfig.from_config(config)
        if settings.agent is None or cfg.thread_id is None:
            return
        try:
            await record_agent_run(config, settings.agent, started, carried=carried)
        except Exception:
            # Bookkeeping: the turn goes on, and the reports behind this one with it.
            logger.warning(
                "Recording an agent turn's start failed",
                extra={"agent_thread_id": cfg.thread_id},
                exc_info=True,
            )

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
        cfg = RunConfig.from_config(config)
        if name in INCIDENT_TOOL_NAMES:
            incident = await self.incident(config)
            return None if incident is None else next(t for t in incident.tools if t.name == name)
        if cfg.thread_id and name.startswith("walkthrough_"):
            guide = await ReviewGuideSession.get(cfg.thread_id)
            if guide is not None and not guide.closed:
                tools = apply_tool_descriptions(walkthrough_tools(guide.mode))
                return next((tool for tool in tools if tool.name == name), None)
        static = agent_tools_by_name().get(name)
        return static if static is not None else (await integration_tools(config)).get(name)

    async def incident(self, config: RunnableConfig) -> IncidentSession | None:
        cfg = RunConfig.from_config(config)
        if cfg.source != INCIDENT_SOURCE:
            return None
        return await load_incident_session(config, run_id=cfg.run_id)
