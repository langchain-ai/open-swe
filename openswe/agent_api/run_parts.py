"""What a graph's MDA run calls are answered with when the run's plan is not at hand.

Every call carries the run's token, and the loop's calls the rules its catalog
planned. From those alone each graph builds the few pieces a call needs: its
hooks around a model call, its tools by name, the thread's files, its skills, the
guards on its shell commands and its end-of-run hooks. A run whose calls need more
than that says so (``needs_plan``), and is answered from its plan as before.
"""

from collections.abc import Sequence

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from runtime_api.catalog import LoopRules, RunModels

from openswe.agent_api.loop import RunLoop
from openswe.run_config import RunConfig
from openswe.skill_store.run_skills import RunSkillStores


class RunParts:
    """A graph's pieces, built per call from its run's token; a graph overrides its own."""

    # The middleware that records what MDA runs: its replies and tool calls. Each keeps
    # its run's state by the run's ids, never by the plan.
    reporters: Sequence[AgentMiddleware] = ()
    # The guards on the shell commands the run's tools run.
    shell_guards: Sequence[AgentMiddleware] = ()

    def needs_plan(self, cfg: RunConfig) -> bool:  # noqa: ARG002
        """Whether this run's calls need its plan."""
        return False

    def restore(self, state: dict[str, object]) -> None:
        """Rebuild what does not survive the state's trip to MDA and back."""

    async def credentials(self, config: RunnableConfig) -> None:
        """Make the run's GitHub token ready in this process for its tools."""

    async def skills(self, config: RunnableConfig) -> RunSkillStores | None:  # noqa: ARG002
        return None

    async def loop(
        self,
        config: RunnableConfig,  # noqa: ARG002
        rules: LoopRules,  # noqa: ARG002
        models: RunModels,  # noqa: ARG002
    ) -> RunLoop | None:
        """The hooks around each model call; None answers them from the plan."""
        return None

    async def files(self, config: RunnableConfig) -> BackendProtocol | None:  # noqa: ARG002
        """The thread's files as the run's tools reach them; None when it has none here."""
        return None

    async def tool(self, config: RunnableConfig, name: str) -> BaseTool | None:  # noqa: ARG002
        """The tool behind the operation ``name``."""
        return None

    def end_hooks(self, config: RunnableConfig) -> Sequence[AgentMiddleware]:  # noqa: ARG002
        """The middleware whose ``after_agent`` hooks end the run, in the graph's order."""
        return ()
