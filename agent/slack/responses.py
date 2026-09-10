"""What the Slack webhook routes answer Slack with."""

from typing import Literal, NotRequired, TypedDict

from agent.utils.json_types import JsonObject


class WebhookResponse(TypedDict):
    status: Literal["accepted", "ignored", "error"]
    message: NotRequired[str]
    reason: NotRequired[str]
    error_id: NotRequired[str]


class SlashCommandResponse(TypedDict):
    response_type: Literal["ephemeral", "in_channel"]
    text: str


class ChallengeResponse(TypedDict):
    challenge: str


class BlockSuggestionResponse(TypedDict):
    options: list[JsonObject]


class FeedbackResponse(TypedDict, total=False):
    response_action: Literal["errors"]
    errors: dict[str, str]


class HealthResponse(TypedDict):
    status: Literal["ok"]
    message: str


def accepted(message: str) -> WebhookResponse:
    return {"status": "accepted", "message": message}


def ignored(reason: str) -> WebhookResponse:
    return {"status": "ignored", "reason": reason}


def failed(error_id: str) -> WebhookResponse:
    return {"status": "error", "error_id": error_id}


def ephemeral(text: str) -> SlashCommandResponse:
    return {"response_type": "ephemeral", "text": text}
