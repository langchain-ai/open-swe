import json

from langchain_core.runnables import RunnableConfig
from langchain_typesafe import (
    ChoiceAnswer,
    ClassifierRequest,
    ClassifierResponse,
    TypeSafeClassifier,
)

from openswe.slack.parsed_message import ParsedSlackMessage


async def deterministic_command_classifier(
    self: TypeSafeClassifier,
    input: ClassifierRequest,
    config: RunnableConfig | None = None,
    **kwargs: object,
) -> ClassifierResponse:
    state = input["state"]
    if not isinstance(state, str):
        raise AssertionError("Expected serialized Slack command state")
    message = json.loads(state)
    parsed = ParsedSlackMessage.parse(
        message["message"], message["bot_user_id"], message["bot_username"]
    )
    active = {
        parsed.action,
        "/model:perf" if parsed.performance_model else None,
        "/workspace" if parsed.workspace else None,
    }
    return ClassifierResponse(
        model="fake-jev",
        answers={
            command: ChoiceAnswer(
                type="choice",
                choice=f"deterministic:{command}" if command in active else "no_command",
                confidence=1.0,
                probabilities={
                    f"deterministic:{command}" if command in active else "no_command": 1.0
                },
            )
            for command in input["questions"]
        },
    )
