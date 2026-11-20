"""Static definitions and per-invocation managed agent factories."""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from pydantic import BaseModel

from managed_deepagents.define_deep_agent import DeepAgentDefinition

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import ManagedServerRuntime

ParamsT = TypeVar("ParamsT")

AgentFactory = Callable[
    ["ManagedServerRuntime"],
    DeepAgentDefinition | Awaitable[DeepAgentDefinition],
]

AgentParamsFunction = Callable[["ManagedServerRuntime"], ParamsT | Awaitable[ParamsT]]
"""Returns the params one graph access builds the agent from: a JSON-serializable
dict, or a pydantic model. They are also the cache key."""


@dataclass(frozen=True)
class CachedParams(Generic[ParamsT]):
    """The params one graph access produced, plus the cache key derived from them."""

    # SHA-256 of the params serialized as canonical JSON. MDA uses it as the cache key
    # for both the built definition and its compiled graphs.
    digest: str
    # A fresh copy of the params, rebuilt from that same JSON. The agent function gets
    # this copy, so it can only see values that are part of the cache key.
    params: ParamsT


class CachedAgent(Generic[ParamsT]):
    """An agent whose compiled graph is cached and reused across runs.

    For agents that vary per run. A static ``define_deep_agent(...)`` export is
    already compiled once and reused, with no decorator needed.

    - ``keyed_by(runtime)`` runs on every graph access (every run, state read and
      schema read) and returns the params the agent is built from, as a
      JSON-serializable dict or a pydantic model. MDA hashes their JSON and uses the
      hash as the cache key.
    - ``build(params)`` runs only when MDA has nothing cached for that key yet, so
      there is one graph per distinct value ``keyed_by`` returns.

    ``build`` gets a copy of the params rebuilt from their JSON, and runs in an empty
    ``contextvars`` context, so it cannot read the current runtime or run. This is
    deliberate: the graph it builds is reused by other runs, so it must not depend on
    anything from the run that happened to build it.

    Anything that differs per run or per caller, such as credentials or per-user data,
    must be read by tools and middleware while the run executes, not inside ``build``.
    """

    def __init__(
        self,
        build: Callable[[ParamsT], DeepAgentDefinition],
        keyed_by: AgentParamsFunction[ParamsT],
    ) -> None:
        self._keyed_by = keyed_by
        self._build = build

    async def resolve(self, runtime: ManagedServerRuntime) -> CachedParams[ParamsT]:
        returned = self._keyed_by(runtime)
        given = cast("ParamsT", await returned if inspect.isawaitable(returned) else returned)
        if isinstance(given, BaseModel):
            snapshot = given.model_dump(mode="json")
            copy = type(given).model_validate(snapshot)
        else:
            # Round-trip through JSON so the copy holds only what the key holds.
            snapshot = json.loads(json.dumps(given))
            copy = json.loads(json.dumps(snapshot))
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return CachedParams(
            digest=hashlib.sha256(canonical.encode()).hexdigest(),
            params=cast("ParamsT", copy),
        )

    def build(self, resolved: CachedParams[ParamsT]) -> DeepAgentDefinition:
        return contextvars.Context().run(self._build, resolved.params)

    async def __call__(self, runtime: ManagedServerRuntime) -> DeepAgentDefinition:
        """Build a definition for this access without caching it.

        Lets a cached agent be called like a plain ``agent(runtime)`` factory.
        """
        return self.build(await self.resolve(runtime))


def cached_agent(
    *, keyed_by: AgentParamsFunction[ParamsT]
) -> Callable[[Callable[[ParamsT], DeepAgentDefinition]], CachedAgent[ParamsT]]:
    """Make an agent whose compiled graph MDA caches and reuses across runs.

    There is one graph per distinct value ``keyed_by`` returns::

        def agent_params(runtime: ManagedServerRuntime) -> dict:
            return {"mode": mode_for(runtime.channel)}


        @cached_agent(keyed_by=agent_params)
        def agent(params: dict):
            return define_deep_agent(name="assistant", instructions=PROMPTS[params["mode"]])

    A static agent needs no decorator: an exported ``define_deep_agent(...)`` is
    compiled once and reused. See ``CachedAgent`` for when each function runs and
    what ``build`` can see.
    """

    def decorate(build: Callable[[ParamsT], DeepAgentDefinition]) -> CachedAgent[ParamsT]:
        return CachedAgent[ParamsT](build, keyed_by)

    return decorate


AgentEntry = DeepAgentDefinition | AgentFactory | CachedAgent[Any]

__all__ = ["AgentEntry", "AgentFactory", "CachedAgent", "CachedParams", "cached_agent"]
