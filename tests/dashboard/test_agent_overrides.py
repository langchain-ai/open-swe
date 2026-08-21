"""Which model a run gets, and which models the dashboard offers.

``resolve_agent_model_id`` layers a per-thread override over the user's profile
over the team default; ``/options`` and ``/profile`` decide what the UI may pick
from and migrate anything the team has since retired.
"""

from unittest.mock import AsyncMock, patch

from agent.dashboard import routes
from agent.dashboard.agent_overrides import resolve_agent_model_id
from agent.dashboard.options import model_supports_images

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.6-sol"
_FABLE = "anthropic:claude-fable-5"
_PAIR = (_VISION_MODEL, "medium")


def test_model_supports_images_marks_text_only_fireworks_models() -> None:
    assert not model_supports_images(_TEXT_ONLY_MODEL)
    assert model_supports_images(_VISION_MODEL)


async def test_resolve_agent_model_id_defaults_to_team_default(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(None)
    assert model_id == _TEXT_ONLY_MODEL


async def test_resolve_agent_model_id_applies_profile_override(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)

    async def fake_load_profile(login: str) -> dict:
        return {"default_model": _VISION_MODEL, "reasoning_effort": "medium"}

    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", fake_load_profile)

    model_id = await resolve_agent_model_id("someuser")
    assert model_id == _VISION_MODEL


async def test_resolve_agent_model_id_applies_per_thread_override(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(None, per_thread_model_id="anthropic:claude-opus-5")
    assert model_id == "anthropic:claude-opus-5"


async def test_resolve_agent_model_id_migrates_deprecated_per_thread_override(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        return _TEXT_ONLY_MODEL, "high"

    monkeypatch.setattr("agent.dashboard.agent_overrides.get_team_default_model", fake_team_default)
    monkeypatch.setattr("agent.dashboard.agent_overrides.load_profile", lambda login: None)

    model_id = await resolve_agent_model_id(None, per_thread_model_id="openai:gpt-5.5")
    assert model_id == "openai:gpt-5.6-sol"


async def test_get_my_profile_migrates_deprecated_models() -> None:
    with patch(
        "agent.dashboard.routes.get_profile",
        new_callable=AsyncMock,
        return_value={
            "default_model": "openai:gpt-5.5",
            "reasoning_effort": "medium",
            "default_subagent_model": "anthropic:claude-opus-4-8",
            "subagent_reasoning_effort": "low",
        },
    ):
        payload = await routes.get_my_profile({"sub": "octocat"})

    assert payload["default_model"] == "openai:gpt-5.6-sol"
    assert payload["reasoning_effort"] == "medium"
    assert payload["default_subagent_model"] == "anthropic:claude-opus-5"
    assert payload["subagent_reasoning_effort"] == "low"


async def test_options_omits_fable_when_disabled() -> None:
    with (
        patch(
            "agent.dashboard.routes.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_subagent_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
    ):
        payload = await routes.options()
    assert _FABLE not in [m["id"] for m in payload["models"]]


async def test_options_includes_fable_when_enabled() -> None:
    with (
        patch(
            "agent.dashboard.routes.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_subagent_model",
            new_callable=AsyncMock,
            return_value=_PAIR,
        ),
    ):
        payload = await routes.options()
    assert _FABLE in [m["id"] for m in payload["models"]]
    openai_model = next(m for m in payload["models"] if m["id"] == _VISION_MODEL)
    assert openai_model["context_window"] == 272_000


async def test_options_gates_stale_fable_default_when_disabled() -> None:
    # A stale Fable team default must not be advertised as the default while Fable
    # is omitted from the selectable list, or the Cloud Agents page would offer a
    # default that PUT /profile then rejects.
    fable_pair = (_FABLE, "high")
    with (
        patch(
            "agent.dashboard.routes.get_team_fable_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_model",
            new_callable=AsyncMock,
            return_value=fable_pair,
        ),
        patch(
            "agent.dashboard.routes.get_team_default_subagent_model",
            new_callable=AsyncMock,
            return_value=fable_pair,
        ),
    ):
        payload = await routes.options()
    model_ids = [m["id"] for m in payload["models"]]
    assert _FABLE not in model_ids
    assert payload["default_agent_model"] != _FABLE
    assert payload["default_agent_subagent_model"] != _FABLE
    assert payload["default_agent_model"] in model_ids
    assert payload["default_agent_subagent_model"] in model_ids
