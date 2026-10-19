"""The Open SWE graphs Managed Deep Agents runs play, by assistant id."""

from collections.abc import Mapping
from functools import cache

from openswe.remote_runtime.graph import RemoteGraph


@cache
def hosted_graphs() -> Mapping[str, RemoteGraph]:
    from openswe.remote_runtime.agent import AGENT
    from openswe.remote_runtime.review_scout import REVIEW_SCOUT
    from openswe.remote_runtime.reviewer import REVIEWER

    return {graph.assistant_id: graph for graph in (AGENT, REVIEWER, REVIEW_SCOUT)}
