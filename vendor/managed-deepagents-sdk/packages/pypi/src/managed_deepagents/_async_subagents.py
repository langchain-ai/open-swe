"""Async subagents: subagent specs marked `"async": True` that MDA builds as their own graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard, cast

from managed_deepagents.define_deep_agent import DeepAgentDefinition

#: The general-purpose subagent, which served Python agents also get async.
GENERAL_PURPOSE = "general-purpose"
# The agent's skills, which deepagents' default general-purpose subagent loads.
_AGENT_SKILLS = "/skills/"

# Spec keys that override the lead's value; unset ones inherit it, like a sync subagent.
_INHERITED_KEYS = ("model", "tools", "permissions", "interrupt_on")
# Spec keys the lead never passes down, like a sync subagent.
_OWN_KEYS = ("middleware", "response_format")

#: Definition key for an async subagent's own skill sources (deepagents `SubAgent.skills`).
SUBAGENT_SKILLS_KEY = "subagent_skills"


def is_async_subagent(spec: object) -> TypeGuard[Mapping[str, object]]:
    """Whether `spec` is a subagent dict marked `"async": True`."""
    return isinstance(spec, Mapping) and spec.get("async") is True


def with_async_general_purpose(definition: DeepAgentDefinition) -> DeepAgentDefinition:
    """Add an async general-purpose subagent, unless one is declared already.

    It copies a declared sync general-purpose subagent, or else deepagents'
    default, so the lead can run the same agent blocking or in the background.
    """
    subagents = list(_subagents(definition))
    if any(is_async_subagent(spec) and spec.get("name") == GENERAL_PURPOSE for spec in subagents):
        return definition
    declared = next(
        (
            spec
            for spec in subagents
            if _is_declarative(spec) and spec.get("name") == GENERAL_PURPOSE
        ),
        None,
    )
    # A declared one keeps its own `skills`; the default loads the agent's, like deepagents'.
    spec = (
        dict(declared)
        if declared is not None
        else {**_default_general_purpose(), "skills": [_AGENT_SKILLS]}
    )
    return DeepAgentDefinition(
        {**definition.config, "subagents": [*subagents, {**spec, "async": True}]}
    )


def _default_general_purpose() -> dict[str, object]:
    # Imported here so a deepagents rename breaks only this feature, not the runtime.
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT  # noqa: PLC0415

    return dict(GENERAL_PURPOSE_SUBAGENT)


def route_async_subagents(
    definition: DeepAgentDefinition, graphs: Mapping[str, str]
) -> DeepAgentDefinition:
    """Point each async subagent at the graph MDA built for it."""
    subagents = definition.config.get("subagents")
    if not isinstance(subagents, Sequence) or not any(map(is_async_subagent, subagents)):
        return definition
    routed = [_route(spec, graphs) if is_async_subagent(spec) else spec for spec in subagents]
    return DeepAgentDefinition({**definition.config, "subagents": routed})


def reject_async_subagents(definition: DeepAgentDefinition) -> None:
    """Refuse async subagents in eval, which has no Agent Server to launch them on."""
    subagents = definition.config.get("subagents")
    for spec in subagents if isinstance(subagents, Sequence) else ():
        if is_async_subagent(spec):
            msg = (
                f"async subagent {spec.get('name')!r} is not supported in `mda eval`: "
                "there is no Agent Server to launch it on"
            )
            raise ValueError(msg)


def async_subagent_definition(definition: DeepAgentDefinition, name: str) -> DeepAgentDefinition:
    """Derive the definition of async subagent `name` from the lead's definition."""
    spec = _find(definition, name)
    config = {
        key: value
        for key, value in definition.config.items()
        if key not in ("subagents", *_OWN_KEYS)
    }
    config.update({key: spec[key] for key in (*_INHERITED_KEYS, *_OWN_KEYS) if key in spec})
    # Keep the lead's skills mount; load only the spec's `skills`. The lead's memory
    # and instructions.md aren't the subagent's.
    config[SUBAGENT_SKILLS_KEY] = list(cast("Sequence[str]", spec.get("skills") or ()))
    config.update(name=name, memory=None, instructions=spec.get("system_prompt"))
    return DeepAgentDefinition(config)


def _route(spec: Mapping[str, object], graphs: Mapping[str, str]) -> dict[str, object]:
    name = spec.get("name")
    graph_id = graphs.get(name) if isinstance(name, str) else None
    if graph_id is None:
        msg = (
            f"async subagent {name!r} has no graph. Declare it inline in "
            "define_deep_agent(subagents=[...]) with a literal `name` and "
            '`"async": True` so `mda` can build it.'
        )
        raise ValueError(msg)
    routed: dict[str, object] = {
        "name": name,
        "description": spec.get("description", ""),
        "graph_id": graph_id,
    }
    routed.update(
        {
            key: spec[key]
            for key in ("webhook", "notify_parent", "update_strategy", "mode")
            if key in spec
        }
    )
    return routed


def _subagents(definition: DeepAgentDefinition) -> Sequence[object]:
    subagents = definition.config.get("subagents")
    return subagents if isinstance(subagents, Sequence) else ()


def _is_declarative(spec: object) -> TypeGuard[Mapping[str, object]]:
    """A sync spec MDA can copy: not prebuilt (`runnable`), async, or a fork of the lead."""
    return (
        isinstance(spec, Mapping)
        and not {"runnable", "graph_id"} & spec.keys()
        and spec.get("mode") != "fork"
        and not is_async_subagent(spec)
    )


def _find(definition: DeepAgentDefinition, name: str) -> Mapping[str, object]:
    for spec in _subagents(definition):
        if is_async_subagent(spec) and spec.get("name") == name:
            return spec
    msg = f"async subagent {name!r} is not declared in define_deep_agent(subagents=[...])"
    raise ValueError(msg)
