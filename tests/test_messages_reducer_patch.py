import importlib
import sys

import pytest
from langchain_core.messages import HumanMessage


def _clear_deepagents_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "deepagents"
            or name.startswith("deepagents.")
            or name in {"agent.server", "agent.reviewer"}
        ):
            del sys.modules[name]
    importlib.invalidate_caches()


def _apply_patch() -> None:
    _clear_deepagents_modules()
    patch_mod = importlib.import_module("agent._patch_messages_reducer")
    importlib.reload(patch_mod)


def test_messages_delta_reducer_accepts_none_state() -> None:
    _apply_patch()
    from deepagents import _messages_reducer as reducer_mod

    result = reducer_mod._messages_delta_reducer(None, [])
    assert result == []


def test_messages_delta_reducer_still_merges_writes() -> None:
    _apply_patch()
    from deepagents import _messages_reducer as reducer_mod

    msg = HumanMessage(content="hi", id="m1")
    result = reducer_mod._messages_delta_reducer(None, [[msg]])
    assert len(result) == 1
    assert result[0].content == "hi"


def test_graph_binds_patched_reducer_on_server_import() -> None:
    _clear_deepagents_modules()
    importlib.import_module("agent.server")
    from deepagents.graph import _messages_delta_reducer

    assert _messages_delta_reducer(None, []) == []


def test_late_graph_import_is_repaired_by_patch() -> None:
    _clear_deepagents_modules()
    from deepagents.graph import _messages_delta_reducer as unpatched

    with pytest.raises(TypeError):
        unpatched(None, [])

    importlib.reload(importlib.import_module("agent._patch_messages_reducer"))
    from deepagents.graph import _messages_delta_reducer as patched

    assert patched(None, []) == []
