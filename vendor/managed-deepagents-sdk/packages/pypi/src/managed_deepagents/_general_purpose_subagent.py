"""deepagents' default general-purpose subagent, declared by MDA so it can take a credential gate.

deepagents gives that subagent no way to add middleware, so MDA declares it the
way `create_deep_agent` builds it; the parity tests in
`tests/test_subagent_credentials.py` fail when deepagents changes how it does.

MDAC-281: once deepagents can add middleware to its default general-purpose
subagent, pass the gate through that option and delete this module and the
parity tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model

from managed_deepagents._async_subagents import GENERAL_PURPOSE, default_general_purpose
from managed_deepagents._connectors.mcp.load import mcp_credential_consumers
from managed_deepagents._credential_gate import create_credential_gate_middleware
from managed_deepagents._tool_gateway.cards import tool_gateway_prompts

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from managed_deepagents._connector import Connector

# Its default middleware, which a lead's middleware replaces by name.
_SLOTS = frozenset(
    {
        "FilesystemMiddleware",
        "SummarizationMiddleware",
        "PatchToolCallsMiddleware",
        "SkillsMiddleware",
        "AnthropicPromptCachingMiddleware",
    }
)


def gated_general_purpose(
    params: Mapping[str, object],
    connectors: Sequence[Connector],
    managed: Sequence[object] = (),
) -> dict[str, object] | None:
    """The general-purpose subagent with a credential gate over the agent's MCP definitions.

    It also takes the `managed` middleware every sync subagent needs. `None` when
    it needs neither, the agent declares its own, or the model's harness profile
    turns it off. deepagents applies the profile's
    prompt to a declared subagent itself.
    """
    gate = create_credential_gate_middleware(mcp_credential_consumers(connectors))
    declared = cast("Iterable[object]", params.get("subagents") or ())
    if (gate is None and not managed) or any(
        isinstance(spec, Mapping) and spec.get("name") == GENERAL_PURPOSE and "graph_id" not in spec
        for spec in declared
    ):
        return None
    model = params.get("model")
    profile = (
        None
        if model is None
        else _harness_profile_for_model(
            cast("BaseChatModel", model), model if isinstance(model, str) else None
        )
    )
    settings = profile.general_purpose_subagent if profile is not None else None
    if settings is not None and settings.enabled is False:
        return None
    spec = default_general_purpose()
    if settings is not None and settings.description is not None:
        spec["description"] = settings.description
    if settings is not None and settings.system_prompt is not None:
        spec["system_prompt"] = settings.system_prompt
    if (skills := params.get("skills")) is not None:
        spec["skills"] = list(cast("Iterable[str]", skills))
    lead = cast("Iterable[object]", params.get("middleware") or ())
    spec["middleware"] = [
        *([] if gate is None else [gate, *tool_gateway_prompts(connectors)]),
        *managed,
        *(m for m in lead if getattr(m, "name", None) in _SLOTS),
    ]
    return spec
