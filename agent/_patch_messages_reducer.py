"""Guard deepagents message reducer against None checkpoint state.

DeltaChannel snapshots can persist ``None`` for ``messages`` (e.g. after a
cancelled run). The stock reducer calls ``convert_to_messages(None)``, which
raises when LangGraph replays checkpoint history for ``GET /threads/.../state``.

Must load ``deepagents._messages_reducer`` without importing ``deepagents.graph``
first — ``deepagents.__init__`` eagerly imports ``create_deep_agent``, which
binds the reducer into ``DeltaChannel`` at class definition time.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AnyMessage

_PATCHED_ATTR = "_open_swe_messages_reducer_patched"


def _load_messages_reducer_module():
    name = "deepagents._messages_reducer"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing

    spec = importlib.util.find_spec("deepagents")
    if spec is None or not spec.origin:
        raise ImportError("deepagents package not found")
    root = Path(spec.origin).parent
    reducer_spec = importlib.util.spec_from_file_location(
        name,
        root / "_messages_reducer.py",
    )
    if reducer_spec is None or reducer_spec.loader is None:
        raise ImportError("deepagents._messages_reducer not found")
    mod = importlib.util.module_from_spec(reducer_spec)
    sys.modules[name] = mod
    reducer_spec.loader.exec_module(mod)
    return mod


def _apply() -> None:
    mod = _load_messages_reducer_module()
    if getattr(mod._messages_delta_reducer, _PATCHED_ATTR, False):
        return

    orig = mod._messages_delta_reducer
    if getattr(orig, _PATCHED_ATTR, False):
        return

    def _messages_delta_reducer(
        state: list[AnyMessage] | None, writes: Sequence[list[AnyMessage]]
    ) -> list[AnyMessage]:
        if state is None:
            state = []
        return orig(state, writes)

    mod._messages_delta_reducer = _messages_delta_reducer
    setattr(_messages_delta_reducer, _PATCHED_ATTR, True)

    graph_mod = sys.modules.get("deepagents.graph")
    if graph_mod is not None:
        graph_mod._messages_delta_reducer = mod._messages_delta_reducer
        importlib.reload(graph_mod)


_apply()
