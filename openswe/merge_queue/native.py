"""Validate selected GitHub candidates; GitHub alone constructs and lands them."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from pydantic import BaseModel, Field

from openswe.github.http import RepoClient
from openswe.github.queue import NativeQueueSnapshot
from openswe.merge_queue.admission import digest
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.dispatch import DispatchGateway, DispatchRejected
from openswe.merge_queue.models import Lease
from openswe.merge_queue.runtime import QueueJob
from openswe.merge_queue.store import LeaseLost, MergeQueue
from openswe.merge_queue.validation import InvalidValidation, ValidationEvidence

logger = logging.getLogger(__name__)


class NativeRules(BaseModel):
    merge_method: str
    grouping_strategy: str
    max_entries_to_build: int = Field(gt=0)
    max_entries_to_merge: int = Field(gt=0)
    min_entries_to_merge: int = Field(gt=0)
    check_response_timeout_minutes: int = Field(gt=0)


class NativeQueueRuntime:
    def __init__(
        self,
        queue_id: UUID,
        repository_id: int,
        open_repo: Callable[[MergeQueue], AbstractAsyncContextManager[RepoClient]],
        *,
        config: QueueConfiguration | None = None,
        dispatcher: DispatchGateway | None = None,
    ):
        self.queue_id, self.repository_id, self.open_repo = queue_id, repository_id, open_repo
        self.config, self.dispatcher = config, dispatcher

    async def _timeout(self, repo: RepoClient) -> int:
        config = self.config
        assert config is not None and config.check_app_id is not None
        rules = await repo.branch_rules("main")
        queues = [
            NativeRules.model_validate(rule["parameters"])
            for rule in rules
            if rule["type"] == "merge_queue"
        ]
        gates = [
            check
            for rule in rules
            if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]
            if check["context"] == "merge-queue"
        ]
        if (
            len(queues) != 1
            or not gates
            or any(check.get("integration_id") != config.check_app_id for check in gates)
        ):
            raise ValueError("Native queue must require the trusted merge-queue check")
        rule = queues[0]
        validation = config.policy.validation
        assert validation is not None
        timeout = rule.check_response_timeout_minutes * 60
        if (
            rule.merge_method != "MERGE"
            or rule.grouping_strategy != "HEADGREEN"
            or rule.min_entries_to_merge != 1
            or config.policy.max_batch_size
            > min(rule.max_entries_to_build, rule.max_entries_to_merge)
            or config.policy.collection_seconds + validation.deadline_seconds + 30 >= timeout
        ):
            raise ValueError("Native queue limits cannot support this batch size and time budget")
        return timeout

    async def _validate(
        self, queue: MergeQueue, repo: RepoClient, snapshot: NativeQueueSnapshot, lease: Lease
    ) -> None:
        config, dispatcher = self.config, self.dispatcher
        assert config is not None and dispatcher is not None
        timeout = await self._timeout(repo)
        trust = digest(
            {"workflow_digest": config.workflow_digest, "check_app_id": config.check_app_id}
        )
        previous = await queue.native_validation_work(lease)
        if (
            previous is not None
            and previous.attempt.state == "succeeded"
            and snapshot.main_sha != previous.batch.base_sha
        ):
            if (
                await repo.merge_base(previous.request.candidate_sha, snapshot.main_sha)
                == previous.request.candidate_sha
            ):
                await queue.record_native_landing(previous, lease)
        work = await queue.prepare_native_validation(
            snapshot, timeout, lease, policy=config.policy, trust=trust
        )
        if work is None:
            return
        if work.dispatch:
            await queue.renew(lease, timedelta(seconds=60))
            try:
                await dispatcher.dispatch(work.request, work.attempt.deadline)
            except DispatchRejected as exc:
                await queue.finish_native_validation(
                    work, {"error": str(exc)}, lease, succeeded=False
                )
                return
            await dispatcher.report(work.request, work.attempt.deadline, "in_progress")
            return
        run_id = work.run_id
        if work.attempt.state == "running":
            evidence = ValidationEvidence(repo)
            try:
                run_id = await evidence.find_run(work.request, work.attempt.created_at, run_id)
                if run_id is None:
                    return
                await queue.bind_validation_run(work.attempt.id, run_id, lease)
                result = await evidence.result(work.request, run_id)
                if result is None:
                    return
            except InvalidValidation as exc:
                await queue.finish_native_validation(
                    work, {"error": str(exc)}, lease, succeeded=False
                )
                return
            await queue.finish_native_validation(work, result, lease, succeeded=True)
        # Saved results are final; the gateway verifies evidence before any new success write.
        assert run_id is not None
        snapshot = await repo.native_queue(queue.target_branch)
        if snapshot.repository_id != self.repository_id:
            raise ValueError("Native queue repository identity changed")
        if await self._timeout(repo) != timeout:
            raise ValueError("Native queue deadline changed during validation")
        await queue.renew(lease, timedelta(seconds=60))
        if await queue.authorize_native_result(work, snapshot, lease):
            await dispatcher.report(work.request, work.attempt.deadline, "success", run_id=run_id)

    async def reconcile(self, payload: QueueJob) -> None:
        if payload.queue_id != self.queue_id:
            return
        queue = await MergeQueue.load(payload.queue_id, payload.workspace_id)
        if queue is None or queue.mode != "native":
            return
        lease = await queue.claim(uuid7(), timedelta(seconds=60))
        if lease is None:
            return
        try:
            async with asyncio.timeout(45), self.open_repo(queue) as repo:
                snapshot = await repo.native_queue(queue.target_branch)
                if snapshot.repository_id != self.repository_id:
                    raise ValueError("Native queue repository identity changed")
                await queue.record_native_snapshot(snapshot, lease)
                if self.dispatcher is not None:
                    await self._validate(queue, repo, snapshot, lease)
        finally:
            if self.config is not None:
                try:
                    async with asyncio.timeout(10):
                        await queue.prune_history(
                            datetime.now(UTC) - timedelta(days=self.config.retention_days), lease
                        )
                except LeaseLost:
                    logger.info(
                        "Native queue cleanup lost its lease", extra={"queue_id": str(queue.id)}
                    )
                except Exception:
                    logger.exception(
                        "Native queue history cleanup failed", extra={"queue_id": str(queue.id)}
                    )
            try:
                async with asyncio.timeout(10):
                    await queue.release(lease)
            except LeaseLost:
                logger.info("Native queue lease no longer held", extra={"queue_id": str(queue.id)})
