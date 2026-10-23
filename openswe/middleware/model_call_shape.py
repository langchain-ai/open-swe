"""What a graph's middleware adds to each model call its MDA run makes.

MDA makes the call; the backend says what goes in it. Middleware that adds a
section to the system prompt, or keeps a tool from the call, says so through
``ashape_model_call``, from the run's state. The run's session folds what each
says, in the graph's middleware order (``openswe.agent_api.graph``).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
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
