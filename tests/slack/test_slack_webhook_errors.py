from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.run_config import Repo
from openswe.slack import failures as slack_failures
from openswe.slack import webhook as slack_webhook
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.request import SlackRequest
from openswe.webhooks import common as webhook_common


class _FakeThreads:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, "metadata": metadata})


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


def _event_data() -> SlackRequest:
    return SlackRequest(
        channel_id="C1",
        thread_ts="123.45",
        event_ts="123.45",
        user_id="U1",
        text="help",
        bot_user_id="BOT",
    )


@pytest.mark.asyncio
async def test_slack_processing_error_replies_even_without_an_agent_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_processing(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
        raise RuntimeError("boom")

    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", fail_processing)
    monkeypatch.setattr(
        slack_webhook.common, "lookup_slack_thread_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: _FakeClient())
    monkeypatch.setattr(slack_failures, "post_slack_thread_reply", post_reply)

    await slack_webhook.process_slack_mention(
        _event_data(),
        webhook_common.SlackRepoResolution(Repo(owner="langchain-ai", name="open-swe")),
    )

    post_reply.assert_awaited_once()
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "123.45")
    assert await_args.kwargs["agent_thread_id"] is None


@pytest.mark.asyncio
async def test_private_dm_does_not_dispatch_when_privacy_metadata_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_webhook.common, "authorize_github_thread", AsyncMock(return_value={}))
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: _FakeClient())
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_info", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_names", AsyncMock(return_value={}))
    monkeypatch.setattr(
        slack_webhook.common, "resolve_slack_links_in_context", AsyncMock(return_value=("", []))
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_slack", AsyncMock(return_value="alice"))
    monkeypatch.setattr(
        slack_webhook.common, "get_valid_access_token", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(slack_webhook.common, "thread_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(slack_webhook.common, "get_thread_workspace", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common, "get_thread_model_choice", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        slack_webhook.common, "upsert_agent_thread_metadata", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(slack_webhook, "_dispatch_or_queue_slack_run", dispatch)

    with pytest.raises(RuntimeError):
        await slack_webhook._process_slack_mention_impl(
            SlackRequest(
                channel_id="D1",
                channel_context={"is_im": True},
                thread_ts="1.0",
                event_ts="1.0",
                user_id="U1",
                text="hello",
                bot_user_id="BOT",
                thread_id="t1",
            ),
            webhook_common.SlackRepoResolution(
                Repo(owner="langchain-ai", name="open-swe"), explicit=True
            ),
        )
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_errored_dm_owner_falls_back_to_email_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_webhook, "get_langgraph_client", lambda: _FakeClient())
    monkeypatch.setattr(
        slack_webhook.common, "strip_bot_mention", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_slack", AsyncMock(return_value=None))
    monkeypatch.setattr(
        slack_webhook.common,
        "get_slack_user_info",
        AsyncMock(return_value={"profile": {"email": "alice@example.com"}}),
    )
    monkeypatch.setattr(slack_webhook.User, "login_for_email", AsyncMock(return_value="alice"))

    request = _event_data().model_copy(update={"channel_context": SlackChannelContext(is_im=True)})
    await slack_webhook._mark_slack_thread_errored("t1", request, None)

    kwargs = upsert.await_args.kwargs
    assert kwargs["visibility"] == "private"
    assert kwargs["owner_login"] == "alice"

    # An unlinked DM sender never reaches thread creation, so the error path
    # must not leave a private thread nobody owns.
    upsert.reset_mock()
    monkeypatch.setattr(slack_webhook.User, "login_for_email", AsyncMock(return_value=None))
    await slack_webhook._mark_slack_thread_errored("t1", request, None)
    upsert.assert_not_awaited()
