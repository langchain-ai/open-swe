import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_typesafe import Choice, ClassifierResponse

from openswe.slack import ask_messages
from openswe.slack.ask_messages import AskChannelMessage, AskResponse
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.slack.records import SlackThread
from openswe.utils.jev import JevUnavailable
from openswe.utils.json_types import JsonObject
from tests.support.slack_api import SlackAPI


def _jev_answer(action: str | None, reaction: str | None) -> ClassifierResponse:
    """Jev picking `action`, or an unsure Jev leaning quiet when it is None."""
    answers: JsonObject = {
        "action": {
            "type": "choice",
            "choice": action,
            "confidence": 0.9,
            "probabilities": {action: 1.0},
        }
        if action
        else {
            "type": "choice",
            "choice": "do_nothing",
            "confidence": 0.3,
            "probabilities": {"follow_up_only": 0.3, "do_nothing": 0.5, "react_only": 0.2},
        }
    }
    if reaction is not None:
        answers["reaction"] = {
            "type": "choice",
            "choice": reaction,
            "confidence": 0.9,
            "probabilities": {reaction: 1.0},
        }
    return ClassifierResponse.model_validate({"model": "jev", "answers": answers})


@pytest.mark.parametrize(
    ("ask_channel", "choice", "reaction", "reaction_succeeds", "allowed", "posted_emoji"),
    [
        (False, None, None, True, True, None),
        (True, "follow_up_only", "eyes", True, True, None),
        (True, "do_nothing", "eyes", True, False, None),
        (True, "react_only", "woman-tipping-hand", True, False, "woman-tipping-hand"),
        (True, "react_and_follow_up", "saluting_face", True, True, "saluting_face"),
        (True, "react_only", None, True, False, "eyes"),
        (True, "react_only", "invalid", True, False, "eyes"),
        (True, "react_only", "eyes", False, True, "eyes"),
        (True, "react_only", None, False, True, "eyes"),
        (True, "react_and_follow_up", None, True, True, "eyes"),
        (True, "react_and_follow_up", "eyes", False, True, "eyes"),
        (True, None, "eyes", True, False, None),
        (True, "failure", None, True, True, None),
        (True, "reply", None, True, True, None),
    ],
)
async def test_ask_channel_messages_need_jev(
    monkeypatch: pytest.MonkeyPatch,
    ask_channel: bool,
    choice: str | None,
    reaction: str | None,
    reaction_succeeds: bool,
    allowed: bool,
    posted_emoji: str | None,
) -> None:
    async def channel_prompt(_channel_id: str) -> str | None:
        return "Investigate database reports" if ask_channel else None

    async def messages(_channel_id: str, _thread_ts: str, _message_ts: str) -> list[SlackMessage]:
        return [SlackMessage(ts="2.0", text="dude shut up robin")]

    reactions: list[tuple[str, str, str]] = []

    async def react(channel_id: str, message_ts: str, emoji: str) -> bool:
        reactions.append((channel_id, message_ts, emoji))
        return reaction_succeeds

    async def ask(*_args: object, **kwargs: object) -> ClassifierResponse:
        questions = kwargs["questions"]
        assert isinstance(questions, dict)
        decision = questions["action"]
        assert isinstance(decision, Choice)
        assert "Investigate database reports" in decision.instructions
        assert "description-only-routing" not in decision.instructions
        if choice == "failure":
            raise JevUnavailable("classifier_error")
        return _jev_answer(choice, reaction)

    monkeypatch.setattr(ask_messages.INTEGRATIONS, "channel_prompt", channel_prompt)
    monkeypatch.setattr(AskChannelMessage, "_messages", staticmethod(messages))

    async def channel_context(_channel_id: str) -> SlackChannelContext:
        return SlackChannelContext(
            name="help",
            topic="description-only-routing topic",
            purpose="description-only-routing purpose",
            description="description-only-routing description",
        )

    monkeypatch.setattr(ask_messages, "JEV", SimpleNamespace(ask=ask))
    monkeypatch.setattr(ask_messages, "add_slack_reaction", react)
    monkeypatch.setattr(ask_messages.common, "resolve_slack_channel_context", channel_context)

    assert (
        await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="2.0")
        is allowed
    )
    assert reactions == ([("C1", "2.0", posted_emoji)] if posted_emoji else [])


@pytest.fixture
def slack_root_cache(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool | str]:
    from langgraph_api import cache

    values: dict[str, bool | str] = {}
    monkeypatch.setattr(cache, "cache_get", AsyncMock(side_effect=values.get))
    monkeypatch.setattr(
        cache,
        "cache_set",
        AsyncMock(side_effect=lambda key, value, ttl: values.update({key: value})),
    )
    return values


@pytest.mark.parametrize(
    ("root_fields", "allowed"),
    [
        ({"user": "PROD_BOT", "bot_id": "BPROD"}, False),
        (
            {
                "user": "PROD_BOT",
                "bot_id": "BPROD",
                "text": "Breakout",
                "blocks": [
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "<https://robin.example.com/agents/thread-1|Open in Robin>",
                            }
                        ],
                    }
                ],
            },
            False,
        ),
        ({"user": "BOT", "bot_id": "BSTAGING"}, True),
        ({"user": "U1"}, True),
        ({"user": "ALERT_BOT", "bot_id": "BALERT", "text": "Build failed"}, True),
    ],
    ids=["other-robin", "other-robin-blocks", "this-robin", "human-sharing-link", "alert-bot"],
)
async def test_untagged_replies_stay_with_the_robin_that_started_the_thread(
    monkeypatch: pytest.MonkeyPatch,
    slack_api: SlackAPI,
    slack_root_cache: dict[str, bool | str],
    root_fields: JsonObject,
    allowed: bool,
) -> None:
    monkeypatch.setattr(ask_messages.common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(ask_messages.INTEGRATIONS, "channel_prompt", AsyncMock(return_value=None))
    slack_api.respond(
        {
            "ok": True,
            "messages": [
                {
                    "ts": "1.0",
                    "text": "Breakout <https://robin.example.com/agents/thread-1|Open in Robin>",
                    **root_fields,
                }
            ],
        }
    )

    for message_ts in ("2.0", "3.0"):
        assert (
            await AskChannelMessage.for_robin(
                channel_id="C1", thread_ts="1.0", message_ts=message_ts
            )
            is allowed
        )
    assert len(slack_api.calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "rate-limit", "missing-root", "cache-unavailable"])
