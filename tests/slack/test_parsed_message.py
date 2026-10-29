from unittest.mock import AsyncMock

import pytest

from openswe.slack import command_intent
from openswe.slack.parsed_message import ParsedSlackMessage, SlackAction
from openswe.slack.request import SlackRequest


@pytest.mark.parametrize(
    ("text", "action", "argument", "prior_text"),
    [
        ("<@U0BOT> /btw why?", SlackAction.BY_THE_WAY, "why?", ""),
        ("details <@U0BOT> /BTW why?\nmore", SlackAction.BY_THE_WAY, "why?\nmore", "details"),
        ("/breakout fix it", SlackAction.BREAKOUT, "fix it", ""),
        ("@openswe /breakout:web explain", SlackAction.BREAKOUT_WEB, "explain", ""),
        ("<@U0BOT> /model:perf /breakout fix it", SlackAction.BREAKOUT, "fix it", ""),
        ("details <@U0BOT> please /btw why?", None, "details <@U0BOT> please /btw why?", ""),
        ("details /btw why? <@U0BOT>", None, "details /btw why? <@U0BOT>", ""),
        ("<@OTHER> /btw why?", None, "<@OTHER> /btw why?", ""),
        ("`<@U0BOT> /btw why?`", None, "`<@U0BOT> /btw why?`", ""),
        ("> <@U0BOT> /btw why?", None, "> <@U0BOT> /btw why?", ""),
        ("<@U0BOT> /btwhatever why?", None, "<@U0BOT> /btwhatever why?", ""),
    ],
)
def test_commands_only_lead_the_message_or_follow_this_bots_mention(
    text: str, action: SlackAction | None, argument: str, prior_text: str
) -> None:
    parsed = ParsedSlackMessage.parse(text, "U0BOT", "openswe")

    assert (parsed.action, parsed.argument, parsed.prior_text) == (action, argument, prior_text)


@pytest.mark.parametrize(
    ("text", "performance_model", "workspace", "without"),
    [
        ("<@U0BOT> /model:perf /workspace:Infra fix it", True, "infra", "<@U0BOT> fix it"),
        ("<@U0BOT> /model:performance fix it", True, None, "<@U0BOT> fix it"),
        ("<@U0BOT> /MODEL:PERFORM fix it", True, None, "<@U0BOT> fix it"),
        ("research <https://x.y/p> /model:perf\n", True, None, "research <https://x.y/p>"),
        ("<@U0BOT> fix /workspace:infra it /MODEL:PERF", True, "infra", "<@U0BOT> fix it"),
        (
            "<@U0BOT> why did `/model:perf` fire?",
            False,
            None,
            "<@U0BOT> why did `/model:perf` fire?",
        ),
        (
            '<@U0BOT> why did "/model:perf" fire?',
            False,
            None,
            '<@U0BOT> why did "/model:perf" fire?',
        ),
        ("<@U0BOT> fix it workspace:infra", False, "infra", "<@U0BOT> fix it"),
        ("env:infra <@U0BOT> fix it", False, "infra", "<@U0BOT> fix it"),
        ("<@U0BOT> fix it Workspace:Infra", False, "infra", "<@U0BOT> fix it"),
        ("@OpenSWE /Model:Perf /WORKSPACE:infra fix it", True, "infra", "@OpenSWE fix it"),
        ("<@U0BOT> fix `workspace:infra`", False, None, "<@U0BOT> fix `workspace:infra`"),
    ],
)
def test_options_parse_and_strip_the_same_way(
    text: str, performance_model: bool, workspace: str | None, without: str
) -> None:
    parsed = ParsedSlackMessage.parse(text, "U0BOT", "openswe")

    assert (parsed.performance_model, parsed.workspace) == (performance_model, workspace)
    assert parsed.without(performance_model=True, workspace=True) == without


@pytest.mark.parametrize(
    ("answers", "expected_action", "expected_performance", "expected_workspace", "handoff"),
    [
        (
            {
                "/breakout": "deterministic:/breakout",
                "/model:perf": "deterministic:/model:perf",
                "/workspace": "deterministic:/workspace",
            },
            SlackAction.BREAKOUT,
            True,
            "infra",
            False,
        ),
        ({}, None, False, None, False),
        (
            {"/breakout": "agent:/breakout", "/model:perf": "deterministic:/model:perf"},
            None,
            False,
            None,
            True,
        ),
        ({"/breakout": None}, None, False, None, True),
        ({"/btw": "deterministic:/btw"}, None, False, None, True),
    ],
)
async def test_intent_gates_all_command_effects_and_survives_request_serialization(
    monkeypatch: pytest.MonkeyPatch,
    answers: dict[str, str | None],
    expected_action: SlackAction | None,
    expected_performance: bool,
    expected_workspace: str | None,
    handoff: bool,
) -> None:
    choices: dict[str, str | None] = dict.fromkeys(command_intent._COMMANDS, "no_command")
    choices.update(answers)
    monkeypatch.setattr(command_intent, "select_jev_choices", AsyncMock(return_value=choices))
    text = "<@U0BOT> /model:perf /workspace:infra /breakout fix it"
    parsed = await command_intent.interpret_commands(text, "U0BOT")
    request = SlackRequest(channel_id="C1", thread_ts="1", text=text, command_message=parsed)
    restored = SlackRequest.model_validate_json(request.model_dump_json()).command_message
    assert restored is not None
    assert (restored.action, restored.performance_model, restored.workspace) == (
        expected_action,
        expected_performance,
        expected_workspace,
    )
    assert bool(restored.handoff_commands) == handoff
    if handoff or not expected_action:
        assert restored.argument == text
        assert restored.without(performance_model=True, workspace=True) == text
    else:
        assert restored.argument == "fix it"
        assert restored.options == "/model:perf /workspace:infra"


async def test_classifier_timeout_keeps_original_message_for_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command_intent, "select_jev_choices", AsyncMock(side_effect=TimeoutError))
    text = "<@U0BOT> /btw why?"
    parsed = await command_intent.interpret_commands(text, "U0BOT")
    assert parsed.action is None
    assert parsed.argument == text
    assert parsed.handoff_commands
