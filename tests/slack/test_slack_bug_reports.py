from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe import event_claims
from openswe.slack import bug_reports
from openswe.slack.breakout_destination import BreakoutDestination
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackChannelContext, SlackEventEnvelope
from openswe.workspaces.store import WORKSPACES

_CHANNEL = SlackChannelContext(id="C0ASK", is_ext_shared=False, is_pending_ext_shared=False)


def _envelope(**event: object) -> SlackEventEnvelope:
    return SlackEventEnvelope.model_validate(
        {
            "type": "event_callback",
            "event_id": "Ev1",
            "team_id": "T1",
            "event": {
                "type": "message",
                "channel": "C0ASK",
                "user": "UREPORTER",
                "text": "The trace page crashes when I open a large run",
                "ts": "100.0",
                **event,
            },
        }
    )


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    lookup = AsyncMock(return_value="C0KITCHEN")
    monkeypatch.setattr(WORKSPACES, "bug_report_kitchen", lookup)
    return lookup


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(
        classify=AsyncMock(return_value="bug_report"),
        root=AsyncMock(return_value="200.0"),
        notify=AsyncMock(),
        unlinked=AsyncMock(return_value=None),
        mention=AsyncMock(),
    )
    monkeypatch.setattr(bug_reports, "select_jev_choice", calls.classify)
    monkeypatch.setattr(event_claims, "claim_within_limit", AsyncMock(return_value="claimed"))
    monkeypatch.setattr(
        bug_reports,
        "resolve_breakout_destination",
        AsyncMock(return_value=BreakoutDestination("C0KITCHEN", "frontend")),
    )
    monkeypatch.setattr(
        SlackChannel,
        "load",
        AsyncMock(
            side_effect=lambda channel_id, **_: SlackChannel.from_payload(
                {
                    "id": channel_id,
                    "name": channel_id.lower(),
                    "is_channel": True,
                    "is_private": False,
                    "is_ext_shared": False,
                    "is_pending_ext_shared": False,
                }
            )
        ),
    )
    monkeypatch.setattr(
        bug_reports, "source_thread_line", AsyncMock(return_value="<https://slack/p100|(source)>")
    )
    monkeypatch.setattr(bug_reports, "post_slack_top_level_message_with_ts", calls.root)
    monkeypatch.setattr(
        bug_reports,
        "bind_slack_thread_id",
        AsyncMock(side_effect=lambda c, ts, thread_id: thread_id),
    )
    monkeypatch.setattr(bug_reports, "dashboard_thread_url", lambda thread_id: None)
    monkeypatch.setattr(
        bug_reports, "get_slack_permalink", AsyncMock(return_value="https://slack/p200")
    )
    monkeypatch.setattr(bug_reports, "notify_breakout", calls.notify)
    monkeypatch.setattr(bug_reports.webhook, "account_link_needed", calls.unlinked)
    monkeypatch.setattr(bug_reports.common, "get_slack_repo_config", AsyncMock(return_value=None))
    monkeypatch.setattr(bug_reports.webhook, "process_slack_mention", calls.mention)
    return calls


@pytest.mark.usefixtures("routed")
async def test_a_bug_report_starts_a_kitchen_thread_acting_for_its_reporter(
    slack: SimpleNamespace,
) -> None:
    report = await bug_reports.SlackBugReport.posted(_envelope(), _CHANNEL, "UBOT")
    assert report is not None

    await report.triage()

    slack.root.assert_awaited_once()
    assert slack.root.await_args.args[:2] == (
        "C0KITCHEN",
        "Bug report from <#C0ASK> · <https://slack/p100|(source)> · <@UREPORTER>",
    )
    slack.notify.assert_awaited_once_with(
        "C0ASK",
        "100.0",
        "Open SWE is looking into this bug report in <https://slack/p200|a thread> in "
        "<#C0KITCHEN>.",
    )
    [request, _repo] = slack.mention.await_args.args
    assert (request.channel_id, request.thread_ts, request.user_id) == (
        "C0KITCHEN",
        "200.0",
        "UREPORTER",
    )
    assert (request.context_channel_id, request.context_thread_ts) == ("C0ASK", "100.0")
    assert request.trigger_system == bug_reports.BUG_REPORT_TRIAGE
    assert request.kitchen_channel
    assert slack.mention.await_args.kwargs["inherited_workspace"] == "frontend"


@pytest.mark.usefixtures("routed")
async def test_a_message_that_reports_no_bug_starts_nothing(slack: SimpleNamespace) -> None:
    slack.classify.return_value = "not_bug_report"
    report = await bug_reports.SlackBugReport.posted(_envelope(), _CHANNEL, "UBOT")
    assert report is not None

    await report.triage()

    slack.root.assert_not_awaited()
    slack.notify.assert_not_awaited()
    slack.mention.assert_not_awaited()


@pytest.mark.usefixtures("routed")
async def test_a_reporter_open_swe_cannot_act_for_leaves_the_thread_to_the_kitchen(
    slack: SimpleNamespace,
) -> None:
    slack.unlinked.return_value = object()
    report = await bug_reports.SlackBugReport.posted(_envelope(), _CHANNEL, "UBOT")
    assert report is not None

    await report.triage()

    slack.root.assert_awaited_once()
    slack.mention.assert_not_awaited()
    assert [call.args[:2] for call in slack.notify.await_args_list] == [
        ("C0ASK", "100.0"),
        ("C0KITCHEN", "200.0"),
    ]


@pytest.mark.parametrize(
    "event",
    [
        {"thread_ts": "90.0"},
        {"subtype": "bot_message", "bot_id": "B1"},
        {"subtype": "message_changed"},
        {"text": "<@UBOT> is the trace page broken?"},
        {"user": "UBOT"},
    ],
)
async def test_only_new_top_level_posts_by_people_are_triaged(
    routed: AsyncMock, event: dict[str, object]
) -> None:
    assert await bug_reports.SlackBugReport.posted(_envelope(**event), _CHANNEL, "UBOT") is None


async def test_channels_that_route_no_bug_reports_are_left_alone(routed: AsyncMock) -> None:
    routed.return_value = None

    assert await bug_reports.SlackBugReport.posted(_envelope(), _CHANNEL, "UBOT") is None
