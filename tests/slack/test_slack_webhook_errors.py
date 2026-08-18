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
    post_reply = AsyncMock(return_value=True)

    monkeypatch.setattr(slack_webhook, "_process_slack_mention_impl", fail_processing)
    monkeypatch.setattr(
        slack_webhook.common, "lookup_slack_thread_id", AsyncMock(return_value="t1")
    )
    monkeypatch.setattr(
        slack_webhook.common, "strip_bot_mention", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_owner_metadata", upsert)
    monkeypatch.setattr(slack_webhook.common, "get_client", lambda *, url: client)
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
    post_reply.assert_awaited_once()
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "123.45")
    assert "<https://ui/t1|Open SWE Web>" in await_args.args[2]


async def test_non_owner_natural_language_plan_approval_uses_shared_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {"plan_mode": True, "plan_status": "ready"}
    load_metadata = AsyncMock(return_value=metadata)
    approve = AsyncMock(return_value={"status": "approved", "run_id": "run-1"})

    monkeypatch.setattr(plan_api, "_thread_metadata", load_metadata)
    monkeypatch.setattr(plan_api, "approve_plan_for_thread", approve)

    handled = await slack_webhook._maybe_approve_ready_plan_reply(
        "t1", "C1", "123.45", "U1", "Alice", "Looks good!"
    )

    assert handled is True
    load_metadata.assert_awaited_once_with("t1")
    approve.assert_awaited_once_with(
        "t1",
        approver={"id": "U1", "name": "Alice", "source": "slack"},
    )


async def test_approval_phrase_on_missing_thread_continues_as_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_api, "_thread_metadata", AsyncMock(side_effect=RuntimeError("missing"))
    )

    handled = await slack_webhook._maybe_approve_ready_plan_reply(
        "new-thread", "C1", "123.45", "U1", "Alice", "please implement issue 123"
    )

    assert handled is False


async def test_non_owner_can_send_untagged_ready_plan_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_api,
        "_thread_metadata",
        AsyncMock(return_value={"plan_mode": True, "plan_status": "ready"}),
    )
    lookup_thread = AsyncMock(return_value="t1")
    monkeypatch.setattr(slack_webhook.common, "lookup_slack_thread_id", lookup_thread)

    allowed = await slack_webhook._slack_user_can_reply_to_ready_plan("C1", "123.45", "U2")

    assert allowed is True
    lookup_thread.assert_awaited_once()


@pytest.mark.parametrize("reply", ["LGTM, thanks", "Looks good — please implement this"])
async def test_plan_approval_allows_polite_surrounding_words(
    monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    monkeypatch.setattr(
        plan_api,
        "_thread_metadata",
        AsyncMock(return_value={"plan_mode": True, "plan_status": "ready"}),
    )
    approve = AsyncMock(return_value={"status": "approved", "run_id": "run-1"})
    monkeypatch.setattr(plan_api, "approve_plan_for_thread", approve)

    assert (
        await slack_webhook._maybe_approve_ready_plan_reply(
            "t1", "C1", "123.45", "U1", "Alice", reply
        )
        is True
    )
    approve.assert_awaited_once()


async def test_duplicate_plan_approval_returns_shared_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {"plan_mode": True, "plan_status": "ready"}
    monkeypatch.setattr(plan_api, "_thread_metadata", AsyncMock(return_value=metadata))
    approve_mock = AsyncMock(
        side_effect=[
            {"status": "approved", "run_id": "run-1"},
            {"status": "approved", "already_approved": True},
        ]
    )
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
    assert approve_mock.await_count == 2


async def test_slack_plan_button_failure_notifies_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approve = AsyncMock(side_effect=RuntimeError("store down"))
    notify = AsyncMock()
    monkeypatch.setattr(slack_webhook, "_maybe_approve_ready_plan_reply", approve)
    monkeypatch.setattr(slack_webhook, "_notify_slack_processing_error", notify)
    event_data = {
        "thread_id": "t1",
        "channel_id": "C1",
        "thread_ts": "123.45",
        "user_id": "U1",
        "user_name": "Alice",
    }
    repo_config = {"owner": "langchain-ai", "name": "open-swe"}

    await slack_webhook.process_slack_plan_approval(event_data, repo_config)

    notify.assert_awaited_once_with(event_data, repo_config)


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


def _thread(*users: str) -> list[dict[str, Any]]:
    return [{"ts": f"1.{i}", "user": user} for i, user in enumerate(users)]


@pytest.mark.asyncio
async def test_untagged_reply_allowed_for_two_party_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _thread("UHUMAN", "BOT", "UHUMAN")
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "no worries, keep going", "BOT"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_when_mentioning_other_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _thread("UHUMAN", "BOT")
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "hey <@UOTHER> can you look?", "BOT"
    )


