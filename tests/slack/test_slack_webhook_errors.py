import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.dashboard import plan_api
from agent.webhooks import slack as slack_webhook


class _FakeThreads:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append({"thread_id": thread_id, "metadata": metadata})


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


@pytest.mark.asyncio
async def test_slack_processing_error_posts_dashboard_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_processing(event_data: dict[str, Any], repo_config: dict[str, str]) -> None:
        raise RuntimeError("boom")

    client = _FakeClient()
    upsert = AsyncMock()
    set_status = AsyncMock()
    post_reply = AsyncMock(return_value=True)

    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", fail_processing)
    monkeypatch.setattr(
        slack_webhook.common, "generate_thread_id_from_slack_thread", lambda *_: "t1"
    )
    monkeypatch.setattr(
        slack_webhook.common, "strip_bot_mention", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_owner_metadata", upsert)
    monkeypatch.setattr(slack_webhook.common, "get_client", lambda *, url: client)
    monkeypatch.setattr(slack_webhook.common, "set_slack_assistant_status", set_status)
    monkeypatch.setattr(
        slack_webhook.common, "dashboard_thread_url", lambda thread_id: f"https://ui/{thread_id}"
    )
    monkeypatch.setattr(slack_webhook.common, "post_slack_thread_reply", post_reply)

    await slack_webhook.process_slack_mention(
        {
            "channel_id": "C1",
            "thread_ts": "123.45",
            "event_ts": "123.45",
            "user_id": "U1",
            "text": "help",
            "bot_user_id": "BOT",
        },
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    upsert.assert_awaited_once()
    assert len(client.threads.updates) == 1
    update = client.threads.updates[0]
    assert update["thread_id"] == "t1"
    assert update["metadata"]["latest_run_status"] == "error"
    assert "failure_reply_posted" not in update["metadata"]
    assert isinstance(update["metadata"]["updated_at_ms"], int)
    set_status.assert_awaited_once_with("C1", "123.45", status="")
    post_reply.assert_awaited_once()
    assert post_reply.await_args.args[:2] == ("C1", "123.45")
    assert "<https://ui/t1|Open SWE Web>" in post_reply.await_args.args[2]


async def test_natural_language_plan_approval_uses_shared_approval_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    is_owner = AsyncMock(return_value=True)
    metadata = {"plan_mode": True, "plan_status": "ready"}
    load_metadata = AsyncMock(return_value=metadata)
    approve = AsyncMock(return_value={"status": "approved"})

    monkeypatch.setattr(slack_webhook.common, "_slack_user_is_thread_owner", is_owner)
    monkeypatch.setattr(plan_api, "_thread_metadata", load_metadata)
    monkeypatch.setattr(plan_api, "approve_plan_for_thread", approve)

    handled = await slack_webhook._maybe_approve_ready_plan_reply(
        "t1", "C1", "123.45", "U1", "Alice", "Looks good!"
    )

    assert handled is True
    is_owner.assert_awaited_once_with("t1", "U1")
    load_metadata.assert_awaited_once_with("t1")
    approve.assert_awaited_once_with("t1", metadata=metadata, actor="Alice")


@pytest.mark.parametrize("reply", ["LGTM, thanks", "Looks good — please implement this"])
async def test_plan_approval_allows_polite_surrounding_words(
    monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    monkeypatch.setattr(
        slack_webhook.common, "_slack_user_is_thread_owner", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        plan_api,
        "_thread_metadata",
        AsyncMock(return_value={"plan_mode": True, "plan_status": "ready"}),
    )
    approve = AsyncMock(return_value={"status": "approved"})
    monkeypatch.setattr(plan_api, "approve_plan_for_thread", approve)

    assert (
        await slack_webhook._maybe_approve_ready_plan_reply(
            "t1", "C1", "123.45", "U1", "Alice", reply
        )
        is True
    )
    approve.assert_awaited_once()


async def test_duplicate_plan_approval_dispatches_once(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {"plan_mode": True, "plan_status": "ready"}

    async def approve(*_args: object, **_kwargs: object) -> dict[str, str]:
        metadata.update(plan_mode=False, plan_status="approved")
        return {"status": "approved"}

    monkeypatch.setattr(
        slack_webhook.common, "_slack_user_is_thread_owner", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(plan_api, "_thread_metadata", AsyncMock(return_value=metadata))
    approve_mock = AsyncMock(side_effect=approve)
    monkeypatch.setattr(plan_api, "approve_plan_for_thread", approve_mock)

    results = await asyncio.gather(
        *(
            slack_webhook._maybe_approve_ready_plan_reply(
                "t1", "C1", "123.45", "U1", "Alice", "LGTM"
            )
            for _ in range(2)
        )
    )

    assert results == [True, False]
    approve_mock.assert_awaited_once()


async def test_plan_revision_reply_does_not_trigger_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    is_owner = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_webhook.common, "_slack_user_is_thread_owner", is_owner)

    handled = await slack_webhook._maybe_approve_ready_plan_reply(
        "t1", "C1", "123.45", "U1", "Alice", "Do not approve; revise the tests"
    )

    assert handled is False
    is_owner.assert_not_awaited()
