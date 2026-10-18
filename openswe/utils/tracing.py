"""Where runs are traced: the LangSmith SDK's own project setting."""

from langsmith.address import EnvAddressError, address_from_env

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


def traces_to_agent() -> bool:
    """Whether the LANGSMITH_AGENT_* variables name a complete, valid agent environment."""
    try:
        return address_from_env() is not None
    except EnvAddressError:
        return False