@pytest.mark.asyncio
async def test_untagged_reply_mentioning_only_bot_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _thread("UHUMAN", "BOT")
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "<@BOT> keep going", "BOT"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_for_three_party_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _thread("UHUMAN", "BOT", "USECOND")
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "keep going", "BOT"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_when_bot_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _thread("UHUMAN", "UHUMAN")
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "keep going", "BOT"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_when_only_third_party_bot_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One human + a GitHub/CI bot reply, but no Open SWE message: not a two-party
    # Open SWE thread, so an untagged follow-up must not start a run.
    messages = [
        {"ts": "1.0", "user": "UHUMAN"},
        {"ts": "1.1", "user": "UGH", "bot_id": "BGITHUB"},
    ]
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "keep going", "BOT"
    )


@pytest.mark.asyncio
async def test_dispatch_or_queue_enqueues_untagged_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(slack_webhook.common, "dispatch_agent_run", dispatch)

    blocks = [{"type": "text", "text": "follow up"}]
    run = await slack_webhook._dispatch_or_queue_slack_run(
        _FakeClient(),
        "t1",
        blocks,
        {},
        explicitly_tagged=False,
    )

    assert run == {"run_id": "run-1"}
    await_args = dispatch.await_args
    assert await_args is not None
    assert await_args.args[1] == blocks
    assert await_args.kwargs["multitask_strategy"] == "enqueue"


@pytest.mark.asyncio
async def test_dispatch_or_queue_interrupts_for_explicit_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    monkeypatch.setattr(slack_webhook.common, "dispatch_agent_run", dispatch)

    run = await slack_webhook._dispatch_or_queue_slack_run(
        _FakeClient(),
        "t1",
        [{"type": "text", "text": "<@BOT> stop and do this instead"}],
        {},
        explicitly_tagged=True,
    )

    assert run == {"run_id": "run-1"}
    await_args = dispatch.await_args
    assert await_args is not None
    assert await_args.kwargs["multitask_strategy"] == "interrupt"


def test_message_update_is_non_explicit_even_when_original_mention_remains() -> None:
    assert not slack_webhook._is_explicit_slack_request(
        "<@BOT> corrected request",
        "BOT",
        treat_all_messages_as_mentions=False,
        message_update=True,
    )


def _msg(ts: float, user: str, **extra: Any) -> dict[str, Any]:
    return {"ts": f"{ts:.6f}", "user": user, **extra}


@pytest.mark.asyncio
async def test_untagged_reply_ignores_a_third_party_who_went_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mukil's drive-by emoji must not disable untagged replies forever."""
    base = 1786723299.0
    messages = [
        _msg(base, "URAMON"),
        _msg(base + 6, "BOT"),
        _msg(base + 90, "UMUKIL"),  # went quiet, before the bot's latest reply
        _msg(base + 1331, "BOT"),
    ]
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "why do you not see this message", "BOT", "URAMON", f"{base + 1423:.6f}"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_while_a_third_party_is_still_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1786723299.0
    messages = [
        _msg(base, "URAMON"),
        _msg(base + 6, "BOT"),
        _msg(base + 1400, "UMUKIL"),  # spoke 23s ago — still in the conversation
    ]
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "how about now", "BOT", "URAMON", f"{base + 1423:.6f}"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_when_third_party_spoke_after_the_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale needs both conditions: older than the bot's last reply AND >15 min."""
    base = 1786723299.0
    messages = [
        _msg(base, "URAMON"),
        _msg(base + 6, "BOT"),
        _msg(base + 60, "UMUKIL"),  # after the bot, so never stale however old
    ]
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "ping", "BOT", "URAMON", f"{base + 9999:.6f}"
    )


@pytest.mark.asyncio
async def test_untagged_reply_ignores_joins_and_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1786723299.0
    messages = [
        _msg(base, "URAMON"),
        _msg(base + 6, "BOT"),
        _msg(base + 10, "ULURKER", subtype="channel_join"),
    ]
    monkeypatch.setattr(
        slack_webhook.common, "fetch_slack_thread_messages", AsyncMock(return_value=messages)
    )

    assert await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "keep going", "BOT", "URAMON", f"{base + 20:.6f}"
    )


@pytest.mark.asyncio
async def test_untagged_reply_blocked_when_bot_never_posted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1786723299.0
    monkeypatch.setattr(
        slack_webhook.common,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=[_msg(base, "URAMON")]),
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "hello", "BOT", "URAMON", f"{base + 5:.6f}"
    )


@pytest.mark.asyncio
async def test_rapid_follow_up_allowed_before_bot_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    base = 1786723299.0
    monkeypatch.setattr(
        slack_webhook.common,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=[_msg(base, "URAMON", text="<@BOT> start this")]),
    )

    assert await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "one more detail", "BOT", "URAMON", f"{base + 60:.6f}"
    )


