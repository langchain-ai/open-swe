import gc
from typing import Annotated
from unittest.mock import patch

from langchain_core.tools import StructuredTool, tool
from langgraph.prebuilt import InjectedState

from openswe.agent_api import operations
from openswe.agent_api.operations import Injections


def _reads_route(route: Annotated[str | None, InjectedState("model_route")], text: str) -> str:
    """Read the route."""
    return f"{route}:{text}"


def test_a_tools_signature_is_read_once_however_often_its_injections_are_asked() -> None:
    """Every run's start asks for every tool's; reading the signature each time held the loop."""

    def reads_route(route: Annotated[str | None, InjectedState("model_route")], text: str) -> str:
        """Read the route."""
        return f"{route}:{text}"

    first, again = (tool(reads_route), tool(reads_route))

    with patch.object(operations, "get_type_hints", wraps=operations.get_type_hints) as read:
        injections = [Injections.of(first), Injections.of(again), Injections.of(first)]

    read.assert_called_once()
    assert all(found.reads == ["model_route"] and not found.reads_all for found in injections)


def test_a_functions_injections_are_dropped_with_it() -> None:
    """An integration's tools are built per run, so their entries must not outlive them."""

    def per_run(state: Annotated[dict, InjectedState()]) -> str:
        """A tool built for one run."""
        return str(state)

    built = StructuredTool.from_function(per_run)
    assert Injections.of(built).reads_all
    assert per_run in operations._INJECTIONS  # noqa: SLF001

    del built, per_run
    gc.collect()

    assert not any(
        getattr(function, "__name__", "") == "per_run"
        for function in operations._INJECTIONS  # noqa: SLF001
    )


def test_a_function_that_cannot_be_weakly_referenced_is_read_each_time() -> None:
    class Unreferenceable:
        __slots__ = ()

        def __call__(self, text: str) -> str:
            return text

    built = tool(_reads_route)
    object.__setattr__(built, "func", Unreferenceable())

    assert Injections.of(built) == Injections({})
