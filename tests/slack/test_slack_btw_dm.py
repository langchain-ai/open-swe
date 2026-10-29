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


class _Threads:
    """One thread's metadata, as the thread index keeps it."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {"source_context": dict(_ASKED_IN)}

    async def get(self, _thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(metadata=self.metadata)

    async def update(self, _thread_id: str, metadata: dict[str, Any]) -> None:
        self.metadata = {**self.metadata, **metadata}


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> _Threads:
    store = _Threads()
    monkeypatch.setattr(by_the_way, "THREADS", store)
    monkeypatch.setattr(by_the_way.index, "get", store.get)
    return store


def _run(monkeypatch: pytest.MonkeyPatch, **configurable: object) -> None:
    monkeypatch.setattr(
        "openswe.run_config.get_config",
        lambda: {"configurable": {"thread_id": "t-1", **configurable}},
    )


@pytest.mark.asyncio
async def test_a_btw_question_asked_in_dm_is_answered_once_where_it_was_asked(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads
) -> None:
    post_dm = AsyncMock(return_value=("D1", "200.1"))
    bind = AsyncMock()
    monkeypatch.setattr(btw_tools, "post_dm", post_dm)
    monkeypatch.setattr(btw_tools, "bind_slack_thread_id", bind)
    monkeypatch.setattr(btw_tools, "get_slack_permalink", AsyncMock(return_value="https://p"))
    _run(monkeypatch, **_ASKED_IN)

    asked = await btw_tools.slack_btw_ask_in_dm("Which environment do you mean?")

    assert asked["success"] is True
    assert post_dm.await_args.args[0] == "U1"
    assert "Which environment do you mean?" in post_dm.await_args.args[1]
    bind.assert_awaited_once_with("D1", "200.1", "t-1")
    assert (await btw_tools.slack_btw_ask_in_dm("again?"))["success"] is False

    # Only the asker's replies in that DM thread continue the question.
    assert await by_the_way.ByTheWayFollowUp.at("t-1", "D1", "200.1", "U1") is not None
    assert await by_the_way.ByTheWayFollowUp.at("t-1", "D1", "200.1", "U2") is None
    assert await by_the_way.ByTheWayFollowUp.at("t-1", "D1", "999.9", "U1") is None
    monkeypatch.setattr(by_the_way, "lookup_slack_thread_id", AsyncMock(return_value="t-1"))
    assert await by_the_way.continues_in_dm("D1", "200.1") is True

    post = AsyncMock(return_value="100.9")
    remove_reaction = AsyncMock()
    monkeypatch.setattr(slack_reply.event_claims, "claim", AsyncMock(side_effect=[True, False]))
    monkeypatch.setattr(slack_reply, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(slack_reply, "remove_slack_reaction", remove_reaction)
    monkeypatch.setattr(slack_reply, "settle_slack_thread_status", AsyncMock())
    _run(
        monkeypatch,
        slack_ask=False,
        slack_by_the_way_thread_ts="",
        slack_btw_follow_up=True,
        slack_thread={"channel_id": "D1", "thread_ts": "200.1", "triggering_user_id": "U1"},
    )

    answered = await btw_tools.slack_btw_answer("Staging runs the migration first.")

    assert answered["success"] is True
    assert post.await_args.args == ("C1", "100.1", "Staging runs the migration first.")
    remove_reaction.assert_awaited_once_with("C1", "100.2", "hourglass_flowing_sand")
    # Detached: nothing more from this DM reaches the thread it was asked in.
    assert threads.metadata[by_the_way.METADATA_KEY]["answered"] is True
    assert (await btw_tools.slack_btw_answer("more"))["success"] is False
    assert await by_the_way.continues_in_dm("D1", "200.1") is False
    post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configurable",
    [
        {**_ASKED_IN, "slack_by_the_way_thread_ts": ""},
        {**_ASKED_IN, "slack_ask": False},
    ],
    ids=["oswe-command", "not-an-ask"],
)
async def test_only_a_btw_question_moves_to_a_dm(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads, configurable: dict[str, object]
) -> None:
    post_dm = AsyncMock()
    monkeypatch.setattr(btw_tools, "post_dm", post_dm)
    _run(monkeypatch, **configurable)

    assert (await btw_tools.slack_btw_ask_in_dm("Which one?"))["success"] is False
    post_dm.assert_not_awaited()
    assert by_the_way.METADATA_KEY not in threads.metadata


@pytest.mark.asyncio
async def test_the_answer_posts_only_from_the_dm_the_question_continues_in(
    monkeypatch: pytest.MonkeyPatch, threads: _Threads
) -> None:
    threads.metadata[by_the_way.METADATA_KEY] = {"channel_id": "D1", "thread_ts": "200.1"}
    post = AsyncMock()
    monkeypatch.setattr(slack_reply, "post_slack_thread_reply_with_ts", post)
    _run(monkeypatch, slack_thread={"channel_id": "D1", "thread_ts": "300.1"})

    assert (await btw_tools.slack_btw_answer("answer"))["success"] is False
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("answered", [False, True])
async def test_a_reply_in_the_dm_answers_there_not_where_the_question_was_asked(
    monkeypatch: pytest.MonkeyPatch, answered: bool
) -> None:
    from openswe.run_config import RunConfig
    from openswe.slack import webhook as slack_webhook
    from openswe.slack.payloads import SlackChannelContext
    from openswe.slack.request import SlackRequest
    from openswe.source_context import SourceContext
    from tests.slack.test_slack_context import _setup_slack_mention_fakes

    _setup_slack_mention_fakes(monkeypatch, {})
    follow_up = by_the_way.ByTheWayFollowUp(
        dm=by_the_way.ByTheWayDm(
            channel_id="D1", thread_ts="200.1", question="Which environment?", answered=answered
        ),
        asked_in=SourceContext.model_validate(_ASKED_IN),
    )
    monkeypatch.setattr(slack_webhook.ByTheWayFollowUp, "at", AsyncMock(return_value=follow_up))
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
