"""Admission is derived from GitHub protections and trusted check provenance."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openswe.github.ci import RequiredCheck
from openswe.github.http import RepoClient
from openswe.github.queue import Check, QueuePull, WorkflowRun
from openswe.merge_queue.git import SHA
from openswe.merge_queue.validation_contract import ValidationPolicy

ADMISSION_MAX_AGE = timedelta(seconds=120)


class CheckSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    app_id: int = Field(gt=0)
    workflow_path: str = Field(pattern=r"^\.github/workflows/[a-zA-Z0-9_-]+\.ya?ml$")
    review_artifact_prefix: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+$")


class QueuePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    checks: dict[str, CheckSource]
    max_batch_size: int = Field(default=1, ge=1, le=100)
    collection_seconds: int = Field(default=1800, ge=0, le=1800)
    deadline_seconds: int = Field(default=7200, ge=60, le=86400)
    max_build_attempts: int = Field(default=3, ge=1, le=10)
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_plans: int = Field(default=3, ge=0, le=100)
    validation: ValidationPolicy | None = None

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


class Admission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    head_sha: SHA
    base_sha: SHA
    checked_at: datetime
    policy_digest: str
    snapshot_digest: str
    merged_dependencies: tuple[Annotated[int, Field(gt=0)], ...] = ()


class AdmissionRejected(ValueError):
    def __init__(self, reason: str, *, terminal: bool = False, merged: bool = False):
        super().__init__(reason)
        self.terminal, self.merged = terminal, merged


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class AdmissionReader:
    def __init__(self, repo: RepoClient, branch: str, policy: QueuePolicy):
        self.repo, self.branch, self.policy = repo, branch, policy

    def _same_repository(self, pull: QueuePull) -> None:
        if pull.branch != self.branch or any(
            name is None or name.lower() != self.repo.full_name.lower()
            for name in (pull.head_repository, pull.base_repository)
        ):
            raise AdmissionRejected(
                "PR must originate in and target the configured repository and branch",
                terminal=True,
            )

    async def _inputs(
        self, number: int
    ) -> tuple[QueuePull, tuple[RequiredCheck, ...], tuple[Check, ...]]:
        pull = await self.repo.pull_request(number).queue_state()
        rules = await RequiredCheck.for_branch(self.repo, self.branch, include_review_checks=True)
        if not rules:
            raise AdmissionRejected("Required checks are missing or unreadable")
        statuses = await self.repo.commit_statuses(pull.head)
        runs = [Check.model_validate(run) for run in await self.repo.check_runs(pull.head)]
        selected: list[Check] = []
        ordered = tuple(sorted(rules, key=lambda rule: (rule.name, rule.app_id or 0)))
        for rule in ordered:
            source = self.policy.checks.get(rule.name)
            if source is None or rule.app_id not in (None, source.app_id):
                raise AdmissionRejected(f"Required check source is not configured: {rule.name}")
            if any(status.get("context") == rule.name for status in statuses):
                raise AdmissionRejected("Legacy commit status admission is unsupported")
            latest = max(
                (
                    run
                    for run in runs
                    if run.name == rule.name
                    and run.app_id == source.app_id
                    and run.head_sha == pull.head
                ),
                key=lambda run: run.id,
                default=None,
            )
            if (
                latest is None
                or latest.status != "completed"
                or latest.conclusion not in {"success", "skipped", "neutral"}
            ):
                raise AdmissionRejected(f"Required check is not successful: {rule.name}")
            selected.append(latest)
        return pull, ordered, tuple(selected)

    async def check(self, number: int, head_sha: str, dependencies: tuple[int, ...]) -> Admission:
        checked_at = datetime.now(UTC)
        pull, rules, checks = await self._inputs(number)
        self._same_repository(pull)
        if pull.merged or pull.state != "OPEN":
            raise AdmissionRejected("PR is no longer open", terminal=True, merged=pull.merged)
        if pull.head != head_sha:
            raise AdmissionRejected("PR head changed; revalidate the new head")
        if pull.draft or pull.decision != "APPROVED":
            raise AdmissionRejected("PR needs approval or is a draft")
        merge_base = await self.repo.merge_base(pull.target, head_sha)
        paths = sorted({self.policy.checks[check.name].workflow_path for check in checks})
        before = await self.repo.read_files(paths, merge_base)
        after = await self.repo.read_files(paths, head_sha)
        if set(before) != set(paths) or before != after:
            raise AdmissionRejected("Required workflow changes need separate review")
        runs: dict[int, WorkflowRun] = {}
        for check in checks:
            source = self.policy.checks[check.name]
            if check.suite_id not in runs:
                runs[check.suite_id] = await self.repo.workflow_for_suite(check.suite_id)
            run = runs[check.suite_id]
            if (
                run.head_sha != head_sha
                or run.path != source.workflow_path
                or run.repository.lower() != self.repo.full_name.lower()
            ):
                raise AdmissionRejected("Required check workflow evidence does not match")
            if source.review_artifact_prefix is not None:
                if (
                    check.conclusion != "success"
                    or check.started_at is None
                    or any(review.updated_at >= check.started_at for review in pull.reviews)
                ):
                    raise AdmissionRejected(
                        "Approval check must succeed after the latest review change"
                    )
                if run.event not in {"pull_request_target", "pull_request_review"} or not any(
                    item.number == number and item.head == head_sha for item in run.pull_requests
                ):
                    raise AdmissionRejected("Approval workflow is not bound to this PR revision")
                evidence = await self.repo.admission_artifact(
                    run.id, f"{source.review_artifact_prefix}-{run.run_attempt}"
                )
                expected: dict[str, JsonValue] = {
                    "repository": self.repo.full_name,
                    "number": number,
                    "headRefOid": head_sha,
                    "baseRefOid": pull.base,
                }
                if evidence != expected:
                    raise AdmissionRejected("Approval evidence does not match the current PR")
        merged: list[int] = []
        for number in dependencies:
            dependency = await self.repo.pull_request(number).queue_state()
            self._same_repository(dependency)
            if dependency.merged:
                merged.append(number)
            elif dependency.state != "OPEN":
                raise AdmissionRejected(f"Dependency #{number} is closed without merging")
        if await self._inputs(pull.number) != (pull, rules, checks):
            raise AdmissionRejected("PR, reviews or checks changed during admission")
        return Admission(
            head_sha=head_sha,
            base_sha=pull.target,
            checked_at=checked_at,
            policy_digest=self.policy.fingerprint,
            snapshot_digest=digest(
                [
                    pull.model_dump(mode="json"),
                    [asdict(rule) for rule in rules],
                    [check.model_dump(mode="json") for check in checks],
                ]
            ),
            merged_dependencies=tuple(merged),
        )
