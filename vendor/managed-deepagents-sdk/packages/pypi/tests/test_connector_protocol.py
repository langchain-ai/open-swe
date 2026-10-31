"""Unit tests for the connector protocol: branding, collection, tool loading."""

from __future__ import annotations

import types
from typing import Any

import pytest

from managed_deepagents._connector import (
    Connector,
    SandboxContext,
    collect_connectors,
    is_connector,
    load_connector_tools,
    requirements_from_connectors,
    run_connector_sandbox_hooks,
)


class _ToolsConnector(Connector):
    def __init__(self, kind: str, tools: list[Any]) -> None:
        self.kind = kind
        self._tools = tools

    def tools(self, ctx: Any = None) -> list[Any]:
        return self._tools


def _module(name: str, **attrs: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def test_is_connector_recognizes_branded_objects() -> None:
    assert is_connector(_ToolsConnector("a", []))
    assert not is_connector(object())
    assert not is_connector(None)


def test_collect_connectors_reads_connector_attr_and_dedupes() -> None:
    connector = _ToolsConnector("a", [])
    collected = collect_connectors(
        [
            ("connectors/a.py", _module("a", connector=connector)),
            ("connectors/a_again.py", _module("a_again", connector=connector)),
        ]
    )
    assert collected == [connector]
    assert connector.name == "a"


def test_collect_connectors_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate connector name"):
        collect_connectors(
            [
                ("connectors/events.py", _module("a", connector=_ToolsConnector("a", []))),
                ("connectors/events.py", _module("b", connector=_ToolsConnector("b", []))),
            ]
        )


def test_collect_connectors_accepts_events_only_connector() -> None:
    class _EventsOnly(Connector):
        kind = "slack"

        def events(self, ctx: Any) -> Any:
            return None

    connector = _EventsOnly()
    collected = collect_connectors(
        [("connectors/events.py", _module("events", connector=connector))]
    )
    assert collected == [connector]
    assert callable(getattr(collected[0], "events", None))
    assert requirements_from_connectors(collected) == {}


def test_collect_connectors_skips_missing_attribute() -> None:
    connector = _ToolsConnector("a", [])
    assert collect_connectors(
        [
            ("connectors/a.py", _module("a", connector=connector)),
            ("connectors/test_a.py", _module("test_a")),
        ]
    ) == [connector]


def test_collect_connectors_skips_non_connector() -> None:
    connector = _ToolsConnector("a", [])
    assert collect_connectors(
        [
            ("connectors/a.py", _module("a", connector=connector)),
            ("connectors/not_a_connector.py", _module("x", connector=object())),
        ]
    ) == [connector]


def test_collect_connectors_rejects_connector_with_no_hooks() -> None:
    class _Empty(Connector):
        kind = "empty"

    with pytest.raises(ValueError, match="implements none of"):
        collect_connectors([("connectors/x.py", _module("x", connector=_Empty()))])


def test_collect_connectors_accepts_sandbox_only_connector() -> None:
    calls: list[str] = []

    class _SandboxOnly(Connector):
        kind = "bitbucket"

        def sandbox(self, ctx: SandboxContext) -> None:
            calls.append(ctx.mode)

    connector = _SandboxOnly()
    collected = collect_connectors(
        [("connectors/bitbucket.py", _module("bitbucket", connector=connector))]
    )
    assert collected == [connector]
    run_connector_sandbox_hooks(
        collected,
        backend=types.SimpleNamespace(execute=lambda *_a, **_k: None),
        mode="create",
    )
    assert calls == ["create"]


def test_load_connector_tools_concatenates_in_order() -> None:
    class _HttpOnly(Connector):
        kind = "http-only"

        def http(self, ctx: Any) -> None:
            return None

    tools = load_connector_tools(
        [
            _ToolsConnector("a", ["t1"]),
            _HttpOnly(),
            _ToolsConnector("b", ["t2", "t3"]),
        ]
    )
    assert tools == ["t1", "t2", "t3"]
