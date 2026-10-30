"""Durable Slack ↔ actor account links (LangGraph Store + in-memory)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Item

from managed_deepagents._identity_auth import (
    HANDOFF_SECRET_ENV,
    HANDOFF_SECRET_HEADER,
    IDENTITY_LINKS_SERVICE_USER,
    IDENTITY_LINKS_STORE_PREFIX,
    INGRESS_SECRET_ENV,
    INGRESS_SECRET_HEADER,
    USER_HEADER,
)
from managed_deepagents._langsmith_env import resolve_server_api_key
from managed_deepagents._loopback import (
    is_loopback_url,
    resolve_loopback_api_key,
    resolve_loopback_api_url,
)

IDENTITY_LINKS_NAMESPACE: list[str] = [IDENTITY_LINKS_STORE_PREFIX]


@dataclass(frozen=True)
class IdentityLinkRecord:
    user_id: str
    slack_user_id: str
    slack_key: str
    linked_at: str
    source: str = "slack_oauth"
    slack_team_id: str | None = None
    email: str | None = None


class IdentityLinkStore(Protocol):
    async def get_by_slack_key(self, slack_key: str) -> IdentityLinkRecord | None: ...

    async def get_by_user_id(self, user_id: str) -> IdentityLinkRecord | None: ...

    async def upsert(self, record: IdentityLinkRecord) -> None: ...

    async def remove_by_user_id(self, user_id: str) -> None: ...


class InMemoryIdentityLinkStore:
    """In-memory store for tests."""

    def __init__(self) -> None:
        self._by_slack: dict[str, IdentityLinkRecord] = {}
        self._by_actor: dict[str, IdentityLinkRecord] = {}

    async def get_by_slack_key(self, slack_key: str) -> IdentityLinkRecord | None:
        return self._by_slack.get(slack_key)

    async def get_by_user_id(self, user_id: str) -> IdentityLinkRecord | None:
        return self._by_actor.get(user_id)

    async def upsert(self, record: IdentityLinkRecord) -> None:
        previous = self._by_actor.get(record.user_id)
        if previous and previous.slack_key != record.slack_key:
            self._by_slack.pop(previous.slack_key, None)
        existing_slack = self._by_slack.get(record.slack_key)
        if existing_slack and existing_slack.user_id != record.user_id:
            self._by_actor.pop(existing_slack.user_id, None)
        self._by_slack[record.slack_key] = record
        self._by_actor[record.user_id] = record

    async def remove_by_user_id(self, user_id: str) -> None:
        existing = self._by_actor.pop(user_id, None)
        if existing is not None:
            self._by_slack.pop(existing.slack_key, None)


_default_store: IdentityLinkStore | None = None


def set_identity_link_store(store: IdentityLinkStore | None) -> None:
    """Override the process-default link store (tests)."""
    global _default_store  # noqa: PLW0603
    _default_store = store


def get_identity_link_store() -> IdentityLinkStore:
    """Resolve the process-default link store."""
    global _default_store  # noqa: PLW0603
    if _default_store is not None:
        return _default_store
    _default_store = create_langgraph_identity_link_store() or InMemoryIdentityLinkStore()
    return _default_store


def create_langgraph_identity_link_store() -> IdentityLinkStore | None:
    """Build a Store-backed client when a trusted loopback credential is available."""
    api_url = resolve_loopback_api_url()
    is_local = is_loopback_url(api_url)
    handoff = os.environ.get(HANDOFF_SECRET_ENV, "").strip() if is_local else ""
    server_api_key = resolve_server_api_key()
    ingress = os.environ.get(INGRESS_SECRET_ENV, "").strip() if is_local else ""
    if not handoff and not server_api_key and not ingress:
        return None
    api_key = resolve_loopback_api_key()
    headers = {USER_HEADER: IDENTITY_LINKS_SERVICE_USER}
    if handoff:
        headers[HANDOFF_SECRET_HEADER] = handoff
    elif not server_api_key and ingress:
        headers[INGRESS_SECRET_HEADER] = ingress
    client = get_client(
        url=api_url,
        api_key=api_key,
        headers=headers,
    )
    return LangGraphIdentityLinkStore(client)


class LangGraphIdentityLinkStore:
    """LangGraph Store-backed identity links."""

    def __init__(self, client: LangGraphClient) -> None:
        self._client = client

    async def get_by_slack_key(self, slack_key: str) -> IdentityLinkRecord | None:
        return await self._get_item(_slack_key(slack_key))

    async def get_by_user_id(self, user_id: str) -> IdentityLinkRecord | None:
        return await self._get_item(_actor_key(user_id))

    async def upsert(self, record: IdentityLinkRecord) -> None:
        store = self._client.store
        previous = await self.get_by_user_id(record.user_id)
        if previous and previous.slack_key != record.slack_key:
            await store.delete_item(IDENTITY_LINKS_NAMESPACE, _slack_key(previous.slack_key))
        existing_slack = await self.get_by_slack_key(record.slack_key)
        if existing_slack and existing_slack.user_id != record.user_id:
            await store.delete_item(IDENTITY_LINKS_NAMESPACE, _actor_key(existing_slack.user_id))
        value = _record_to_value(record)
        await store.put_item(IDENTITY_LINKS_NAMESPACE, _slack_key(record.slack_key), value)
        await store.put_item(IDENTITY_LINKS_NAMESPACE, _actor_key(record.user_id), value)

    async def remove_by_user_id(self, user_id: str) -> None:
        store = self._client.store
        existing = await self.get_by_user_id(user_id)
        if existing is None:
            return
        await store.delete_item(IDENTITY_LINKS_NAMESPACE, _actor_key(user_id))
        await store.delete_item(IDENTITY_LINKS_NAMESPACE, _slack_key(existing.slack_key))

    async def _get_item(self, key: str) -> IdentityLinkRecord | None:
        # The SDK types ``get_item`` as ``Item``, but the server answers a missing
        # key with ``null``, so keep the ``None`` check.
        item: Item | None = await self._client.store.get_item(IDENTITY_LINKS_NAMESPACE, key)
        if item is None:
            return None
        value = item.get("value")
        if not isinstance(value, dict):
            return None
        return _value_to_record(cast("dict[str, object]", value))


def slack_key_from_parts(team_id: str, user_id: str) -> str:
    return f"slack:{team_id}:{user_id}"


def build_identity_link_record(
    *,
    user_id: str,
    slack_user_id: str,
    slack_team_id: str | None = None,
    email: str | None = None,
) -> IdentityLinkRecord:
    team = (slack_team_id or "").strip() or "unknown"
    return IdentityLinkRecord(
        user_id=user_id,
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        slack_key=slack_key_from_parts(team, slack_user_id),
        email=email,
        linked_at=datetime.now(tz=UTC).isoformat(),
        source="slack_oauth",
    )


def _slack_key(slack_key: str) -> str:
    suffix = slack_key.removeprefix("slack:")
    return f"slack:{suffix}"


def _actor_key(user_id: str) -> str:
    return f"actor:{user_id}"


def _record_to_value(record: IdentityLinkRecord) -> dict[str, object]:
    raw = asdict(record)
    return {key: value for key, value in raw.items() if value is not None}


def _value_to_record(value: dict[str, object]) -> IdentityLinkRecord | None:
    user_id = value.get("user_id")
    slack_user_id = value.get("slack_user_id")
    slack_key = value.get("slack_key")
    if not isinstance(user_id, str) or not isinstance(slack_user_id, str):
        return None
    if not isinstance(slack_key, str):
        return None
    linked_at = value.get("linked_at")
    return IdentityLinkRecord(
        user_id=user_id,
        slack_user_id=slack_user_id,
        slack_key=slack_key,
        slack_team_id=str(value["slack_team_id"]) if value.get("slack_team_id") else None,
        email=str(value["email"]) if value.get("email") else None,
        linked_at=(
            str(linked_at) if isinstance(linked_at, str) else datetime.now(tz=UTC).isoformat()
        ),
        source="slack_oauth",
    )
