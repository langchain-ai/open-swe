"""Attach metadata to the LangSmith run that a query can actually find.

``get_current_run_tree()`` returns the INNERMOST active run. Tagging from inside
a middleware hook therefore lands the metadata on a nested span -- which is
invisible to any query filtered on ``is_root=true``. Analyses that ask
per-invocation questions ("which route did this turn take?") are exactly the
ones that filter on root runs, so metadata tagged this way cannot answer them.

`tag_root_run_metadata` walks to the root of the run tree instead, so the value
lands on the row those queries return.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# A run tree is a linked list of parents; this bounds the walk so a cycle or an
# unexpectedly deep tree can never hang the middleware chain.
_MAX_PARENT_DEPTH = 64


def _current_run_tree() -> Any | None:
    try:
        from langsmith.run_helpers import get_current_run_tree

        return get_current_run_tree()
    except Exception:  # noqa: BLE001
        return None


def tag_run_metadata(extra_metadata: dict[str, Any]) -> None:
    """Attach ``extra_metadata`` to the current (innermost) run, best-effort."""
    run_tree = _current_run_tree()
    if run_tree is None:
        return
    try:
        run_tree.metadata.update(extra_metadata)
    except Exception:  # noqa: BLE001
        logger.debug("Could not tag run metadata", exc_info=True)


def tag_root_run_metadata(extra_metadata: dict[str, Any]) -> None:
    """Attach ``extra_metadata`` to the ROOT run, best-effort.

    Walks ``parent_run`` to the top of the tree. Every step is guarded: a
    missing or malformed ``parent_run`` stops the walk and tags whatever run was
    reached, which is strictly better than tagging nothing.
    """
    run_tree = _current_run_tree()
    if run_tree is None:
        return
    for _ in range(_MAX_PARENT_DEPTH):
        try:
            parent = getattr(run_tree, "parent_run", None)
        except Exception:  # noqa: BLE001
            break
        if parent is None:
            break
        run_tree = parent
    try:
        run_tree.metadata.update(extra_metadata)
    except Exception:  # noqa: BLE001
        logger.debug("Could not tag root run metadata", exc_info=True)
