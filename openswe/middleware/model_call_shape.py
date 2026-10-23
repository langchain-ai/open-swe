"""What a graph's middleware has to do with each model call its MDA run makes.

MDA makes the call; the backend says what goes in it (``next_model_call``). Each
piece of middleware says which of these it does, from the run's state, and the
run's session asks each in the graph's middleware order (``openswe.agent_api.graph``):

- ``EndsLoop``: end the run instead of making the call
- ``DeliversOwedMessages``: messages the thread is owed first, such as follow-ups
  queued while it ran
- ``PicksModelRoute``: which of the graph's models the call goes to
- ``ShapesModelCalls``: a section of the system prompt, or tools kept from the call

and, beside the call, ``RefreshesGitHubAccess``: keep the sandbox's GitHub access fresh.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol, Self, runtime_checkable

from langgraph.runtime import Runtime


def _joined(*sections: str) -> str:
    return "\n\n".join(section for section in sections if section)


@dataclass(frozen=True)
class CallShape:
    """The system prompt so far, and the tools kept from the call."""

    system_prompt: str = ""
    hidden_tools: frozenset[str] = frozenset()

    def prepending(self, section: str) -> Self:
        return replace(self, system_prompt=_joined(section, self.system_prompt))

    def appending(self, section: str) -> Self:
        return replace(self, system_prompt=_joined(self.system_prompt, section))

    def hiding(self, names: Iterable[str]) -> Self:
        return replace(self, hidden_tools=self.hidden_tools | frozenset(names))


@runtime_checkable
class ShapesModelCalls(Protocol):
    """Middleware that adds to the model calls a run makes."""

    async def ashape_model_call(
        self, call: CallShape, state: Mapping[str, object], runtime: Runtime
    ) -> CallShape: ...


@dataclass(frozen=True)
class LoopEnd:
    """The run ends instead of the next model call, with what it last writes to state."""

    update: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class EndsLoop(Protocol):
    async def aend_instead(
        self, state: Mapping[str, object], runtime: Runtime
    ) -> LoopEnd | None: ...


@runtime_checkable
class DeliversOwedMessages(Protocol):
    """Returns the state update that delivers them: ``messages``, and any state they move."""

    async def adeliver_owed(
        self, state: Mapping[str, object], runtime: Runtime
    ) -> Mapping[str, object] | None: ...


@runtime_checkable
class PicksModelRoute(Protocol):
    """Returns the state update that names the route."""

    async def apick_route(
        self, state: Mapping[str, object], runtime: Runtime
    ) -> Mapping[str, object]: ...


@runtime_checkable
class RefreshesGitHubAccess(Protocol):
    async def arefresh_github_access(self) -> None: ...
