"""Append-only log of verified GitHub, Slack, Linear, and deployment deliveries."""

import asyncio
import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Self
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import text

from openswe.database import configured, transaction
from openswe.github.pull_request_key import PullRequestKey
from openswe.slack.payloads import SlackEventEnvelope
from openswe.slack.pr_links import event_pull_requests

logger = logging.getLogger(__name__)


type WebhookSource = Literal["github", "slack", "linear", "deployment", "thread"]

RETAINED_DAYS = 2
_TABLE = "event_log"
_ROTATE_INTERVAL_SECONDS = 3600

_ROTATED_AT: float | None = None
_ROTATION_LOCK = asyncio.Lock()
_SEGMENT_TASKS: set[asyncio.Task[None]] = set()

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
    RETURNING source, event_type, delivery_id, received_at,
        user_id, workspace_id, repository_id, pull_request_id
    """
)

_EVENT_KINDS = text(
    f"""
    SELECT source, event_type, count(*) AS count, max(received_at) AS last_received_at
    FROM {_TABLE}
    WHERE received_at >= :since
      AND (CAST(:source AS text) IS NULL OR source = :source)
      AND (CAST(:event_type AS text) = ''
           OR event_type = :event_type
           OR starts_with(event_type, :event_type || '.'))
    GROUP BY 1, 2
    ORDER BY 1, 2
    """
)

_LATEST_PAYLOAD = text(
    f"""
    SELECT payload
    FROM {_TABLE}
    WHERE source = :source AND event_type = :event_type AND received_at >= :since
    ORDER BY received_at DESC
    LIMIT 1
    """
)
_SHAPE_DEPTH = 6


class LoggedEvent(BaseModel):
    """A row as written, with the links resolved on insert."""

    source: WebhookSource
    event_type: str
    delivery_id: str
    received_at: datetime
    user_id: UUID | None
    workspace_id: UUID | None
    repository_id: UUID | None
    pull_request_id: UUID | None
    payload: JsonValue

    @property
    def base_event_type(self) -> str:
        """``event_type`` without its ``.<action>`` suffix, e.g. ``pull_request``."""
        return self.event_type.partition(".")[0]


class EventKind(BaseModel):
    source: WebhookSource
    event_type: str
    count: int
    last_received_at: datetime
    payload_shape: JsonValue = None


class EventRefs(BaseModel):
    """External identifiers a delivery names, resolved into row links on insert."""

    github_repository: str = ""
    github_user_id: str = ""
    pull_request_number: int | None = None
    slack_user_id: str = ""
    slack_channel_id: str = ""
    email: str = ""

    @property
    def pull_request(self) -> PullRequestKey | None:
        owner, _, repo = self.github_repository.partition("/")
        if not owner or not repo or self.pull_request_number is None:
            return None
        return PullRequestKey.of(owner, repo, self.pull_request_number)

    @classmethod
    def github(cls, body: bytes) -> Self:
        try:
            delivery = _GitHubDelivery.model_validate_json(body)
        except ValidationError:
            logger.debug("GitHub delivery names no linkable rows", exc_info=True)
            return cls()
        number = delivery.pull_request.number if delivery.pull_request else None
        if number is None and delivery.issue and delivery.issue.pull_request:
            number = delivery.issue.number
        if number is None:
            number = next(
                (
                    check.pull_requests[0].number
                    for check in (delivery.check_run, delivery.check_suite, delivery.workflow_run)
                    if check and check.pull_requests
                ),
                None,
            )
        return cls(
            github_repository=delivery.repository.full_name if delivery.repository else "",
            github_user_id=str(delivery.sender.id)
            if delivery.sender and delivery.sender.id
            else "",
            pull_request_number=number,
        )

    @classmethod
    def slack(cls, envelope: SlackEventEnvelope) -> Self:
        event = envelope.event
        if event is None:
            return cls()
        message = event.message if event.subtype == "message_changed" else event
        refs = event_pull_requests(envelope)
        ref = refs[0] if len(refs) == 1 else None
        return cls(
            github_repository=f"{ref.owner}/{ref.repo}" if ref else "",
            pull_request_number=ref.number if ref else None,
            slack_user_id=message.user
            if message and isinstance(message.user, str)
            else event.resolve_user_id(),
            slack_channel_id=event.resolve_channel_id(),
        )

    @classmethod
    def linear(cls, body: bytes) -> Self:
        try:
            delivery = _LinearDelivery.model_validate_json(body)
        except ValidationError:
            logger.debug("Linear delivery names no linkable rows", exc_info=True)
            return cls()
        email = delivery.data.user.email if delivery.data and delivery.data.user else ""
        if not email and delivery.actor:
            email = delivery.actor.email
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


class _GitHubCheck(BaseModel):
    pull_requests: list[_GitHubPullRequest] = []


class _GitHubDelivery(BaseModel):
    repository: _GitHubRepository | None = None
    sender: _GitHubAccount | None = None
    pull_request: _GitHubPullRequest | None = None
    issue: _GitHubIssue | None = None
    check_run: _GitHubCheck | None = None
    check_suite: _GitHubCheck | None = None
    workflow_run: _GitHubCheck | None = None


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
    ) -> bool:
        """Log, then wake subscribed threads.

        Never raises. False means the row was not stored, so a caller that must
        not drop the delivery can ask the sender to retry.
        """
        from openswe.webhooks.event_subscriptions import EventSubscription  # noqa: PLC0415

        if not configured():
            return False
        if source == "slack":
            from openswe.slack.channels import SlackChannel

            channel_id = refs.slack_channel_id if refs else ""
            try:
                channel = await SlackChannel.load(channel_id)
            except Exception:
                logger.warning("Checking event log Slack channel failed", exc_info=True)
                return True
            if channel is None or not channel.details.publishes_events:
                return True
        try:
            await cls.ensure_partitions()
        except Exception:  # noqa: BLE001
            logger.warning("Rotating event log partitions failed", exc_info=True)
        payload = cls._decode(request, body)
        action = payload.get("action") if isinstance(payload, dict) else None
        if event_type and isinstance(action, str) and action:
            event_type = f"{event_type}.{action}"
        try:
            async with transaction() as conn:
                result = await conn.execute(
                    _INSERT,
                    {
                        "source": source,
                        "endpoint": request.url.path,
                        "event_type": event_type,
                        "delivery_id": delivery_id,
                        "payload": json.dumps(payload),
                        **(refs or EventRefs()).model_dump(),
                    },
                )
                row = result.mappings().one()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Recording a webhook in the event log failed",
                extra={"webhook_source": source, "webhook_endpoint": request.url.path},
                exc_info=True,
            )
            return False
        from openswe.analytics.segment import record_webhook

        event = LoggedEvent.model_validate({**row, "payload": payload})
        task = asyncio.create_task(record_webhook(event))
        _SEGMENT_TASKS.add(task)
        task.add_done_callback(_SEGMENT_TASKS.discard)
        await EventSubscription.deliver(event)
        return True

    @classmethod
    async def kinds(
        cls, since: datetime, *, source: WebhookSource | None = None, event_type: str = ""
    ) -> list[EventKind]:
        """Every distinct source and event type received since ``since``.

        ``event_type`` narrows to that type and its ``.<action>`` variants. With
        ``source``, an exact match also gets the newest payload's shape: keys and value
        types, never values.
        """
        await cls.ensure_partitions()
        params = {"since": since, "source": source, "event_type": event_type}
        async with transaction() as conn:
            rows = await conn.execute(_EVENT_KINDS, params)
            kinds = [EventKind.model_validate(dict(row)) for row in rows.mappings()]
            if source is None or not any(kind.event_type == event_type for kind in kinds):
                return kinds
            payload = await conn.scalar(_LATEST_PAYLOAD, params)
        return [
            kind.model_copy(update={"payload_shape": cls.shape(payload)})
            if kind.event_type == event_type
            else kind
            for kind in kinds
        ]

    @classmethod
    def shape(cls, value: JsonValue, depth: int = 0) -> JsonValue:
        """``value`` with every leaf replaced by its JSON type name."""
        if isinstance(value, dict):
            if depth >= _SHAPE_DEPTH:
                return "object"
            return {key: cls.shape(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [cls.shape(value[0], depth + 1)] if value else []
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if value is None:
            return "null"
        return "string"

    @classmethod
    async def ensure_partitions(cls) -> None:
        """Rotate at most once an hour per process; call before every read or write."""
        global _ROTATED_AT
        if cls._rotated_recently():
            return
        async with _ROTATION_LOCK:
            if cls._rotated_recently():
                return
            await cls.rotate_partitions()
            _ROTATED_AT = time.monotonic()

    @staticmethod
    def _rotated_recently() -> bool:
        return _ROTATED_AT is not None and time.monotonic() - _ROTATED_AT < _ROTATE_INTERVAL_SECONDS

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
