"""Persisted Slack channel identities and metadata."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.slack.oauth import SLACK_TEAM_ID
from agent.store import TypedStore, now_ms

_UNKNOWN_TEAM = "_unknown"


class SlackChannel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    team_id: str = ""
    name: str = ""
    name_normalized: str = ""
    is_private: bool | None = None
    is_im: bool | None = None
    is_mpim: bool | None = None
    is_archived: bool | None = None
    first_seen_at_ms: int
    updated_at_ms: int

    @property
    def label(self) -> str:
        return self.name or self.name_normalized or self.id


def _store(team_id: str) -> TypedStore[SlackChannel]:
    return TypedStore(("slack_channels", team_id or _UNKNOWN_TEAM), SlackChannel)


async def upsert_slack_channel(
    channel_id: str,
    channel_context: dict[str, Any] | None,
    *,
    team_id: str = "",
) -> SlackChannel | None:
    """Create or refresh one channel record."""
    channel_id = channel_id.strip()
    if not channel_id:
        return None
    resolved_team_id = team_id.strip() or SLACK_TEAM_ID.strip()
    store = _store(resolved_team_id)
    existing = await store.get(channel_id)
    context = channel_context if isinstance(channel_context, dict) else {}
    timestamp = now_ms()

    def text(key: str) -> str:
        value = context.get(key)
        return value.strip() if isinstance(value, str) else ""

    def boolean(key: str) -> bool | None:
        value = context.get(key)
        if isinstance(value, bool):
            return value
        return getattr(existing, key) if existing else None

    record = SlackChannel(
        id=channel_id,
        team_id=resolved_team_id,
        name=text("name") or (existing.name if existing else ""),
        name_normalized=text("name_normalized") or (existing.name_normalized if existing else ""),
        is_private=boolean("is_private"),
        is_im=boolean("is_im"),
        is_mpim=boolean("is_mpim"),
        is_archived=boolean("is_archived"),
        first_seen_at_ms=existing.first_seen_at_ms if existing else timestamp,
        updated_at_ms=timestamp,
    )
    return await store.put(channel_id, record)


async def get_slack_channel(channel_id: str, *, team_id: str = "") -> SlackChannel | None:
    """Read one channel, including legacy records without a workspace id."""
    channel_id = channel_id.strip()
    if not channel_id:
        return None
    resolved_team_id = team_id.strip() or SLACK_TEAM_ID.strip()
    record = await _store(resolved_team_id).get(channel_id)
    if record is not None or not resolved_team_id:
        return record
    return await _store("").get(channel_id)
