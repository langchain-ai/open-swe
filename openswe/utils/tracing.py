"""Where runs are traced: the MDA deployment's own project."""

from openswe.config import ENV


def tracing_project() -> str:
    """LangSmith project every run traces into and trace links point at.

    Runs execute on the MDA deployment, which traces them into its own project,
    ``MDA_LANGSMITH_PROJECT``, so links, cost lookups, feedback and reviewer evals
    query the project the runs actually went to. Unset, it is this backend's
    ``LANGSMITH_PROJECT`` (with the legacy ``LANGCHAIN_PROJECT`` alias), as when
    both run locally under one project.
    """
    return ENV.MDA_LANGSMITH_PROJECT.optional() or ENV.LANGSMITH_PROJECT.get()
