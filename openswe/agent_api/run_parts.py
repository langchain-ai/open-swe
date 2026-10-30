"""What a graph's MDA run calls are answered with: the pieces each call builds from
the run's token alone.

Each graph names its own: the recorders that hear of its replies and tool calls, the
guards on its shell commands, and how it reaches its tools by name, the thread's
files, its skills and its GitHub credentials. A run still served from its plan says
so (``legacy``).
"""

from collections.abc import Sequence

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from openswe.agent_api.preparation import AgentPreparation
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.skill_store.run_skills import RunSkillStores


class RunParts:
    """A graph's pieces, built per call from its run's token; a graph overrides its own."""

    # What records the run's replies, tool calls and end: its transcript and usage. Each
    # keeps its run's state by the run's ids.
    reporters: Sequence[AgentMiddleware] = ()
    # The guards on the shell commands the run's tools run.
    shell_guards: Sequence[AgentMiddleware] = ()

    def preparation_plan(self, surface: ToolSurface) -> AgentPreparation | None:  # noqa: ARG002
        return None

    async def preparation(
        self, config: RunnableConfig, plan: AgentPreparation
    ) -> ToolSurface | None:
        return None

    def legacy(self, cfg: RunConfig) -> bool:  # noqa: ARG002
        """Whether this run's phases and tool calls are still answered from its plan."""
        return False

    def restore(self, state: dict[str, object]) -> None:
        """Rebuild what does not survive the state's trip to MDA and back."""

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