@pytest.mark.asyncio
async def test_each_rapid_follow_up_extends_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    base = 1786723299.0
    messages = [
        _msg(base, "URAMON", text="<@BOT> start this"),
        _msg(base + 55, "URAMON", text="first detail"),
        _msg(base + 110, "URAMON", text="second detail"),
    ]
    monkeypatch.setattr(
        slack_webhook.common,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=messages),
    )

    assert await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "third detail", "BOT", "URAMON", f"{base + 165:.6f}"
    )


@pytest.mark.asyncio
async def test_rapid_follow_up_window_expires_after_latest_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1786723299.0
    messages = [
        _msg(base, "URAMON", text="<@BOT> start this"),
        _msg(base + 55, "URAMON", text="first detail"),
    ]
    monkeypatch.setattr(
        slack_webhook.common,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=messages),
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "too late", "BOT", "URAMON", f"{base + 116:.6f}"
    )


@pytest.mark.asyncio
async def test_rapid_follow_up_blocked_for_another_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1786723299.0
    monkeypatch.setattr(
        slack_webhook.common,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=[_msg(base, "URAMON", text="<@BOT> start this")]),
    )

    assert not await slack_webhook._slack_thread_allows_untagged_reply(
        "C1", "123.45", "I have a detail", "BOT", "UOTHER", f"{base + 10:.6f}"
    )


@pytest.mark.asyncio
async def test_message_update_dispatches_a_new_message_without_old_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    fetch_messages = AsyncMock(return_value=[{"ts": "1.0", "user": "U1", "text": "old text"}])
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    store_mapping = AsyncMock()
    monkeypatch.setattr(slack_webhook.common, "get_client", lambda *, url: client)
    monkeypatch.setattr(slack_webhook.common, "refresh_user_mapping_cache", AsyncMock())
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_info", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_webhook.common, "fetch_slack_thread_messages", fetch_messages)
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_names", AsyncMock(return_value={}))
    monkeypatch.setattr(
        slack_webhook.common,
        "resolve_slack_links_in_context",
        AsyncMock(return_value=("", [])),
    )
    monkeypatch.setattr(
        slack_webhook, "_format_slack_run_links_section", AsyncMock(return_value="")
    )
    monkeypatch.setattr(slack_webhook.common, "login_for_slack_id", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_webhook.common, "is_bot_token_only_mode", lambda: True)
    monkeypatch.setattr(slack_webhook.common, "_thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(
        slack_webhook.common, "_get_thread_environment", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(slack_webhook.common, "_get_thread_plan_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(slack_webhook.common, "_upsert_slack_thread_repo_metadata", AsyncMock())
    monkeypatch.setattr(slack_webhook.common, "upsert_agent_thread_owner_metadata", AsyncMock())
    monkeypatch.setattr(slack_webhook, "_dispatch_or_queue_slack_run", dispatch)
    monkeypatch.setattr(slack_webhook.common, "store_slack_run_mapping", store_mapping)

    await slack_webhook._process_slack_mention_impl(
        {
            "channel_id": "C1",
            "channel_context": {},
            "thread_ts": "1.0",
            "event_ts": "2.0",
            "original_message_ts": "1.0",
            "user_id": "U1",
            "text": "new corrected text",
            "bot_user_id": "BOT",
            "thread_id": "t1",
            "message_update": True,
        },
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    fetch_messages.assert_not_awaited()
    await_args = dispatch.await_args
    assert await_args is not None
    content_blocks = await_args.args[2]
    prompt = content_blocks[0]["text"]
    assert "new corrected text" in prompt
    assert "old text" not in prompt
    assert "## Conversation Context" not in prompt
    assert await_args.kwargs["explicitly_tagged"] is False
    store_args = store_mapping.await_args
    assert store_args is not None
    assert store_args.kwargs["message_ts"] == "1.0"
    assert store_args.kwargs["agent_thread_id"] == "t1"


def test_untagged_prompt_tells_the_agent_it_was_not_tagged() -> None:
    preamble = slack_webhook._slack_prompt_preamble(untagged_reply=True)

    assert "You were NOT tagged" in preamble
    assert "call `no_op` and post nothing" in preamble
    assert "Staying silent is the right" in preamble
    assert slack_webhook._slack_request_heading(untagged_reply=True) == "## Untagged Message"


def test_tagged_prompt_keeps_the_mention_wording() -> None:
    preamble = slack_webhook._slack_prompt_preamble(untagged_reply=False)

    assert preamble == "You were mentioned in Slack.\n\n"
    assert "NOT tagged" not in preamble
    assert slack_webhook._slack_request_heading(untagged_reply=False) == "## Latest Mention Request"
