import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from starlette.requests import Request

from agent import model_catalog
from agent.dashboard import options_routes
from agent.dashboard.options import (
    SUPPORTED_MODEL_IDS,
    model_supports_effort,
    update_catalog_options,
)
from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.utils.model import provider_model_kwargs


def test_catalog_models_use_supported_provider_thinking_modes():
    assert "anthropic:claude-opus-4-5" not in model_catalog.CATALOG
    for model_id in ("google_genai:gemini-flash-latest", "google_genai:gemini-flash-lite-latest"):
        for effort in ("low", "high"):
            assert (
                provider_model_kwargs(model_id, effort, max_tokens=1000)["thinking_level"] == effort
            )


@pytest.mark.asyncio
async def test_refresh_updates_imported_registry_and_keeps_last_snapshot_on_failure(
    monkeypatch, tmp_path
):
    payload = json.loads(model_catalog.RESOURCE.read_text())
    model: dict[str, object] = dict(payload["openai"]["models"]["gpt-6.1-sol"], id="future-sol")
    model["reasoning_options"] = [{"type": "effort", "values": ["high", "max"]}]
    payload["openai"]["models"]["future-sol"] = model
    original = dict(model_catalog.CATALOG)
    response = httpx2.Response(
        200, json=payload, request=httpx2.Request("GET", "https://models.dev/api.json")
    )
    monkeypatch.setattr(model_catalog, "CACHE", tmp_path / "models.json")
    monkeypatch.setattr(model_catalog, "_last_refresh", float("-inf"))
    monkeypatch.setattr(httpx2.AsyncClient, "get", AsyncMock(return_value=response))
    try:
        assert await model_catalog.refresh_catalog()
        assert "openai:future-sol" in SUPPORTED_MODEL_IDS
        assert model_supports_effort("openai:future-sol", "max")
        assert not model_supports_effort("openai:future-sol", "medium")
        monkeypatch.setattr(model_catalog, "_last_refresh", float("-inf"))
        monkeypatch.setattr(
            httpx2.AsyncClient, "get", AsyncMock(side_effect=httpx2.ConnectError("offline"))
        )
        assert not await model_catalog.refresh_catalog()
        assert "openai:future-sol" in SUPPORTED_MODEL_IDS
        assert model_catalog.CACHE.exists()
    finally:
        model_catalog.CATALOG.clear()
        model_catalog.CATALOG.update(original)
        update_catalog_options()


@pytest.mark.asyncio
async def test_released_desktop_receives_only_legacy_models_and_defaults(monkeypatch):
    monkeypatch.setattr(options_routes, "refresh_catalog", AsyncMock())
    monkeypatch.setattr(
        options_routes,
        "get_workspace_settings",
        AsyncMock(
            return_value=WorkspaceSettings(
                {
                    "default_agent_model": "openai:gpt-6.1-sol",
                    "default_agent_reasoning_effort": "max",
                    "default_agent_subagent_model": "openai:gpt-6.1-sol",
                    "default_agent_subagent_reasoning_effort": "max",
                }
            )
        ),
    )
    request = Request({"type": "http", "headers": [(b"origin", b"open-swe://app")]})
    legacy = await options_routes.options(request)
    assert "openai:gpt-6.1-sol" not in {m["id"] for m in legacy["models"]}
    assert any(m["id"] == "openai:gpt-6-sol" for m in legacy["models"])
    for model in legacy["models"]:
        assert all(model_supports_effort(model["id"], e) for e in model["efforts"])
    for role in ("agent", "agent_subagent"):
        model = next(m for m in legacy["models"] if m["id"] == legacy[f"default_{role}_model"])
        assert legacy[f"default_{role}_reasoning_effort"] in model["efforts"]
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"origin", b"open-swe://app"),
                (b"user-agent", b"open-swe-desktop/0.2.13 Chrome/153"),
            ],
        }
    )
    released = await options_routes.options(request)
    sol = next(m for m in released["models"] if m["id"] == "openai:gpt-6.1-sol")
    assert "none" not in sol["efforts"]
    assert "max" not in sol["efforts"]
    request = Request(
        {
            "type": "http",
            "headers": [(b"origin", b"open-swe://app"), (b"x-open-swe-model-catalog", b"1")],
        }
    )
    current = await options_routes.options(request)
    sol = next(m for m in current["models"] if m["id"] == "openai:gpt-6.1-sol")
    assert "max" in sol["efforts"]
    assert "none" not in sol["efforts"]
