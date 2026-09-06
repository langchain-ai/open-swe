"""Factories must not bake the server's runtime plumbing into the graphs they return."""

from agent.chat import get_chat_agent
from agent.runtime import bindable_config


class _ReadOnlyRuntime:
    """Stands in for the SDK's read runtime, which has no ``context`` attribute."""


def test_bindable_config_drops_langgraph_internal_keys() -> None:
    config = {
        "recursion_limit": 7,
        "configurable": {
            "thread_id": "t1",
            "model": "anthropic:claude-opus-5",
            "__pregel_runtime": _ReadOnlyRuntime(),
            "__pregel_checkpointer": object(),
            "__pregel_store": object(),
        },
    }

    bound = bindable_config(config)

    assert bound["recursion_limit"] == 7
    assert bound["configurable"] == {"thread_id": "t1", "model": "anthropic:claude-opus-5"}
    # The caller's config is left alone.
    assert "__pregel_runtime" in config["configurable"]


async def test_factory_graph_carries_no_runtime_from_the_read_path() -> None:
    """A state read hands the factory a read-only runtime; the bound graph must not keep it."""
    config = {
        "configurable": {"thread_id": "t1", "__pregel_runtime": _ReadOnlyRuntime()},
    }

    graph = await get_chat_agent(config)

    assert "__pregel_runtime" not in (graph.config or {}).get("configurable", {})
    assert graph.config["configurable"]["thread_id"] == "t1"
