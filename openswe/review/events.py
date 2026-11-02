"""Reviewer activity in the append-only event log."""

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Self
from uuid import uuid7

from pydantic import JsonValue, TypeAdapter

from openswe import event_claims
from openswe.github.pull_request_key import PullRequestKey
from openswe.review.findings import Finding
from openswe.run_config import RunConfig
from openswe.threads.store import THREADS
from openswe.webhooks.event_log import RETAINED_DAYS, EventLog, EventRefs

logger = logging.getLogger(__name__)


class ReviewEvents:
    def __init__(self, thread_id: str, pull_request: PullRequestKey) -> None:
        self.thread_id = thread_id
        self.pull_request = pull_request

    @classmethod
    def from_config(cls, cfg: RunConfig) -> Self | None:
        if not cfg.thread_id or not cfg.repo or cfg.pr_number is None:
            return None
        return cls(cfg.thread_id, PullRequestKey.of(cfg.repo.owner, cfg.repo.name, cfg.pr_number))

    async def append(
        self, event_type: str, *, delivery_id: str | None = None, **details: JsonValue
    ) -> None:
        claimed = False
        try:
            if delivery_id:
                claimed = await event_claims.claim(
                    "reviewer_event", delivery_id, ttl=timedelta(days=RETAINED_DAYS)
                )
                if not claimed:
                    return
            number = int(self.pull_request.rsplit("/", 1)[1])
            stored = await EventLog.append(
                "reviewer",
                {
                    "thread_id": self.thread_id,
                    "pull_request_url": f"https://github.com/{self.pull_request.repo_full_name}/pull/{number}",
                    **details,
                },
                event_type=event_type,
                delivery_id=delivery_id or str(uuid7()),
                refs=EventRefs(
                    github_repository=self.pull_request.repo_full_name,
                    pull_request_number=number,
                ),
            )
            if not stored and claimed and delivery_id:
                await event_claims.release("reviewer_event", delivery_id)
        except Exception:
            logger.exception(
                "Could not log reviewer activity",
                extra={"thread_id": self.thread_id, "reviewer_event_type": event_type},
            )

    @classmethod
    async def completed(cls, thread_id: str, run_id: str | None, status: object) -> None:
        if (
            not run_id
            or not isinstance(status, str)
            or status not in {"success", "error", "timeout", "interrupted", "canceled"}
        ):
            return
        try:
            thread = await THREADS.get(thread_id)
            if thread is None or thread.metadata.get("kind") != "reviewer":
                return
            pr = thread.metadata.get("pr")
            if not isinstance(pr, dict):
                return
            owner, repo, number = pr.get("owner"), pr.get("name"), pr.get("number")
            if (
                not isinstance(owner, str)
                or not isinstance(repo, str)
                or not isinstance(number, int)
            ):
                return
            event_type = (
                "run.completed"
                if status == "success"
                else "run.canceled"
                if status in {"interrupted", "canceled"}
                else "run.failed"
            )
            await cls(thread_id, PullRequestKey.of(owner, repo, number)).append(
                event_type,
                delivery_id=f"{thread_id}:{run_id}:ended",
                run_id=run_id,
                status=str(status),
            )
        except Exception:
            logger.exception("Could not log reviewer completion", extra={"thread_id": thread_id})

    async def finding_changes(
        self, before: Mapping[str, Mapping[str, object]], findings: list[Finding]
    ) -> None:
        for record in findings:
            finding = TypeAdapter(dict[str, JsonValue]).validate_python(record)
            finding_id = finding.get("id")
            previous = before.get(finding_id) if isinstance(finding_id, str) else None
            await self.append(
                "finding.created" if previous is None else "finding.updated",
                finding=finding,
                changed_fields=[
                    key for key in finding if previous is None or previous.get(key) != finding[key]
                ],
            )
