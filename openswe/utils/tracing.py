"""Where runs are traced: the LangSmith SDK's own project setting."""

from langsmith.address import AgentAddress, EnvAddressError, resolve_destination

from openswe.config import ENV


def tracing_project() -> str:
    """LangSmith project every run traces into and trace links point at.

    Read the way the SDK's tracer reads it, ``LANGSMITH_PROJECT`` with the legacy
    ``LANGCHAIN_PROJECT`` as fallback (the registry aliases), else ``default``, so
    links, cost lookups, and feedback query the project the runs actually went to.
    LangGraph Platform sets both names to the deployment name. The SDK's own
    helper is not used because it caches the first value it sees.
    """
    return ENV.LANGSMITH_PROJECT.get()


def tracing_address() -> AgentAddress | None:
    """The agent environment the SDK traces into, or ``None`` when it traces into a project.

    A broken environment leaves the SDK's own runs untraced, so it counts as a project.
    """
    try:
        return resolve_destination()[1]
    except EnvAddressError:
        return None
