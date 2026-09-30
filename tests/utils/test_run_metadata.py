"""Root-run metadata tagging.

The bug these cover: `get_current_run_tree()` returns the INNERMOST run, so
tagging from inside a middleware hook lands metadata on a nested span, which
`is_root=true` queries never see.
"""

from types import SimpleNamespace
from unittest.mock import patch

from agent.utils import run_metadata


def _tree(depth: int) -> tuple[object, object]:
    """A chain of `depth` runs; returns (innermost, root)."""
    root = SimpleNamespace(metadata={}, parent_run=None)
    node = root
    for _ in range(depth - 1):
        node = SimpleNamespace(metadata={}, parent_run=node)
    return node, root


def test_tags_root_not_current() -> None:
    inner, root = _tree(4)
    with patch.object(run_metadata, "_current_run_tree", return_value=inner):
        run_metadata.tag_root_run_metadata({"open_swe_model_route": "fast"})
    assert root.metadata == {"open_swe_model_route": "fast"}
    assert inner.metadata == {}, "must not tag the innermost run"


def test_tags_current_when_asked() -> None:
    inner, root = _tree(3)
    with patch.object(run_metadata, "_current_run_tree", return_value=inner):
        run_metadata.tag_run_metadata({"open_swe_model_route": "balanced"})
    assert inner.metadata == {"open_swe_model_route": "balanced"}
    assert root.metadata == {}


def test_root_tag_is_a_noop_without_a_run_tree() -> None:
    with patch.object(run_metadata, "_current_run_tree", return_value=None):
        run_metadata.tag_root_run_metadata({"open_swe_model_route": "fast"})


def test_single_node_tree_tags_itself() -> None:
    only = SimpleNamespace(metadata={}, parent_run=None)
    with patch.object(run_metadata, "_current_run_tree", return_value=only):
        run_metadata.tag_root_run_metadata({"open_swe_model_route": "performance"})
    assert only.metadata == {"open_swe_model_route": "performance"}


def test_cycle_cannot_hang_the_middleware() -> None:
    """A malformed tree must terminate, not spin."""
    a = SimpleNamespace(metadata={}, parent_run=None)
    b = SimpleNamespace(metadata={}, parent_run=a)
    a.parent_run = b  # cycle
    with patch.object(run_metadata, "_current_run_tree", return_value=b):
        run_metadata.tag_root_run_metadata({"open_swe_model_route": "fast"})
    assert a.metadata or b.metadata, "depth cap should still tag whatever it reached"


def test_unreadable_parent_stops_the_walk() -> None:
    class Hostile:
        def __init__(self) -> None:
            self.metadata: dict[str, str] = {}

        @property
        def parent_run(self) -> object:
            raise RuntimeError("boom")

    node = Hostile()
    with patch.object(run_metadata, "_current_run_tree", return_value=node):
        run_metadata.tag_root_run_metadata({"open_swe_model_route": "fast"})
    assert node.metadata == {"open_swe_model_route": "fast"}
