"""Static definitions and per-invocation managed agent factories."""

from __future__ import annotations

import contextvars
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from pydantic import BaseModel

from managed_deepagents.define_deep_agent import DeepAgentDefinition

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import ManagedServerRuntime

InputsT = TypeVar("InputsT", bound=BaseModel)

AgentFactory = Callable[
    ["ManagedServerRuntime"],
    DeepAgentDefinition | Awaitable[DeepAgentDefinition],
]


@dataclass(frozen=True)
class SharedInputs(Generic[InputsT]):
    """One access's inputs to a shared factory, as MDA keys and builds from them."""

    # The digest of the inputs' JSON, which MDA keys the definition and its graphs on.
    digest: str
    # A copy of the inputs made from that JSON, so a build sees only what the key holds.
    inputs: InputsT


class SharedAgentFactory(Generic[InputsT]):
    """An agent built from its inputs alone, so MDA compiles one graph per distinct inputs.

    ``inputs`` runs on every access, a run's or a read's, and returns everything the
    agent is built from. MDA keys the definition on their JSON and shares its compiled
    graph with every access that has the same inputs. ``build`` runs only for inputs
    MDA keeps no definition for yet, with a copy of them made from that JSON and in an
    empty context: it sees no runtime and no run in progress, so it can build nothing
    from one run that a later run on the same inputs would not also get.

    Whatever a run needs of its own beyond the inputs, such as its tokens or its
    catalog, its tools and middleware read from the run at call time.
    """

    def __init__(
        self,
        inputs: Callable[[ManagedServerRuntime], Awaitable[InputsT]],
        build: Callable[[InputsT], DeepAgentDefinition],
    ) -> None:
        self._inputs = inputs
        self._build = build

    async def resolve(self, runtime: ManagedServerRuntime) -> SharedInputs[InputsT]:
        given = await self._inputs(runtime)
        snapshot = given.model_dump(mode="json")
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return SharedInputs(
            digest=hashlib.sha256(canonical.encode()).hexdigest(),
            inputs=type(given).model_validate(snapshot),
        )

    def build(self, resolved: SharedInputs[InputsT]) -> DeepAgentDefinition:
        return contextvars.Context().run(self._build, resolved.inputs)

    async def __call__(self, runtime: ManagedServerRuntime) -> DeepAgentDefinition:
        """The definition for one access, as any factory returns it, unshared."""
        return self.build(await self.resolve(runtime))


def shared_factory(
    inputs: Callable[[ManagedServerRuntime], Awaitable[InputsT]],
) -> Callable[[Callable[[InputsT], DeepAgentDefinition]], SharedAgentFactory[InputsT]]:
    """Declare the decorated function as an agent built from ``inputs`` alone.

    ```python
    async def agent_inputs(runtime: ManagedServerRuntime) -> AgentInputs: ...


    @shared_factory(agent_inputs)
    def agent(inputs: AgentInputs) -> DeepAgentDefinition:
        return define_deep_agent(name="assistant", model=inputs.model)
    ```

    See ``SharedAgentFactory`` for what MDA shares and when it builds.
    """

    def decorate(build: Callable[[InputsT], DeepAgentDefinition]) -> SharedAgentFactory[InputsT]:
        return SharedAgentFactory(inputs, build)

    return decorate


AgentEntry = DeepAgentDefinition | AgentFactory | SharedAgentFactory[BaseModel]

__all__ = ["AgentEntry", "AgentFactory", "SharedAgentFactory", "SharedInputs", "shared_factory"]
