"""The Open SWE graphs Managed Deep Agents runs play, by assistant id."""

from collections.abc import Mapping
from functools import cache

from openswe.agent_api.graph import RemoteGraph


@cache
def hosted_graphs() -> Mapping[str, RemoteGraph]:
    from openswe.agent_api.agent import AGENT
    from openswe.agent_api.review_scout import REVIEW_SCOUT
    from openswe.agent_api.reviewer import REVIEWER

    return {graph.assistant_id: graph for graph in (AGENT, REVIEWER, REVIEW_SCOUT)}
