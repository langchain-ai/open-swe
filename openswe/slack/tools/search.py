import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe import event_claims
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.encryption import decrypt_token, encrypt_token
from openswe.run_config import RunConfig
from openswe.slack.channels import SlackChannel
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from openswe.tools import access as tool_access
from openswe.tools.admin_gate import configurable

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 20
SEARCHES_PER_GRANT = 6
_GRANT_WINDOW = timedelta(hours=1)
PURGE_JOB: Final = "slack_search_grant_purge"
PURGE_SCHEDULE: Final = "17 * * * *"

_NO_ACTION_TOKEN = (
    "Slack attached no search grant to the message that started this run. Slack grants "
    "search only for a message that @mentions Robin or is sent to it in a DM. Ask the "
    "person to @mention Robin in a new message to search."
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


class SlackSearchGrant(Base):
    """The search grant attached to a message, kept across its duplicate events."""

    __tablename__ = "slack_search_grant"

    channel_id: Mapped[str] = mapped_column(ForeignKey("slack_channel.id"))
    message_ts: Mapped[str]
    # Encrypted under TOKEN_ENCRYPTION_KEY.
    action_token: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def keep(cls, channel_id: str, message_ts: str, action_token: str) -> None:
        """Keep the grant a message's event carried; the first one kept stands."""
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            inserted = await session.scalar(
                insert(cls)
                .values(
                    id=uuid7(),
                    channel_id=channel_id,
                    message_ts=message_ts,
                    action_token=encrypt_token(action_token),
                )
                .on_conflict_do_nothing(index_elements=[cls.channel_id, cls.message_ts])
                .returning(cls.id)
            )
        logger.info(
            "Slack search grant stored",
            extra={
                "slack_channel_id": channel_id,
                "slack_message_ts": message_ts,
                "grant_store_outcome": "inserted" if inserted is not None else "conflict",
            },
        )

    @classmethod
    async def find(cls, channel_id: str, message_ts: str) -> str:
        """The grant kept for a message, or ``""`` once it lapsed or when none was."""
        async with postgres.session() as session:
            sealed = await session.scalar(
                select(cls.action_token).where(
                    cls.channel_id == channel_id,
                    cls.message_ts == message_ts,
                    cls.created_at > func.now() - _GRANT_WINDOW,
                )
            )
        action_token = decrypt_token(sealed) if sealed else ""
        logger.info(
            "Slack search grant lookup",
            extra={
                "slack_channel_id": channel_id,
                "slack_message_ts": message_ts,
                "grant_found": bool(action_token),
            },
        )
        return action_token

    @classmethod
    async def purge(cls) -> int:
        """Forget the grants that lapsed."""
        async with postgres.transaction() as conn:
            result = await conn.execute(
                delete(cls).where(cls.created_at <= func.now() - _GRANT_WINDOW)
            )
        return result.rowcount


class SlackSearch:
    """Public-channel message search under the grant Slack attached to this run's message."""

    def __init__(self, action_token: str, channel_id: str) -> None:
        self.action_token = action_token
        self.channel_id = channel_id

    @classmethod
    async def for_run(cls, cfg: RunConfig) -> SlackSearch:
        slack = cfg.slack_thread
        fields = {
            "agent_thread_id": cfg.thread_id,
            "slack_channel_id": slack.channel_id if slack is not None else None,
            "slack_message_ts": cfg.slack_search_message_ts,
            "slack_thread_present": slack is not None,
            "slack_operations_allowed": bool(
                slack is not None
                and slack.channel_context
                and slack.channel_context.allows_operations
            ),
            "action_token_present": bool(cfg.slack_action_token),
        }
        logger.info("Slack search grant requested", extra=fields)
        if slack is None:
            raise SlackSearchRefused(_NO_ACTION_TOKEN)
        if slack.channel_context is None or not slack.channel_context.allows_operations:
            raise SlackSearchRefused(_SHARED_CHANNEL)
        action_token = cfg.slack_action_token or ""
        if not action_token and slack.channel_id and cfg.slack_search_message_ts:
            action_token = await SlackSearchGrant.find(
                slack.channel_id, cfg.slack_search_message_ts
            )
        logger.info(
            "Slack search grant resolved",
            extra={
                **fields,
                "grant_lookup_attempted": bool(
                    not cfg.slack_action_token and slack.channel_id and cfg.slack_search_message_ts
                ),
                "grant_source": (
                    "config" if cfg.slack_action_token else "store" if action_token else "none"
                ),
            },
        )
        if not action_token:
            raise SlackSearchRefused(_NO_ACTION_TOKEN)
        return cls(action_token, slack.channel_id)

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
        return await (await SlackSearch.for_run(configurable())).messages(
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
