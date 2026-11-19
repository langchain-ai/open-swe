"""Selectable models and global defaults offered to the profile editor."""

from fastapi import APIRouter

from openswe.dashboard.options import available_requested_models, gate_fable_model
from openswe.dashboard.settings import get_settings

router = APIRouter(tags=["options"])


@router.get("/options")
async def options() -> dict[str, object]:
    """The selectable models and effective global defaults."""
    settings = await get_settings()
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
        "default_repo": settings.get("default_repo"),
        "models": list(available_requested_models(fable_enabled=fable_enabled).values()),
        "model_selection_enabled": settings.get("model_selection_enabled") is True,
        "default_agent_model": agent_model,
        "default_agent_reasoning_effort": agent_effort,
        "default_agent_subagent_model": subagent_model,
        "default_agent_subagent_reasoning_effort": subagent_effort,
    }