async def test_unknown_origin_blocks_followups_until_rechecked(
    monkeypatch: pytest.MonkeyPatch,
    slack_api: SlackAPI,
    slack_root_cache: dict[str, bool | str],
    failure: str,
) -> None:
    from langgraph_api import cache

    async def stalled(*_args: object, **_kwargs: object) -> bool:
        await asyncio.Event().wait()
        return False

    monkeypatch.setattr(ask_messages.common, "SLACK_BOT_USER_ID", "BOT")
    monkeypatch.setattr(ask_messages.INTEGRATIONS, "channel_prompt", AsyncMock(return_value=None))
    fetch = AsyncMock(side_effect=stalled)
    if failure == "timeout":
        monkeypatch.setattr(SlackThread, "_fetch_other_robin_origin", fetch)
    elif failure == "rate-limit":
        slack_api.respond(
            {"ok": False, "error": "ratelimited"},
            status=429,
            headers={"Retry-After": "60"},
        )
    elif failure == "missing-root":
        slack_api.respond({"ok": True, "messages": []})
    else:
        monkeypatch.setattr(cache, "cache_get", AsyncMock(side_effect=RuntimeError("unavailable")))
        monkeypatch.setattr(cache, "cache_set", AsyncMock(side_effect=RuntimeError("unavailable")))
        slack_api.respond({"ok": True, "messages": [{"ts": "1.0", "user": "U1"}]})

    assert await AskChannelMessage.for_robin(
        channel_id="C1", thread_ts="1.0", message_ts="2.0"
    ) is (failure == "cache-unavailable")
    if failure == "cache-unavailable":
        return
    assert not await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="3.0")
    assert len(slack_api.calls) == (0 if failure == "timeout" else 1)
    if failure == "timeout":
        fetch.assert_awaited_once()
        fetch.side_effect = None
        fetch.return_value = False
    else:
        slack_api.respond({"ok": True, "messages": [{"ts": "1.0", "user": "U1"}]})
    slack_root_cache.clear()
    assert await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="4.0")


@pytest.mark.parametrize(
    ("message", "allowed"),
    [
        ({"files": [{"mimetype": "image/png"}]}, True),
        ({"files": [{"mimetype": "application/pdf"}]}, True),
        ({"attachments": [{"is_share": True, "text": "forwarded"}]}, True),
        (
            {
                "attachments": [
                    {"from_url": "https://example.com", "image_url": "https://example.com/a.png"}
                ]
            },
            False,
        ),
        ({"files": [], "attachments": []}, False),
    ],
)
async def test_ask_attachments_bypass_jev(
    monkeypatch: pytest.MonkeyPatch, message: JsonObject, allowed: bool
) -> None:
    async def channel_prompt(_channel_id: str) -> str | None:
        return ""

    async def fetch(_channel_id: str, _thread_ts: str) -> list[dict[str, object]]:
        return [
            {"ts": "1.0", "files": [{"mimetype": "image/png"}]},
            {"ts": "2.0", "text": "see this", **message},
        ]

    async def channel_context(_channel_id: str) -> SlackChannelContext:
        return SlackChannelContext(name="help")

    classified: list[bool] = []

    async def ask(*_args: object, **_kwargs: object) -> ClassifierResponse:
        classified.append(True)
        return _jev_answer("do_nothing", None)

    monkeypatch.setattr(ask_messages.INTEGRATIONS, "channel_prompt", channel_prompt)
    monkeypatch.setattr(ask_messages, "fetch_slack_thread_messages", fetch)
    monkeypatch.setattr(ask_messages.common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(ask_messages, "JEV", SimpleNamespace(ask=ask))

    assert (
        await AskChannelMessage.for_robin(channel_id="C1", thread_ts="1.0", message_ts="2.0")
        is allowed
    )
    assert bool(classified) is not allowed


@pytest.mark.parametrize(
    ("response", "follows_up"),
    [
        (
            AskResponse(
                probabilities={"follow_up_only": 0.25, "react_and_follow_up": 0.15},
                action="do_nothing",
                confidence=0.6,
            ),
            True,
        ),
        (
            AskResponse(
                probabilities={"follow_up_only": 0.3999, "react_and_follow_up": 0.0001},
                action="do_nothing",
            ),
            True,
        ),
        (
            AskResponse(
                probabilities={"follow_up_only": 0.3998, "react_and_follow_up": 0.0001},
                action="do_nothing",
            ),
            False,
        ),
        (AskResponse(probabilities={"follow_up_only": 0.39}, action="do_nothing"), False),
        (AskResponse(unavailable=True), True),
    ],
)
def test_robin_replies_short_of_certainty(response: AskResponse, follows_up: bool) -> None:
    assert response.follows_up is follows_up
