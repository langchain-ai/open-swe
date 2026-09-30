import pytest
from langgraph.graph import END, START, StateGraph

from agent.middleware.conversation_offloading import OffloadingState
from agent.middleware.prepare_run import PrepareRunState


async def _run_concurrent_updates(
    state_type: type[PrepareRunState] | type[OffloadingState],
    updates: dict[str, object],
) -> dict[str, object]:
    graph = StateGraph(state_type)

    async def subagent(_state: object) -> dict[str, object]:
        return updates

    graph.add_node("subagent_a", subagent)
    graph.add_node("subagent_b", subagent)
    graph.add_edge(START, "subagent_a")
    graph.add_edge(START, "subagent_b")
    graph.add_edge("subagent_a", END)
    graph.add_edge("subagent_b", END)
    return await graph.compile().ainvoke({})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_type", "updates"),
    [
        (
            PrepareRunState,
            {
                "run_prepared": True,
                "run_prepared_for": "fingerprint",
                "work_dir": "/tmp/work",
                "rendered_system_prompt": "prompt",
            },
        ),
        (
            OffloadingState,
            {"conversation_offloading": {"status": "completed"}},
        ),
    ],
)
async def test_concurrent_subagent_updates_complete(
    state_type: type[PrepareRunState] | type[OffloadingState],
    updates: dict[str, object],
) -> None:
    result = await _run_concurrent_updates(state_type, updates)

    assert {key: result[key] for key in updates} == updates
