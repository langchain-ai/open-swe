import base64

import pytest
from fastapi import HTTPException

from agent.dashboard import thread_api
from agent.dashboard.options import model_supports_images

_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_VISION_MODEL = "openai:gpt-5.5"


def _image() -> thread_api.DashboardImageBody:
    return thread_api.DashboardImageBody(
        base64=base64.b64encode(b"image").decode("ascii"),
        mimeType="image/png",
    )


def test_model_supports_images_marks_text_only_fireworks_models() -> None:
    assert not model_supports_images(_TEXT_ONLY_MODEL)
    assert model_supports_images(_VISION_MODEL)


def test_user_message_content_rejects_images_for_text_only_model() -> None:
    with pytest.raises(HTTPException) as exc_info:
        thread_api._user_message_content("see attached", [_image()], model_id=_TEXT_ONLY_MODEL)

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail


def test_user_message_content_allows_images_for_vision_model() -> None:
    content = thread_api._user_message_content("see attached", [_image()], model_id=_VISION_MODEL)

    assert isinstance(content, list)
    assert content[-1] == {"type": "text", "text": "see attached"}
    assert any(block.get("type") != "text" for block in content)


async def test_resolve_agent_model_choice_applies_profile_before_team_default(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    model_id, effort = await thread_api._resolve_agent_model_choice(
        {"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
        None,
        None,
    )

    assert (model_id, effort) == (_TEXT_ONLY_MODEL, "high")


async def test_resolve_agent_model_choice_applies_request_before_profile(monkeypatch) -> None:
    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    model_id, effort = await thread_api._resolve_agent_model_choice(
        {"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"},
        "anthropic:claude-opus-4-8",
        "high",
    )

    assert (model_id, effort) == ("anthropic:claude-opus-4-8", "high")


async def test_create_dashboard_thread_rejects_images_for_resolved_text_only_model(
    monkeypatch,
) -> None:
    async def fake_profile(login: str) -> dict[str, str]:
        assert login == "octocat"
        return {"default_model": _TEXT_ONLY_MODEL, "reasoning_effort": "high"}

    async def fake_team_default(role: str) -> tuple[str, str]:
        assert role == "agent"
        return _VISION_MODEL, "medium"

    monkeypatch.setattr(thread_api, "get_profile", fake_profile)
    monkeypatch.setattr(thread_api, "get_team_default_model", fake_team_default)

    body = thread_api.ThreadCreateBody(prompt="see attached", images=[_image()])
    with pytest.raises(HTTPException) as exc_info:
        await thread_api.create_dashboard_thread("octocat", body)

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail
