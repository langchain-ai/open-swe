"""Queue-only, minimized Segment lifecycle delivery."""

import math
from datetime import datetime
from uuid import UUID

import httpx2
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import text

from openswe.analytics.events import EntryPoint, EventEnvelope, EventName, person_uuid
from openswe.config import ENV
from openswe.database import transaction
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.utils.http_pool import pooled_transport

SEGMENT_JOB = "segment_track"
_EVENTS = {f"agents_{name.value.replace('.', '_')}" for name in EventName} | {
    "agents_request_started",
    "agents_tool_executed",
}
_UUID_FIELDS = {
    "event_id",
    "workspace_id",
    "run_id",
    "runtime_run_id",
    "thread_id",
    "task_id",
    "pr_id",
    "review_id",
    "finding_id",
    "repository_id",
    "model_id",
    "configured_model_id",
    "effective_model_id",
    "originating_model_id",
    "opening_run_id",
    "submission_event_id",
}
_COUNTS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "observation_revision",
    "additions",
    "deletions",
    "changed_files",
    "finding_count",
    "human_replies",
    "distance_basis_points",
}
_ENUMS: dict[str, set[str]] = {
    "source": {
        "backend",
        "mcp",
        "web",
        "dashboard",
        "slack",
        "github",
        "linear",
        "api",
        "scheduled",
        "unknown",
    },
    "entry_point": {entry.value for entry in EntryPoint},
    "activity_kind": {"lifecycle", "agent_work", "human_request", "automatic_work", "unknown"},
    "identity_quality": {"canonical", "anonymous", "unresolved"},
    "attribution_role": {"actor", "owner", "unknown"},
    "environment": {
        "production",
        "prod",
        "staging",
        "preview",
        "development",
        "dev",
        "test",
        "local",
    },
    "model_attribution_quality": {"effective", "configured", "unavailable"},
    "configured_effort": {"none", "minimal", "low", "medium", "high", "xhigh", "max"},
    "failure_class": {"error", "timeout", "unavailable"},
    "cancellation_source": {"user", "system", "superseded", "unavailable"},
    "status": {"complete", "partial", "unavailable"},
    "cost_source": {"provider", "langsmith", "unavailable"},
    "sentiment": {"positive", "neutral", "negative"},
    "completion_source": {"user", "pr_terminal"},
    "acceptance_source": {"explicit_user", "external_system"},
    "scope": {"minor", "major"},
    "request_source": {"explicit_user", "plan_review", "pr_reopened"},
    "link_role": {"opening", "follow_up", "review"},
    "previous_state": {"open", "draft", "closed", "merged", "resolved", "dismissed", "unknown"},
    "current_state": {"open", "resolved", "dismissed"},
    "severity": {"low", "medium", "high", "critical"},
    "trigger_kind": {"human", "system", "context", "unknown"},
    "run_kind": {"agent", "reviewer", "review_scout", "unknown"},
}


class SegmentTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageId: UUID
    event: str
    timestamp: datetime
    userId: UUID | None = None
    anonymousId: UUID | None = None
    properties: dict[str, JsonValue]

    def minimized(self) -> dict[str, JsonValue]:
        if self.event not in _EVENTS or self.timestamp.tzinfo is None:
            raise ValueError("Invalid Segment event")
        if self.userId is None and self.anonymousId is None:
            raise ValueError("Segment requires a canonical or anonymous identity")
        body: dict[str, JsonValue] = {
            "messageId": str(self.messageId),
            "event": self.event,
            "timestamp": self.timestamp.isoformat(),
            "properties": SegmentDelivery.minimize(self.properties),
            "context": {"ip": "0.0.0.0"},
        }
        body["userId" if self.userId else "anonymousId"] = str(self.userId or self.anonymousId)
        return body


