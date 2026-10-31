"""Static graph entries cannot select run-scoped managed resources."""

from __future__ import annotations

from typing import Any, cast

import pytest

from managed_deepagents import (
    define_browser,
    define_deep_agent,
    define_mcp,
    define_memory,
    define_sandbox,
)
from managed_deepagents._static_entry import assert_static_agent_definition

_ACCOUNTS_MCP = define_mcp(
    servers={
        "accounts": {
            "transport": "http",
            "url": "https://example.com/mcp",
        }
    }
)


def test_static_entry_accepts_discovered_resources() -> None:
    assert_static_agent_definition(define_deep_agent(name="support"))


@pytest.mark.parametrize(
    ("field", "populated", "empty"),
    [
        ("instructions", "private-prompt", ""),
        ("mcp", [_ACCOUNTS_MCP], []),
        (
            "memory",
            define_memory(scope={"type": "agent"}),
            define_memory(),
        ),
        ("browser", define_browser(), None),
        ("sandbox", define_sandbox(), None),
        ("skills", ["/project/skills/team-playbook"], []),
    ],
)
def test_static_entry_rejects_run_selections(field: str, populated: object, empty: object) -> None:
    for value in (populated, empty):
        definition = define_deep_agent(name="support", **cast("Any", {field: value}))
        with pytest.raises(TypeError) as exc:
            assert_static_agent_definition(definition)
        assert str(exc.value) == f"`{field}` requires a factory entrypoint"
