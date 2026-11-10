import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openswe import event_claims
from openswe.run_config import RunConfig
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from openswe.tools import access as tool_access
from openswe.tools.admin_gate import configurable

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 20
SEARCHES_PER_GRANT = 6
_GRANT_WINDOW = timedelta(hours=1)

_NO_ACTION_TOKEN = (
    "This run has no Slack search grant for its message. Search is unavailable for this turn."
)
_SHARED_CHANNEL = (
    "Search is unavailable in a channel shared with another organization, because its "
    "results would reach people outside the workspace."
)
_BUDGET_SPENT = (
    f"This message's {SEARCHES_PER_GRANT} Slack searches are spent. Answer with what you "
    "already found."
)
_ERRORS = {
    "invalid_action_token": "Slack no longer accepts this message's search grant. Ask the "
    "person to @mention Open SWE again to search.",
    "missing_scope": "The Slack app is missing the search:read.public scope. A workspace "
    "admin needs to add it and reinstall the app.",
    "feature_not_enabled": "Slack search is not enabled for this workspace or app.",
    "not_allowed_token_type": "Slack search is not enabled for this app.",
}


class SlackSearchMessage(BaseModel):
    """One message ``assistant.search.context`` matched."""

    model_config = ConfigDict(extra="ignore")

    channel_id: str = ""
    channel_name: str = ""
    message_ts: str = ""
    author_user_id: str = ""
    author_name: str = ""
    is_author_bot: bool = False
    content: str = ""
    permalink: str = ""


class _SearchResults(BaseModel):
    messages: list[SlackSearchMessage] = Field(default_factory=list)


class _ResponseMetadata(BaseModel):
    next_cursor: str = ""


class _SearchResponse(BaseModel):
    results: _SearchResults = Field(default_factory=_SearchResults)
    response_metadata: _ResponseMetadata = Field(default_factory=_ResponseMetadata)


class SlackSearchRefused(Exception):
    """This run may not search Slack."""


class SlackSearch:
    """Public-channel message search under the grant Slack attached to this run's message."""

    def __init__(self, action_token: str, channel_id: str) -> None:
        self.action_token = action_token
        self.channel_id = channel_id

    @classmethod
    def for_run(cls, cfg: RunConfig) -> SlackSearch:
        logger.info(
            "Slack search requested", extra={"has_action_token": bool(cfg.slack_action_token)}
        )
        slack = cfg.slack_thread
        if slack is None:
            raise SlackSearchRefused("Slack search requires a run associated with a Slack thread.")
        if slack.channel_context is None or not slack.channel_context.allows_operations:
            raise SlackSearchRefused(_SHARED_CHANNEL)
        if not cfg.slack_action_token:
            raise SlackSearchRefused(_NO_ACTION_TOKEN)
        return cls(cfg.slack_action_token, slack.channel_id)

    async def spend(self) -> None:
        """Take one of the grant's searches, shared by every run and subagent holding it."""
        grant = hashlib.sha256(self.action_token.encode()).hexdigest()[:32]
        outcome = await event_claims.claim_within_limit(
            "slack-search",
            f"{grant}:{uuid.uuid4()}",
            ttl=_GRANT_WINDOW,
            group=f"{grant}:",
            limit=SEARCHES_PER_GRANT,
            within=_GRANT_WINDOW,
        )
        if outcome != "claimed":
            raise SlackSearchRefused(_BUDGET_SPENT)

    async def messages(
        self, query: str, *, limit: int, cursor: str, sort: str
    ) -> dict[str, object]:
        await self.spend()
        payload: dict[str, object] = {
            "query": query,
            "action_token": self.action_token,
            "channel_types": ["public_channel"],
            "content_types": ["messages"],
            "include_bots": False,
            "limit": limit,
            "sort": sort,
        }
        if self.channel_id:
            payload["context_channel_id"] = self.channel_id
        if cursor:
            payload["cursor"] = cursor
        async with SlackClient.bot() as client:
            response = await client.api_call("assistant.search.context", json=payload)
        parsed = _SearchResponse.model_validate(response.data)
        return {
            "success": True,
            "messages": [message.model_dump() for message in parsed.results.messages],
            "next_cursor": parsed.response_metadata.next_cursor,
        }


@tool_access.access(tool_access.Policy(trusted="thread_history"))
async def slack_search(
    query: str,
    limit: int = MAX_SEARCH_RESULTS,
    cursor: str = "",
    sort: Literal["score", "timestamp"] = "score",
) -> dict[str, object]:
    """Implement the `slack_search` tool."""
    if not query or not query.strip():
        return {"success": False, "error": "query is required"}
    try:
        return await SlackSearch.for_run(configurable()).messages(
            query.strip(),
            limit=min(max(limit, 1), MAX_SEARCH_RESULTS),
            cursor=cursor.strip(),
            sort=sort,
        )
    except SlackSearchRefused as exc:
        return {"success": False, "error": str(exc)}
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack search failed", extra={"slack_error": error})
        return {"success": False, "error": _ERRORS.get(error, f"Slack search failed: {error}")}
    except ValidationError:
        logger.warning("Slack search returned an unexpected response", exc_info=True)
        return {"success": False, "error": "Slack search returned an unexpected response."}
