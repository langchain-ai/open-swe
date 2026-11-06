import pytest

from openswe.slack import summon
from openswe.slack.payloads import SlackChannelContext, SlackEvent
from openswe.slack.request import SlackRequest


def _event(user_id: str, reaction: str = summon.SUMMON_REACTION) -> SlackEvent:
    return SlackEvent.model_validate(
        {
            "type": "reaction_added",
            "reaction": reaction,
            "user": user_id,
            "event_ts": "5.000",
            "item": {"type": "message", "channel": "C123", "ts": "3.000"},
        }
    )


@pytest.fixture(params=["robin", "openswe"])
def reaction(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch, reaction: str) -> list[SlackRequest]:
    requests: list[SlackRequest] = []
    monkeypatch.setenv("OPENSWE_ENV", "production")

    async def restore_status(channel_id: str, thread_ts: str) -> bool:
        return True

    monkeypatch.setattr(summon, "restore_slack_thinking_status", restore_status)

    async def fetch_message(channel_id: str, thread_ts: str, message_ts: str) -> dict[str, object]:
        return {
            "ts": message_ts,
            "thread_ts": "1.000",
            "user": "UAUTHOR",
            "reactions": [
                {"name": reaction, "users": ["UREACTOR", "USECOND"]},
                {"name": "openswe" if reaction == "robin" else "robin", "users": ["UREACTOR"]},
            ],
        }

    async def resolve_thread_id(client: object, channel_id: str, thread_ts: str) -> str:
        return f"thread-{thread_ts}"

    async def is_code_channel(channel_id: str) -> bool:
        return False

    async def claim(event_id: str, channel_id: str = "", event_ts: str = "") -> bool:
        return True

    async def repo_config(*args: object, **kwargs: object) -> None:
        return None

    async def process_mention(request: SlackRequest, repo: None) -> None:
        requests.append(request)

    monkeypatch.setattr(summon, "fetch_slack_thread_message_by_ts", fetch_message)
    monkeypatch.setattr(summon, "resolve_slack_thread_id", resolve_thread_id)
    monkeypatch.setattr(summon, "is_code_channel", is_code_channel)
    monkeypatch.setattr(summon, "claim_slack_event", claim)
    monkeypatch.setattr(summon, "langgraph_client", lambda: object())
    monkeypatch.setattr(summon.common, "get_slack_repo_config", repo_config)
    monkeypatch.setattr(summon.webhook, "process_slack_mention", process_mention)
    return requests


async def test_summon_reaction_tags_open_swe_for_the_reactor(
    dispatched: list[SlackRequest],
    reaction: str,
) -> None:
    await summon.process_slack_summon_reaction(
        _event("UREACTOR", reaction),
        "Ev1",
        channel_context=SlackChannelContext(id="C123"),
        bot_user_id="UBOT",
        team_id="T1",
    )

    [request] = dispatched
    assert request.user_id == "UREACTOR"
    assert request.thread_ts == "1.000"
    assert request.thread_id == "thread-1.000"
    assert request.original_message_ts == "3.000"
    assert request.source_message_ts == "3.000"
    assert request.trigger_system is not None
    assert request.explicit_mention


@pytest.mark.parametrize("environment", ["preview", "staging"])
async def test_summon_reactions_are_disabled_outside_production(
    monkeypatch: pytest.MonkeyPatch,
    dispatched: list[SlackRequest],
    environment: str,
    reaction: str,
) -> None:
    monkeypatch.setenv("OPENSWE_ENV", environment)
    await summon.process_slack_summon_reaction(
        _event("UREACTOR", reaction),
        "Ev1",
        channel_context=SlackChannelContext(id="C123"),
        bot_user_id="UBOT",
        team_id="T1",
    )

    assert dispatched == []


@pytest.mark.parametrize("second_alias", [False, True])
async def test_later_summon_reactions_do_not_restart_the_run(
    dispatched: list[SlackRequest],
    reaction: str,
    second_alias: bool,
) -> None:
    await summon.process_slack_summon_reaction(
        _event(
            "UREACTOR" if second_alias else "USECOND",
            ("openswe" if reaction == "robin" else "robin") if second_alias else reaction,
        ),
        "Ev1",
        channel_context=SlackChannelContext(id="C123"),
        bot_user_id="UBOT",
        team_id="T1",
    )

    assert dispatched == []
