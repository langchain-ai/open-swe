"""Append-only log of verified GitHub, Slack, and Linear webhook deliveries."""

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Self
from urllib.parse import parse_qs

from fastapi import Request
from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import text

from agent.database import configured, transaction

logger = logging.getLogger(__name__)

type WebhookSource = Literal["github", "slack", "linear"]

RETAINED_DAYS = 2
_TABLE = "event_log"
_ROTATE_INTERVAL_SECONDS = 3600

_ROTATED_AT: float | None = None

_INSERT = text(
    f"""
    INSERT INTO {_TABLE} (
        source, endpoint, event_type, delivery_id, payload,
        user_id, workspace_id, repository_id, pull_request_id
    )
    SELECT
        :source, :endpoint, :event_type, :delivery_id, CAST(:payload AS jsonb),
        COALESCE(
            (SELECT user_id FROM user_identity
             WHERE provider = 'github' AND external_id = :github_user_id),
            (SELECT user_id FROM user_identity
             WHERE provider = 'slack' AND external_id = :slack_user_id),
            (SELECT user_id FROM user_identity
             WHERE email <> '' AND lower(email) = lower(CAST(:email AS text)) LIMIT 1)
        ),
        COALESCE(
            workspace_repository.workspace_id,
            (SELECT workspace_id FROM workspace_slack_channel
             WHERE channel_id = :slack_channel_id)
        ),
        repository.id,
        (SELECT id FROM pull_request
         WHERE repository_id = repository.id AND number = :pull_request_number)
    FROM (SELECT 1) AS delivery
    LEFT JOIN repository ON repository.key = lower(CAST(:github_repository AS text))
    LEFT JOIN workspace_repository ON workspace_repository.repository_id = repository.id
    """
)


class EventRefs(BaseModel):
    """External identifiers a delivery names, resolved into row links on insert."""

    github_repository: str = ""
    github_user_id: str = ""
    pull_request_number: int | None = None
    slack_user_id: str = ""
    slack_channel_id: str = ""
    email: str = ""

    @classmethod
    def github(cls, body: bytes) -> Self:
        try:
            delivery = _GitHubDelivery.model_validate_json(body)
        except ValidationError:
            return cls()
        number = delivery.pull_request.number if delivery.pull_request else None
        if number is None and delivery.issue and delivery.issue.pull_request:
            number = delivery.issue.number
        return cls(
            github_repository=delivery.repository.full_name if delivery.repository else "",
            github_user_id=str(delivery.sender.id)
            if delivery.sender and delivery.sender.id
            else "",
            pull_request_number=number,
        )

    @classmethod
    def linear(cls, body: bytes) -> Self:
        try:
            delivery = _LinearDelivery.model_validate_json(body)
        except ValidationError:
            return cls()
        email = delivery.actor.email if delivery.actor else ""
        if not email and delivery.data and delivery.data.user:
            email = delivery.data.user.email
        return cls(email=email)


class _GitHubAccount(BaseModel):
    id: int | None = None


class _GitHubRepository(BaseModel):
    full_name: str = ""


class _GitHubPullRequest(BaseModel):
    number: int | None = None


class _GitHubIssue(BaseModel):
    number: int | None = None
    pull_request: JsonValue = None


class _GitHubDelivery(BaseModel):
    repository: _GitHubRepository | None = None
    sender: _GitHubAccount | None = None
    pull_request: _GitHubPullRequest | None = None
    issue: _GitHubIssue | None = None


class _LinearUser(BaseModel):
    email: str = ""


class _LinearData(BaseModel):
    user: _LinearUser | None = None


class _LinearDelivery(BaseModel):
    actor: _LinearUser | None = None
    data: _LinearData | None = None


class EventLog:
    @classmethod
    async def record(
        cls,
        request: Request,
        body: bytes,
        source: WebhookSource,
        *,
        event_type: str = "",
        delivery_id: str = "",
        refs: EventRefs | None = None,
    ) -> None:
        """Never raises: a delivery that cannot be logged is still handled."""
        if not configured():
            return
        try:
            await cls.ensure_partitions()
        except Exception:  # noqa: BLE001
            logger.warning("Rotating event log partitions failed", exc_info=True)
        try:
            async with transaction() as conn:
                await conn.execute(
                    _INSERT,
                    {
                        "source": source,
                        "endpoint": request.url.path,
                        "event_type": event_type,
                        "delivery_id": delivery_id,
                        "payload": json.dumps(cls._decode(request, body)),
                        **(refs or EventRefs()).model_dump(),
                    },
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Recording a webhook in the event log failed",
                extra={"webhook_source": source, "webhook_endpoint": request.url.path},
                exc_info=True,
            )

    @classmethod
    async def ensure_partitions(cls) -> None:
        """Rotate at most once an hour per process; call before every read or write."""
        global _ROTATED_AT
        now = time.monotonic()
        if _ROTATED_AT is not None and now - _ROTATED_AT < _ROTATE_INTERVAL_SECONDS:
            return
        _ROTATED_AT = now
        try:
            await cls.rotate_partitions()
        except Exception:
            _ROTATED_AT = None
            raise

    @classmethod
    async def rotate_partitions(cls, today: date | None = None) -> None:
        """Create today's and tomorrow's partitions and drop those older than the window."""
        today = today or datetime.now(UTC).date()
        oldest = today - timedelta(days=RETAINED_DAYS - 1)
        async with transaction() as conn:
            await conn.execute(text(f"SELECT pg_advisory_xact_lock(hashtext('{_TABLE}'))"))
            for day in (today, today + timedelta(days=1)):
                await conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {cls._partition(day)} PARTITION OF {_TABLE} "
                        f"FOR VALUES FROM ('{day.isoformat()} 00:00+00') "
                        f"TO ('{(day + timedelta(days=1)).isoformat()} 00:00+00')"
                    )
                )
            partitions = await conn.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    f"WHERE i.inhparent = '{_TABLE}'::regclass"
                )
            )
            for name in partitions.scalars().all():
                if date.fromisoformat(name.removeprefix(f"{_TABLE}_")) < oldest:
                    await conn.execute(text(f"DROP TABLE {name}"))

    @staticmethod
    def _partition(day: date) -> str:
        return f"{_TABLE}_{day.strftime('%Y%m%d')}"

    @staticmethod
    def _decode(request: Request, body: bytes) -> JsonValue:
        decoded = body.decode("utf-8", errors="replace")
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            return {key: values[-1] for key, values in parse_qs(decoded).items()}
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
