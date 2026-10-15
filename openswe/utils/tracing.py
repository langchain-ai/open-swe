"""Where runs are traced: the LangSmith SDK's own agent or project setting."""

from dataclasses import dataclass

from openswe.config import ENV


@dataclass(frozen=True)
class AgentAddress:
    agent_id: str
    environment: str


def agent_address() -> AgentAddress | None:
    """The agent environment runs trace into, or None when they trace into a project.

    Like the SDK, ``LANGSMITH_AGENT_ID`` takes precedence over ``LANGSMITH_PROJECT``. An
    agent without an environment is incomplete and the SDK's runs are refused, so it
    counts as unaddressed here too.
    """
    agent_id = ENV.LANGSMITH_AGENT_ID.optional()
    environment = ENV.LANGSMITH_AGENT_ENVIRONMENT.optional()
    if not agent_id or not environment:
        return None
    return AgentAddress(agent_id, environment)


def tracing_project() -> str:
    """LangSmith project every run traces into when no agent is configured.

    Read the way the SDK's tracer reads it, ``LANGSMITH_PROJECT`` with the legacy
    ``LANGCHAIN_PROJECT`` as fallback (the registry aliases), else ``default``, so
    links, cost lookups, and feedback query the project the runs actually went to.
    LangGraph Platform sets both names to the deployment name. The SDK's own
    helper is not used because it caches the first value it sees.
    """
    return ENV.LANGSMITH_PROJECT.get()
