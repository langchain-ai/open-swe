"""A `/btw` question it can't answer in one go continues in the asker's DM."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.slack import by_the_way
from openswe.slack.tools import by_the_way as btw_tools
from openswe.slack.tools import reply as slack_reply

_ASKED_IN = {
    "slack_ask": True,
    "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
    "slack_by_the_way_thread_ts": "100.1",
    "slack_by_the_way_message_ts": "100.2",
}
_IN_DM = {
    "slack_ask": False,
    "slack_by_the_way_thread_ts": "",
    "slack_thread": {"channel_id": "D1", "thread_ts": "200.1", "triggering_user_id": "U1"},
}


def _run(monkeypatch: pytest.MonkeyPatch, configurable: dict[str, Any]) -> None:
    monkeypatch.setattr(
        "openswe.run_config.get_config",
        lambda: {"configurable": {"thread_id": "t-1", **configurable}},
    )


@pytest.mark.asyncio
async def test_a_btw_question_continues_in_dm_and_is_answered_once_where_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata: dict[str, Any] = {"source_context": dict(_ASKED_IN)}

    async def update(_thread_id: str, values: dict[str, Any]) -> None:
        metadata.update(values)

    monkeypatch.setattr(
        by_the_way.index, "get", AsyncMock(return_value=SimpleNamespace(metadata=metadata))
    )
    monkeypatch.setattr(by_the_way.THREADS, "update", update)
    post_dm = AsyncMock(return_value=("D1", "200.1"))
    bind = AsyncMock()
    monkeypatch.setattr(btw_tools, "post_dm", post_dm)
    monkeypatch.setattr(btw_tools, "bind_slack_thread_id", bind)
    monkeypatch.setattr(btw_tools, "get_slack_permalink", AsyncMock(return_value="https://p"))

    _run(monkeypatch, {**_ASKED_IN, "slack_by_the_way_thread_ts": ""})  # `/oswe`, not `/btw`
    assert (await btw_tools.slack_btw_ask_in_dm("Which one?"))["success"] is False
    _run(monkeypatch, _ASKED_IN)
    assert (await btw_tools.slack_btw_ask_in_dm("Which environment?"))["success"] is True
    assert post_dm.await_args.args[0] == "U1"
    bind.assert_awaited_once_with("D1", "200.1", "t-1")
    assert (await btw_tools.slack_btw_ask_in_dm("Again?"))["success"] is False

    # Only the asker's own replies in that DM thread continue the question.
    assert await by_the_way.ByTheWayDm.at("t-1", "D1", "200.1", "U1") is not None
    assert await by_the_way.ByTheWayDm.at("t-1", "D1", "200.1", "U2") is None
    assert await by_the_way.ByTheWayDm.at("t-1", "D1", "999.9", "U1") is None

    post = AsyncMock(return_value="100.9")
    monkeypatch.setattr(slack_reply.event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_reply, "post_slack_thread_reply_with_ts", post)
    # Gone once `/btw` replies "Working.." instead of reacting (#4755).
    monkeypatch.setattr(slack_reply, "remove_slack_reaction", AsyncMock(), raising=False)
    monkeypatch.setattr(slack_reply, "settle_slack_thread_status", AsyncMock())
    _run(monkeypatch, {**_IN_DM, "slack_thread": {"channel_id": "D1", "thread_ts": "300.1"}})
    assert (await btw_tools.slack_btw_answer("Staging"))["success"] is False
    _run(monkeypatch, _IN_DM)

    assert (await btw_tools.slack_btw_answer("Staging runs it first."))["success"] is True
    assert post.await_args.args[:3] == ("C1", "100.1", "Staging runs it first.")
    # Detached: nothing more from this DM reaches the thread it was asked in.
    assert metadata[by_the_way.METADATA_KEY]["answered"] is True
    assert (await btw_tools.slack_btw_answer("More"))["success"] is False
    post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("answered", [False, True])
async def test_a_reply_in_the_dm_answers_there(
    monkeypatch: pytest.MonkeyPatch, answered: bool
) -> None:
    from openswe.run_config import RunConfig
    from openswe.slack import webhook as slack_webhook
    from openswe.slack.payloads import SlackChannelContext
    from openswe.slack.request import SlackRequest
    from tests.slack.test_slack_context import _setup_slack_mention_fakes

    _setup_slack_mention_fakes(monkeypatch, {})
    dm = by_the_way.ByTheWayDm(
        channel_id="D1", thread_ts="200.1", question="Which environment?", answered=answered
    )
    monkeypatch.setattr(slack_webhook.ByTheWayDm, "at", AsyncMock(return_value=dm))
    dispatch = AsyncMock(return_value={})
    monkeypatch.setattr(slack_webhook, "_dispatch_or_queue_slack_run", dispatch)
    request = SlackRequest(
        channel_id="D1",
        thread_ts="200.1",
        event_ts="200.5",
        thread_id="t-1",
        user_id="U1",
        text="staging",
        bot_user_id="UBOT",
        channel_context=SlackChannelContext(id="D1", is_im=True),
        treat_all_messages_as_mentions=True,
    )

    await slack_webhook._process_slack_mention_impl(request, None)

    assert dispatch.await_args is not None
    config = RunConfig.parse(dispatch.await_args.args[3])
    assert config.slack_ask is False
    assert not config.slack_by_the_way_thread_ts
    assert config.slack_btw_follow_up is (not answered)
    assert ("Which environment?" in str(dispatch.await_args.args[2])) is (not answered)