class SegmentDelivery:
    @staticmethod
    def minimize(properties: dict[str, JsonValue]) -> dict[str, JsonValue]:
        safe: dict[str, JsonValue] = {"product": "open-swe", "schema_version": 1}
        for key, value in properties.items():
            if key in _UUID_FIELDS and isinstance(value, str):
                try:
                    safe[key] = str(UUID(value))
                except ValueError:
                    continue
            elif key in _ENUMS and isinstance(value, str):
                safe[key] = value if value in _ENUMS[key] else "unknown"
            elif key in _COUNTS and type(value) is int and 0 <= value <= 2**63 - 1:
                safe[key] = value
            elif key == "rating" and type(value) is int and 1 <= value <= 5:
                safe[key] = value
            elif key in {"repository_private", "is_error"} and isinstance(value, bool):
                safe[key] = value
            elif (
                key == "cost_usd" and isinstance(value, int | float) and not isinstance(value, bool)
            ):
                if math.isfinite(value) and 0 <= value <= 10**12:
                    safe[key] = value
            elif key == "tool" and isinstance(value, str):
                from openswe.analytics.segment_activity import SegmentActivity

                safe[key] = SegmentActivity.safe_tool_name(value)
        return safe

    @classmethod
    async def enqueue_track(
        cls,
        *,
        message_id: UUID,
        occurred_at: datetime,
        workspace_id: UUID,
        event: str,
        user_id: UUID | None = None,
        anonymous_id: UUID | None = None,
        properties: dict[str, JsonValue],
    ) -> None:
        if not ENV.SEGMENT_BROWSER_WRITE_KEY.get().strip():
            return
        track = SegmentTrack(
            messageId=message_id,
            timestamp=occurred_at,
            event=event,
            userId=user_id,
            anonymousId=anonymous_id or (None if user_id else message_id),
            properties={
                **properties,
                "workspace_id": str(workspace_id),
                "identity_quality": "canonical" if user_id else "unresolved",
                "attribution_role": (
                    "actor" if properties.get("attribution_role") == "actor" else "owner"
                )
                if user_id
                else "unknown",
            },
        )
        body = track.minimized()
        body.pop("context")
        await queue.schedule(
            SEGMENT_JOB, body, key=f"segment:{workspace_id}:{message_id}", replace=False
        )

    @classmethod
    async def enqueue_event(cls, event: EventEnvelope) -> None:
        if not ENV.SEGMENT_BROWSER_WRITE_KEY.get().strip():
            return
        async with transaction() as conn:
            context = (
                (
                    await conn.execute(
                        text(
                            """
                        SELECT coalesce(r.user_id, t.user_id) AS user_id,
                               r.entry_point, r.configured_model_id, r.effective_model_id,
                               coalesce(r.model_attribution_quality, p.model_attribution_quality)
                                   AS model_attribution_quality,
                               p.originating_model_id
                        FROM (SELECT 1) AS anchor
                        LEFT JOIN finding_projection f
                          ON f.workspace_id = :workspace AND f.finding_id = :finding
                        LEFT JOIN review_projection v
                          ON v.workspace_id = :workspace
                         AND v.review_id = coalesce(:review, f.review_id)
                        LEFT JOIN pr_projection p
                          ON p.workspace_id = :workspace
                         AND p.pr_id = coalesce(:pr, f.pr_id, v.pr_id)
                        LEFT JOIN run_projection r
                          ON r.workspace_id = :workspace
                         AND r.run_id = coalesce(:run, p.opening_run_id)
                        LEFT JOIN task_projection t
                          ON t.workspace_id = :workspace AND t.task_id = :task
                        """
                        ),
                        {
                            "workspace": event.workspace_id,
                            "run": event.run_id,
                            "pr": event.pr_id,
                            "review": event.review_id,
                            "finding": event.finding_id,
                            "task": event.task_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
        person = event.user_id or context["user_id"]
        canonical = await cls.canonical_user(event.workspace_id, person) if person else None
        properties = event.payload.model_dump(mode="json", exclude_none=True)
        if "cost_usd" in properties:
            properties["cost_usd"] = float(properties["cost_usd"])
        if event.event_name == EventName.RUN_COST_RECORDED:
            properties["cost_source"] = properties.pop("source")
        for field in _UUID_FIELDS:
            value = getattr(event, field, None)
            if isinstance(value, UUID):
                properties[field] = str(value)
        for field in (
            "configured_model_id",
            "effective_model_id",
            "originating_model_id",
            "model_attribution_quality",
        ):
            if field not in properties and context[field] is not None:
                properties[field] = str(context[field])
        properties.update(
            source="backend",
            activity_kind="lifecycle",
            attribution_role=(
                "actor"
                if event.user_id
                and event.event_name in {EventName.FEEDBACK_SUBMITTED, EventName.TASK_ACCEPTED}
                else "owner"
            ),
            environment=event.environment,
            entry_point=(
                event.entry_point.value
                if event.entry_point != EntryPoint.UNKNOWN
                else context["entry_point"] or "unknown"
            ),
        )
        await cls.enqueue_track(
            message_id=event.event_id,
            occurred_at=event.occurred_at,
            workspace_id=event.workspace_id,
            event=f"agents_{event.event_name.value.replace('.', '_')}",
            user_id=canonical,
            anonymous_id=person or event.thread_id or event.event_id,
            properties=properties,
        )

    @staticmethod
    async def canonical_user(workspace: UUID, person: UUID) -> UUID | None:
        async with transaction() as conn:
            people = set(
                (
                    await conn.execute(
                        text(
                            """
                            WITH owners AS (
                                SELECT :person AS person_id
                                UNION SELECT person_id FROM identity_aliases
                                WHERE workspace_id = :workspace AND alias_person_id = :person
                            )
                            SELECT person_id FROM owners
                            UNION SELECT alias_person_id FROM identity_aliases
                            WHERE workspace_id = :workspace
                              AND person_id IN (SELECT person_id FROM owners)
                            """
                        ),
                        {"workspace": workspace, "person": person},
                    )
                ).scalars()
            )
            candidates = list(
                (
                    await conn.execute(
                        text(
                            """
                        SELECT DISTINCT i.user_id, i.provider, i.external_id
                        FROM identity_directory d JOIN user_identity i ON
                            (i.provider = 'github' AND i.login <> ''
                             AND lower(i.login) = lower(d.github_login))
                            OR (i.email <> '' AND lower(i.email) = lower(d.email))
                        WHERE d.workspace_id = :workspace AND d.person_id = ANY(:people)
                        LIMIT 101
                        """
                        ),
                        {"workspace": workspace, "people": list(people)},
                    )
                ).mappings()
            )
            if len(candidates) > 100:
                return None
            matches = {
                row["user_id"]
                for row in candidates
                if person_uuid(workspace, row["provider"], row["external_id"].strip().lower())
                in people
            }
            return matches.pop() if len(matches) == 1 else None

    @staticmethod
    async def deliver(job: Job) -> None:
        key = ENV.SEGMENT_BROWSER_WRITE_KEY.get().strip()
        if not key:
            return
        track = SegmentTrack.model_validate(job.payload)
        async with httpx2.AsyncClient(
            transport=pooled_transport(),
            timeout=httpx2.Timeout(5.0, connect=2.0),
            follow_redirects=False,
        ) as client:
            response = await client.post(
                "https://api.segment.io/v1/track",
                json=track.minimized(),
                auth=httpx2.BasicAuth(key, ""),
            )
            response.raise_for_status()
