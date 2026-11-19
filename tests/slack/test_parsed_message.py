import pytest

from openswe.slack.parsed_message import ParsedSlackMessage, SlackAction


@pytest.mark.parametrize(
    ("text", "action", "argument", "prior_text"),
    [
        ("<@U0BOT> /btw why?", SlackAction.BY_THE_WAY, "why?", ""),
        ("details <@U0BOT> /BTW why?\nmore", SlackAction.BY_THE_WAY, "why?\nmore", "details"),
        ("/breakout fix it", SlackAction.BREAKOUT, "fix it", ""),
        ("@openswe /breakout:web explain", SlackAction.BREAKOUT_WEB, "explain", ""),
        ("<@U0BOT> /model:perf /breakout fix it", SlackAction.BREAKOUT, "fix it", ""),
        ("<@U0BOT> /workspace:infra /btw why?", None, "<@U0BOT> /workspace:infra /btw why?", ""),
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
    ("text", "performance_model", "without"),
    [
        ("<@U0BOT> /model:perf /workspace:Infra fix it", True, "<@U0BOT> /workspace:Infra fix it"),
        ("<@U0BOT> /model:performance fix it", True, "<@U0BOT> fix it"),
        ("<@U0BOT> /MODEL:PERFORM fix it", True, "<@U0BOT> fix it"),
        ("research <https://x.y/p> /model:perf\n", True, "research <https://x.y/p>"),
        (
            "<@U0BOT> fix /workspace:infra it /MODEL:PERF",
            True,
            "<@U0BOT> fix /workspace:infra it",
        ),
        (
            "<@U0BOT> why did `/model:perf` fire?",
            False,
            "<@U0BOT> why did `/model:perf` fire?",
        ),
        (
            '<@U0BOT> why did "/model:perf" fire?',
            False,
            '<@U0BOT> why did "/model:perf" fire?',
        ),
        ("<@U0BOT> fix it workspace:infra", False, "<@U0BOT> fix it workspace:infra"),
        ("env:infra <@U0BOT> fix it", False, "env:infra <@U0BOT> fix it"),
        ("<@U0BOT> fix it Workspace:Infra", False, "<@U0BOT> fix it Workspace:Infra"),
        (
            "@OpenSWE /Model:Perf /WORKSPACE:infra fix it",
            True,
            "@OpenSWE /WORKSPACE:infra fix it",
        ),
        ("<@U0BOT> fix `workspace:infra`", False, "<@U0BOT> fix `workspace:infra`"),
    ],
)
def test_options_parse_and_strip_the_same_way(
    text: str, performance_model: bool, without: str
) -> None:
    parsed = ParsedSlackMessage.parse(text, "U0BOT", "openswe")

    assert parsed.performance_model is performance_model
    assert parsed.without(performance_model=True) == without
