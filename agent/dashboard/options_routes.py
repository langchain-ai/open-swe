"""Selectable models and the defaults a workspace resolves to, offered to the profile editor."""

from typing import Any

from fastapi import APIRouter, HTTPException

from agent.dashboard.options import (
    FABLE_MODEL_IDS,
    SUPPORTED_MODELS,
    gate_fable_model,
    models_with_profile_context_windows,
)
from agent.dashboard.workspace_settings import get_workspace_settings
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, slugify

router = APIRouter(tags=["options"])


@router.get("/options")
async def options(workspace: str = DEFAULT_WORKSPACE_SLUG) -> dict[str, Any]:
    """The models and defaults a composer may offer for ``workspace``.

    Model defaults and the Fable flag resolve per workspace (the instance record
    plus the workspace's overrides), so a picker that asked without one would
    advertise the default workspace's values wherever the run will not land there.
    """
    try:
        workspace = slugify(workspace)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    settings = await get_workspace_settings(workspace)
    agent_model, agent_effort = settings.default_model("agent")
    subagent_model, subagent_effort = settings.default_subagent_model("agent")
    fable_enabled = settings.fable_enabled
    # Never advertise a default that isn't in the selectable list: when Fable is
    # off, gate a stale Fable default down to its non-Fable fallback so the Cloud
    # Agents page (and the PUT /profile it drives) don't choke on it.
    agent_model, agent_effort = gate_fable_model(
        agent_model, agent_effort, fable_enabled=fable_enabled
    )
    subagent_model, subagent_effort = gate_fable_model(
        subagent_model, subagent_effort, fable_enabled=fable_enabled
    )
    models = (
        SUPPORTED_MODELS
        if fable_enabled
        else [m for m in SUPPORTED_MODELS if m["id"] not in FABLE_MODEL_IDS]
    )
    return {
        "models": models_with_profile_context_windows(models),
        "default_agent_model": agent_model,
        "default_agent_reasoning_effort": agent_effort,
        "default_agent_subagent_model": subagent_model,
        "default_agent_subagent_reasoning_effort": subagent_effort,
    }
