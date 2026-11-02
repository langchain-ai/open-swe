"""The kinds of run Open SWE starts on Managed Deep Agents: the coding agent, the pull
request reviewer and the review scout, each named by the run token's assistant id.

MDA plays every kind with its one graph. Each kind names, for the backend: the catalog
it resolves from the backend's data as a run starts, what readies each turn and what
its start records, the recorders that hear of its replies and tool calls, the guards on
its shell commands, and how it reaches its tools by name, the thread's files, its
skills, its incident and its GitHub credentials.
"""

from collections.abc import Sequence
from typing import ClassVar

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.loop import RunSender, RunStarted

from openswe.agent_api.tokens import RunSettings
from openswe.incidents.runtime import IncidentSession
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.skill_store.run_skills import RunSkillStores


class RunKind:
    """A kind of run, as the backend answers its calls: each kind overrides its own.

    Everything here is built per call from the run's token and the request.
    """

    # The kind, as dispatch names it.
    assistant_id: ClassVar[str]
    # Whether the kind reviews a pull request: it reaches only that repository, read-only,
    # and tells the pull request itself when its sandbox is gone.
    reviews: ClassVar[bool] = False
    # What records the run's replies, tool calls and end: its transcript and usage. Each
    # keeps its run's state by the run's ids.
    reporters: Sequence[AgentMiddleware] = ()
    # The guards on the shell commands the run's tools run.
    shell_guards: Sequence[AgentMiddleware] = ()

    async def catalog(self, config: RunnableConfig, surface: ToolSurface) -> None:
        """Resolve a starting run's tools, models, subagents and loop rules into ``surface``,
        from what only the backend holds: the thread's settings, the workspace's defaults,
        the sender's access, and the integrations their credentials reach."""
        raise NotImplementedError

    def restore(self, state: dict[str, object]) -> None:
        """Rebuild what does not survive the state's trip to MDA and back."""

    async def preparation(
        self,
        config: RunnableConfig,  # noqa: ARG002
        settings: RunSettings,  # noqa: ARG002
        *,
        sandbox_id: str | None,  # noqa: ARG002
    ) -> Sequence[AgentMiddleware]:
        """What readies a turn of the run, which MDA opened ``sandbox_id`` for."""
        return ()

    def sender(self, config: RunnableConfig, settings: RunSettings) -> RunSender | None:  # noqa: ARG002
        """Who the run's invocation is attributed to; None where the kind records no start."""
        return None

    async def started(
        self,
        config: RunnableConfig,  # noqa: ARG002
        settings: RunSettings,  # noqa: ARG002
        started: RunStarted,  # noqa: ARG002
        *,
        carried: bool,  # noqa: ARG002
    ) -> None:
        """Record what the run's turn runs as, once it began; ``carried`` when this run
        sends the start of an earlier invocation, whose sender ``started`` names."""

    async def credentials(self, config: RunnableConfig) -> None:
        """Make the run's GitHub token ready in this process for its tools."""

    async def skills(self, config: RunnableConfig) -> RunSkillStores | None:  # noqa: ARG002
        return None

    async def files(self, config: RunnableConfig) -> BackendProtocol | None:  # noqa: ARG002
        """The thread's files as the run's tools reach them; None when it has none here."""
        return None

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        """The tool behind the operation ``name``."""
        return None

    async def incident(self, config: RunnableConfig) -> IncidentSession | None:  # noqa: ARG002
        """The incident the run investigates; None for a run that investigates none."""
        return None
