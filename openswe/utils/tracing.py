"""Where runs are traced: the LangSmith SDK's own project setting."""

from langsmith.address import EnvAddressError, resolve_destination
from langsmith.utils import get_tracer_project

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


def run_tracer() -> dict[str, object]:
    """The ``langsmith_tracer`` a run sent to MDA carries, so it traces where in-process runs do.

    The agent address when the environment names one, else the project. A broken
    environment leaves the SDK's own runs untraced, so it falls back to the project name.
    """
    try:
        project, address = resolve_destination()
    except EnvAddressError:
        return {"project_name": get_tracer_project()}
    if address:
        return {"address": address}
    return {"project_name": project or get_tracer_project()}
