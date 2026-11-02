"""The kinds of run Open SWE starts on Managed Deep Agents, by assistant id."""

from collections.abc import Mapping
from functools import cache

from openswe.agent_api.run_kinds import RunKind


@cache
def run_kinds() -> Mapping[str, RunKind]:
    from openswe.agent_api.agent import AgentKind
    from openswe.agent_api.review_scout import ReviewScoutKind
    from openswe.agent_api.reviewer import ReviewerKind

    return {kind.assistant_id: kind for kind in (AgentKind(), ReviewerKind(), ReviewScoutKind())}
