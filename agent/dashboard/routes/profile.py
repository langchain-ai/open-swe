"""What the signed-in user picked for themselves: model, instructions, mapping."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..authz import SESSION
from ..options import (
    FABLE_MODEL_IDS,
    SUPPORTED_MODELS,
    gate_fable_model,
    models_with_profile_context_windows,
)
from ..profiles import (
    ProfileUpdate,
    get_profile,
    normalize_profile_for_response,
    upsert_profile,
)
from ..team_settings import (
    get_team_default_model,
    get_team_default_subagent_model,
    get_team_fable_enabled,
)
from ..user_instructions import (
    UserInstructionsUpdate,
    delete_user_instructions,
    get_user_instructions,
    set_user_instructions,
)
from ..user_mappings import get_mapping

router = APIRouter()


@router.get("/me/instructions")
async def api_get_my_instructions(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    login = session["sub"]
    record = await get_user_instructions(login)
    return record or {"login": login, "instructions": ""}


@router.put("/me/instructions")
async def api_put_my_instructions(
    body: UserInstructionsUpdate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    login = session["sub"]
    return await set_user_instructions(login, body.instructions, updated_by=login)


@router.delete("/me/instructions")
async def api_delete_my_instructions(
    session: dict[str, Any] = SESSION,
) -> Response:
    await delete_user_instructions(session["sub"])
    return Response(status_code=204)


@router.get("/options")
async def options() -> dict[str, Any]:
    agent_model, agent_effort = await get_team_default_model("agent")
    subagent_model, subagent_effort = await get_team_default_subagent_model("agent")
    fable_enabled = await get_team_fable_enabled()
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


@router.get("/profile")
async def get_my_profile(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    profile = await get_profile(session["sub"])
    if not profile:
        return {}
    return normalize_profile_for_response(profile)


@router.put("/profile")
async def put_my_profile(
    update: ProfileUpdate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    update.validate_pairing()
    if not await get_team_fable_enabled():
        if (
            update.default_model in FABLE_MODEL_IDS
            or update.default_subagent_model in FABLE_MODEL_IDS
        ):
            raise HTTPException(400, "Fable is disabled for this workspace")
    return await upsert_profile(session["sub"], session.get("email") or "", update)


@router.get("/my-mapping")
async def get_my_mapping(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    """Return the logged-in user's own GitHub↔Slack mapping (or empty)."""
    mapping = await get_mapping(session["sub"])
    return mapping or {}
