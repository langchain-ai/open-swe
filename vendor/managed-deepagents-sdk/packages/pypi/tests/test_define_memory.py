"""Unit tests for the root memory declaration.

Mirrors ``packages/npm/src/memory/define-memory.test.ts``.
"""

from __future__ import annotations

import re
from typing import Any, cast

import pytest

from managed_deepagents import MemoryLayer, define_memory


def test_resolves_the_agent_layer_into_a_versioned_config() -> None:
    memory = define_memory(scope=[{"type": "agent"}])
    assert memory.kind == "memory"
    assert memory.config == {"version": 2, "scope": [{"type": "agent"}]}


def test_resolves_both_layers_agent_first() -> None:
    config = define_memory(scope=[{"type": "user"}, {"type": "agent"}]).config
    assert config["scope"] == [{"type": "agent"}, {"type": "user"}]


def test_keeps_an_asserted_user_id_on_the_layer() -> None:
    config = define_memory(scope=[{"type": "user", "id": "usr_1"}]).config
    assert config["scope"] == [{"type": "user", "id": "usr_1"}]


def test_mounts_nothing_when_no_layer_is_declared() -> None:
    # A file that names no layer is legal and inert — memory is opt-in, so the
    # safe reading of "I wrote memory.py but said nothing" is "no memory".
    assert define_memory().config == {"version": 2, "scope": []}
    assert define_memory(scope=[]).config["scope"] == []


def test_rejects_the_retired_string_scope() -> None:
    with pytest.raises(ValueError, match="`scope` must be"):
        define_memory(**cast("Any", {"scope": "agent"}))
    with pytest.raises(ValueError, match=re.escape('scope=[{"type": "agent"}]')):
        define_memory(**cast("Any", {"scope": "agent"}))


def test_names_reserved_keywords_instead_of_calling_them_typos() -> None:
    with pytest.raises(ValueError, match="`user_memory` is not supported"):
        define_memory(**cast("Any", {"user_memory": True}))
    with pytest.raises(ValueError, match="Per-layer access modes"):
        define_memory(**cast("Any", {"access": "ro"}))
    with pytest.raises(ValueError, match="Claim- and slug-based partitions"):
        define_memory(**cast("Any", {"partition": {"slug": "acme"}}))
    with pytest.raises(ValueError, match="Retention policy is not designed yet"):
        define_memory(**cast("Any", {"retention": "90d"}))


def test_rejects_unknown_options() -> None:
    with pytest.raises(ValueError, match="unknown option\\(s\\): layerz"):
        define_memory(**cast("Any", {"layerz": []}))


def test_accepts_the_documented_single_scope_object() -> None:
    assert define_memory(scope={"type": "user"}).config["scope"] == [{"type": "user"}]


def test_retains_user_allow_without_calling_it() -> None:
    def allow(context: object) -> bool:
        raise AssertionError("must not run at declaration")

    memory = define_memory(scope={"type": "user", "allow": allow})
    assert memory.config["scope"] == [{"type": "user", "allow": allow}]


@pytest.mark.parametrize("allow", [None, True, "private", {}])
def test_rejects_non_callable_user_allow(allow: object) -> None:
    with pytest.raises(ValueError, match=r"allow.*callable"):
        define_memory(scope=cast("Any", {"type": "user", "allow": allow}))


def test_named_layers_keep_policies_without_evaluating_them() -> None:
    def allow(context: dict[str, object]) -> bool:
        raise AssertionError("authoring must not call policies")

    memory = define_memory(agent=MemoryLayer(), user=MemoryLayer(allow=allow))
    assert memory.config["scope"] == [{"type": "agent"}, {"type": "user", "allow": allow}]
    assert define_memory(agent=MemoryLayer(allow=allow)).config["scope"] == [
        {"type": "agent", "allow": allow}
    ]


@pytest.mark.parametrize("option", [True, False, {}, lambda ctx: True])
def test_named_layers_require_memory_layer_objects(option: object) -> None:
    with pytest.raises((TypeError, ValueError), match="MemoryLayer"):
        define_memory(agent=cast("Any", option))


def test_named_layers_cannot_mix_with_legacy_scope() -> None:
    with pytest.raises(ValueError, match="mix"):
        define_memory(agent=MemoryLayer(), scope={"type": "user"})
