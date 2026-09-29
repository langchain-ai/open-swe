"""Selectable models and the defaults a workspace resolves to, offered to the profile editor."""

import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from agent.dashboard.options import (
    FABLE_MODEL_IDS,
    SUPPORTED_MODELS,
    ModelOption,
    gate_fable_model,
    models_with_profile_context_windows,
)
from agent.dashboard.workspace_settings import get_workspace_settings
from agent.model_catalog import refresh_catalog
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, slugify

router = APIRouter(tags=["options"])


@router.get("/options")
async def options(request: Request, workspace: str = DEFAULT_WORKSPACE_SLUG) -> dict[str, Any]:
    """The models and defaults a composer may offer for ``workspace``.

    Model defaults and the Fable flag resolve per workspace (the instance record
    plus the workspace's overrides), so a picker that asked without one would
    advertise the default workspace's values wherever the run will not land there.
    """
    await refresh_catalog()
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
    models: list[ModelOption] = (
        SUPPORTED_MODELS
        if fable_enabled
        else [m for m in SUPPORTED_MODELS if m["id"] not in FABLE_MODEL_IDS]
    )
    if (
        request.headers.get("origin") == "open-swe://app"
        and request.headers.get("x-open-swe-model-catalog") != "1"
    ):
        snapshots = cast(
            dict[str, list[ModelOption]],
            json.loads(
                (Path(__file__).parents[1] / "resources" / "desktop-models-legacy.json").read_text()
            ),
        )
        user_agent = request.headers.get("user-agent", "")
        version = next(
            (
                version
                for version in snapshots
                if f"open-swe-desktop/{version} " in user_agent
                or f"Open-SWE/{version} " in user_agent
            ),
            "default",
        )
        current = {model["id"]: model for model in models}
        models = [
            {
                **model,
                "efforts": efforts,
                "default_effort": model["default_effort"]
                if model["default_effort"] in efforts
                else efforts[0],
                "supports_images": current[model["id"]]["supports_images"],
            }
            for model in snapshots[version]
            if model["id"] in current
            and (efforts := [e for e in model["efforts"] if e in current[model["id"]]["efforts"]])
        ]
        agent_model, agent_effort = legacy_model_pair(models, agent_model, agent_effort)
        subagent_model, subagent_effort = legacy_model_pair(models, subagent_model, subagent_effort)
    return {
        "models": models_with_profile_context_windows(models),
        "default_agent_model": agent_model,
        "default_agent_reasoning_effort": agent_effort,
        "default_agent_subagent_model": subagent_model,
        "default_agent_subagent_reasoning_effort": subagent_effort,
    }


def legacy_model_pair(
    models: list[ModelOption], model_id: str, effort: str | None
) -> tuple[str, str]:
    model = next((m for m in models if m["id"] == model_id), None)
    if model is None:
        provider = model_id.split(":", 1)[0]
        model = next(
            (
                m
                for m in models
                if m["id"].startswith(provider + ":") and m.get("can_be_default", True)
            ),
            None,
        )
    if model is None:
        model = next(m for m in models if m.get("can_be_default", True))
    return model["id"], effort if effort in model["efforts"] else model["default_effort"]
