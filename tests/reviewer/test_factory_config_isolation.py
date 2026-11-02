"""The read-only graph does not mutate the caller's config."""

from typing import cast
from unittest.mock import MagicMock, patch

from langgraph.graph.state import RunnableConfig

from openswe.graphs.read_only import read_only_graph


async def test_read_only_graph_copies_config_dicts_but_preserves_runtime_objects() -> None:
    """Config isolation must preserve callback and configurable value identities."""
    callback = object()
    configurable_value = object()
    callbacks = [callback]
    config = cast(
        RunnableConfig,
        {
            "configurable": {"thread_id": None, "custom_key": configurable_value},
            "callbacks": callbacks,
        },
    )

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("openswe.graphs.read_only.create_deep_agent", return_value=fake_pregel):
        await read_only_graph(config)

    assert fake_pregel.with_config.call_args is not None
    bound_config = cast(dict[str, object], fake_pregel.with_config.call_args.args[0])
    configurable = cast(dict[str, object], bound_config["configurable"])
    original_configurable = config.get("configurable")
    assert isinstance(original_configurable, dict)
    assert bound_config is not config
    assert configurable is not original_configurable
    assert configurable["custom_key"] is configurable_value
    assert bound_config["callbacks"] is callbacks
    assert bound_config["callbacks"][0] is callback
    assert config.get("configurable") == {"thread_id": None, "custom_key": configurable_value}
