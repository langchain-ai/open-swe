"""Where runs are traced: the LangSmith SDK's own ``LANGSMITH_PROJECT``."""

from agent.config import ENV


def tracing_project() -> str:
    """LangSmith project every run traces into and trace links point at.

    The SDK reads the same variable, and LangGraph Platform sets it to the
    deployment name, so each deployment's traces stay in its own project.
    """
    return ENV.LANGSMITH_PROJECT.get()
