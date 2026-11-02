"""The Open SWE graphs Managed Deep Agents runs play, by assistant id."""

from collections.abc import Mapping
from functools import cache

from openswe.agent_api.run_parts import RunParts


@cache
def hosted_graphs() -> Mapping[str, RunParts]:
    from openswe.agent_api.agent import AgentParts
    from openswe.agent_api.review_scout import ReviewScoutParts
    from openswe.agent_api.reviewer import ReviewerParts

    return {
        parts.assistant_id: parts for parts in (AgentParts(), ReviewerParts(), ReviewScoutParts())
    }
