import logging
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe.run_config import RunConfig
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error

logger = logging.getLogger(__name__)


class SearchContextMessage(BaseModel):
    text: str
    user_id: str = ""
    ts: str = ""


class SearchContext(BaseModel):
    before: list[SearchContextMessage] = Field(default_factory=list)
    after: list[SearchContextMessage] = Field(default_factory=list)


class SearchMessage(BaseModel):
    channel_id: str
    channel_name: str = ""
    message_ts: str
    thread_ts: str = ""
    author_user_id: str = ""
    author_name: str = ""
    is_author_bot: bool = False
    content: str
    permalink: str
    context_messages: SearchContext = Field(default_factory=SearchContext)


class SearchResults(BaseModel):
    messages: list[SearchMessage]


class SearchMetadata(BaseModel):
    next_cursor: str = ""


class SearchResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    results: SearchResults
    response_metadata: SearchMetadata = Field(default_factory=SearchMetadata)


async def slack_search_messages(
    query: str,
    cursor: str | None = None,
    sort: Literal["score", "timestamp"] = "score",
) -> dict[str, object]:
    """Search public Slack messages on behalf of the current Slack sender."""
    cfg = RunConfig.from_runtime()
    if cfg.source != "slack" or not cfg.slack_action_token:
        return {
            "success": False,
            "error": "Slack search requires a current Slack mention or DM. Ask the user to "
            "mention Open SWE again; do not request a token or additional permissions.",
        }
    if not query.strip():
        return {"success": False, "error": "query is required"}
    try:
        async with SlackClient.bot() as client:
            response = await client.api_call(
                "assistant.search.context",
                json={
                    "query": query.strip(),
                    "action_token": cfg.slack_action_token,
                    "channel_types": ["public_channel"],
                    "content_types": ["messages"],
                    "include_context_messages": True,
                    "include_bots": True,
                    "limit": 20,
                    "sort": sort,
                    "sort_dir": "desc",
                    **({"cursor": cursor} if cursor else {}),
                },
            )
        parsed = SearchResponse.model_validate(response.data)
    except HTTPException:
        logger.warning("Slack search unavailable", extra={"slack_error": "missing_slack_bot_token"})
        return {"success": False, "error": "missing_slack_bot_token"}
    except ValidationError:
        logger.warning("Slack search returned an invalid response")
        return {"success": False, "error": "invalid_slack_response"}
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack search failed", extra={"slack_error": error})
        return {"success": False, "error": error}
    return {
        "success": True,
        "messages": [message.model_dump() for message in parsed.results.messages],
        "next_cursor": parsed.response_metadata.next_cursor,
    }
