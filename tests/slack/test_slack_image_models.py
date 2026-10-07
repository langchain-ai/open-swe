from unittest.mock import AsyncMock

import pytest

from openswe.input_messages import RunInput
from openswe.run_config import RunConfig
from openswe.slack import webhook as slack_webhook
from openswe.slack.request import SlackRequest
from openswe.utils.thread_settings import THREAD_SETTINGS_KEY, ThreadSettings
from openswe.webhooks import common as webhook_common
from tests.conftest import FakeStore
from tests.slack.test_slack_context import _setup_slack_mention_fakes

_KIMI = "fireworks:accounts/fireworks/models/kimi-k3"
_VISION = "anthropic:claude-opus-5-5"


@pytest.mark.parametrize(
    ("stored_model", "default_model", "explicit_model", "with_image", "needs_fallback"),
    [
        pytest.param(_KIMI, _VISION, None, True, True, id="pinned-text-only"),
        pytest.param(_VISION, _KIMI, None, True, False, id="pinned-vision"),
        pytest.param(_KIMI, _VISION, _VISION, True, False, id="explicit-vision"),
        pytest.param(None, _KIMI, None, True, True, id="default-text-only"),
        pytest.param(_KIMI, _VISION, None, False, False, id="text-followup"),
    ],
)
async def test_slack_followup_uses_the_threads_model_for_image_capability(
    monkeypatch: pytest.MonkeyPatch,
    fake_store: FakeStore,
    stored_model: str | None,
    default_model: str,
    explicit_model: str | None,
    with_image: bool,
    needs_fallback: bool,
) -> None:
    _setup_slack_mention_fakes(monkeypatch, {})
    client = slack_webhook.get_langgraph_client()
    client.store = fake_store
    settings: ThreadSettings = {}
    if stored_model:
        settings = {
            "model_id": stored_model,
            "effort": "high",
            "requested_model": stored_model,
            "model_handoff_complete": True,
            "model_routing_enabled": False,
        }
    metadata: dict[str, object] = {"visibility": "public", THREAD_SETTINGS_KEY: settings}
    if explicit_model:
        metadata.update(model_selection="explicit", model=explicit_model, effort="high")
    monkeypatch.setattr(client.threads, "get", AsyncMock(return_value={"metadata": metadata}))
    monkeypatch.setattr(
        webhook_common, "resolve_agent_model_id", AsyncMock(return_value=default_model)
    )
    request = SlackRequest(
        channel_id="C123",
        thread_ts="1700000000.000100",
        event_ts="1700000000.000200",
        thread_id="mapped-thread",
        user_id="U123",
        text="<@UBOT> review this screenshot" if with_image else "<@UBOT> continue",
        bot_user_id="UBOT",
    )
    image_url = "https://files.slack.com/screenshot.png"
    image_block = {"type": "image", "url": image_url, "mime_type": "image/png"}
    monkeypatch.setattr(
        webhook_common,
        "fetch_slack_thread_messages",
        AsyncMock(
            return_value=[
                {
                    "ts": request.event_ts,
                    "text": request.text,
                    "user": request.user_id,
                    "files": [{"mimetype": "image/png", "url_private": image_url}]
                    if with_image
                    else [],
                }
            ]
        ),
    )
    monkeypatch.setattr(webhook_common, "fetch_image_block", AsyncMock(return_value=image_block))
    dispatch = AsyncMock(return_value={})
    monkeypatch.setattr(slack_webhook, "_dispatch_or_queue_slack_run", dispatch)

    await slack_webhook._process_slack_mention_impl(request, None)

    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    run_input: RunInput = dispatch.await_args.args[2]
    config = RunConfig.parse(dispatch.await_args.args[3])
    if needs_fallback:
        assert (config.agent_model_id, config.agent_effort) == (
            webhook_common.default_vision_model_pair()
        )
        assert config.model_override_reason == "image_input"
    else:
        assert config.agent_model_id == explicit_model
        assert config.model_override_reason is None
    assert config.model_selection == ("explicit" if explicit_model else None)
    if with_image:
        assert any(
            isinstance(content := message["content"], list) and image_block in content
            for message in run_input["messages"]
        )
