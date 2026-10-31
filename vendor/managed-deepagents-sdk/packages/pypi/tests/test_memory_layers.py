"""Unit tests for layer-based memory normalization helpers.

Mirrors ``packages/npm/src/memory/scope.test.ts``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from managed_deepagents._memory.layers import (
    has_agent_memory_layer,
    has_user_memory_layer,
    normalize_memory_layers,
    user_memory_layer,
)


def test_normalizes_the_launch_modes() -> None:
    assert normalize_memory_layers(None) == []
    assert normalize_memory_layers([]) == []
    assert normalize_memory_layers([{"type": "agent"}]) == [{"type": "agent"}]


def test_normalizes_user_memory_and_canonicalizes_both_layers() -> None:
    assert normalize_memory_layers([{"type": "user"}]) == [{"type": "user"}]
    assert normalize_memory_layers([{"type": "user"}, {"type": "agent"}]) == [
        {"type": "agent"},
        {"type": "user"},
    ]


def test_keeps_an_asserted_user_id() -> None:
    # The id rides along for the runtime to check against the authenticated
    # principal; normalize does not resolve or drop it.
    assert normalize_memory_layers([{"type": "user", "id": "usr_1"}]) == [
        {"type": "user", "id": "usr_1"}
    ]


def test_rejects_an_id_on_the_agent_layer() -> None:
    with pytest.raises(ValueError, match="agent layer takes no `id`"):
        normalize_memory_layers(cast("Any", [{"type": "agent", "id": "usr_1"}]))


def test_rejects_a_blank_user_id() -> None:
    for value in ("", "   ", 7):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            normalize_memory_layers(cast("Any", [{"type": "user", "id": value}]))


def test_names_per_layer_options_as_reserved() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        normalize_memory_layers(cast("Any", [{"type": "agent", "access": "rw"}]))
    with pytest.raises(ValueError, match="Per-layer mounts, access"):
        normalize_memory_layers(
            cast(
                "Any",
                [
                    {
                        "type": "group",
                        "claim": "project_id",
                        "mount": "/memories/project",
                    }
                ],
            )
        )


def test_rejects_the_retired_string_shorthand() -> None:
    # The shape that shipped before scope is the first thing a reader of older
    # code reaches for, so it names the object form rather than "unknown entry".
    with pytest.raises(ValueError, match="must be a list of layers"):
        normalize_memory_layers(cast("Any", "agent"))
    with pytest.raises(ValueError, match="is not a layer"):
        normalize_memory_layers(cast("Any", ["agent"]))


def test_rejects_unknown_layer_types() -> None:
    with pytest.raises(ValueError, match="unknown `scope` entry type"):
        normalize_memory_layers(cast("Any", [{"type": "organization"}]))
    with pytest.raises(ValueError, match='Use "agent" or "user"'):
        normalize_memory_layers(cast("Any", [{"type": "workspace"}]))


def test_rejects_duplicate_layers() -> None:
    with pytest.raises(ValueError, match="more than once"):
        normalize_memory_layers([{"type": "agent"}, {"type": "agent"}])
    with pytest.raises(ValueError, match="more than once"):
        normalize_memory_layers([{"type": "user"}, {"type": "user"}])


def test_reports_the_agent_layer() -> None:
    assert has_agent_memory_layer(normalize_memory_layers([{"type": "agent"}]))
    assert not has_agent_memory_layer([])


def test_reports_the_user_layer() -> None:
    assert has_user_memory_layer(normalize_memory_layers([{"type": "user"}]))
    assert not has_user_memory_layer([])


def test_returns_the_declared_user_layer() -> None:
    scope = normalize_memory_layers([{"type": "agent"}, {"type": "user", "id": "usr_1"}])
    assert user_memory_layer(scope) == {"type": "user", "id": "usr_1"}
    assert user_memory_layer(normalize_memory_layers([{"type": "agent"}])) is None
