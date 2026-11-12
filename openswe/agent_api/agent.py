"""The coding agent, as Managed Deep Agents runs play it."""

import logging
from typing import Final

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.loop import RunSender, RunStarted

from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.tokens import RunSettings
from openswe.github.thread_token import get_github_token
from openswe.github.token import resolve_github_token
from openswe.middleware.ask_requester_guard import AskRequesterGuardMiddleware
from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
from openswe.middleware.record_run_usage import record_run_usage
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware
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
from openswe.tools.page_commands import PageTools
from openswe.utils.thread_settings import load_thread_settings

logger = logging.getLogger(__name__)

_TRANSCRIPT: Final = TranscriptMiddleware()


class AgentKind(RunKind):
    """The coding agent's pieces."""

    assistant_id = "agent"
    reporters = (_TRANSCRIPT, record_run_usage)
    shell_guards = (
        PullRequestCreationGuardMiddleware(),
        WorkflowPushGuardMiddleware(),
        AskRequesterGuardMiddleware(),
    )

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
        return [prepare]

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
        if (page := PageTools.of_run(cfg)) is not None and (tool := page.tool(name)) is not None:
            return tool
        static = agent_tools_by_name().get(name)
        return static if static is not None else (await integration_tools(config)).get(name)
