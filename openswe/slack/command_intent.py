import asyncio
import inspect
import json
import logging
from dataclasses import replace
from functools import cache

from langchain_typesafe import Choice

from openswe.prompts import prompt
from openswe.slack import parsed_message
from openswe.slack.parsed_message import BreakoutCommand, ParsedSlackMessage, SlackAction
from openswe.utils.jev import select_jev_choices

logger = logging.getLogger(__name__)

_COMMANDS = (*SlackAction, "/model:perf", "/workspace")


@cache
def _rules() -> str:
    return "\n\n".join(
        (
            inspect.getsource(parsed_message),
            inspect.getsource(parsed_message.slugify),
        )
    )


async def interpret_commands(
    text: str, bot_user_id: str, bot_username: str = ""
) -> ParsedSlackMessage:
    parsed = ParsedSlackMessage.parse(text, bot_user_id, bot_username)
    plain = ParsedSlackMessage(text=text, argument=text.strip())
    try:
        async with asyncio.timeout(1.5):
            choices = await select_jev_choices(
                json.dumps(
                    {
                        "python_rules": _rules(),
                        "message": text,
                        "bot_user_id": bot_user_id,
                        "bot_username": bot_username,
                        "deterministic_result": {
                            "action": parsed.action,
                            "argument": parsed.argument,
                            "prior_text": parsed.prior_text,
                            "performance_model": parsed.performance_model,
                            "workspace": parsed.workspace,
                            "breakout": vars(BreakoutCommand.from_message(parsed)),
                        },
                    }
                ),
                questions={
                    command: Choice(
                        instructions=prompt("slack/command-intent", command=command),
                        criteria={
                            f"deterministic:{command}": prompt(
                                "slack/command-deterministic", command=command
                            ),
                            f"agent:{command}": prompt("slack/command-agent", command=command),
                            "no_command": prompt("slack/command-none", command=command),
                        },
                    )
                    for command in _COMMANDS
                },
            )
    except TimeoutError:
        logger.warning("Slack command classification timed out")
        return plain
    available = {
        **{action: parsed.action == action for action in SlackAction},
        "/model:perf": parsed.performance_model,
        "/workspace": parsed.workspace is not None,
    }
    handoff = tuple(
        command
        for command in _COMMANDS
        if choices[command] is None
        or choices[command] == f"agent:{command}"
        or (choices[command] == f"deterministic:{command}" and not available[command])
    )
    if handoff:
        return plain
    action = (
        parsed.action
        if choices.get(parsed.action or "") == f"deterministic:{parsed.action}"
        else None
    )
    performance = choices["/model:perf"] == "deterministic:/model:perf"
    workspace = choices["/workspace"] == "deterministic:/workspace"
    if action and any(
        span is not None and span[1] <= parsed.argument_start and not accepted
        for span, accepted in (
            (parsed.performance_span, performance),
            (parsed.workspace_span, workspace),
        )
    ):
        return plain
    return replace(
        parsed if action else plain,
        action=action,
        performance_model=parsed.performance_model and performance,
        performance_span=parsed.performance_span if performance else None,
        workspace=parsed.workspace if workspace else None,
        workspace_span=parsed.workspace_span if workspace else None,
    )
