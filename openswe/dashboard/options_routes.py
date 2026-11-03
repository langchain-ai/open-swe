"""Selectable models and the defaults a workspace resolves to, offered to the profile editor."""

from typing import Any

from fastapi import APIRouter, HTTPException

from openswe.dashboard.options import available_requested_models, gate_fable_model
from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, slugify

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
    return {
        "models": list(available_requested_models(fable_enabled=fable_enabled).values()),
        "default_agent_model": agent_model,
        "default_agent_reasoning_effort": agent_effort,
        "default_agent_subagent_model": subagent_model,
        "default_agent_subagent_reasoning_effort": subagent_effort,
    }
