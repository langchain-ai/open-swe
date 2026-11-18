"""Validate selected GitHub candidates; GitHub alone constructs and lands them."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import httpx2
from pydantic import BaseModel, Field

from openswe.github.http import RepoClient
from openswe.github.queue import NativeQueueSnapshot
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.dispatch import DispatchGateway, DispatchRejected, WorkflowPolicyMismatch
from openswe.merge_queue.models import Lease
from openswe.merge_queue.runtime import QueueJob
from openswe.merge_queue.store import LeaseLost, MergeQueue
from openswe.merge_queue.validation import InvalidValidation, ValidationEvidence
from openswe.merge_queue.validation_contract import WORKFLOW

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
        open_recovery_repo: Callable[[MergeQueue], AbstractAsyncContextManager[RepoClient]]
        | None = None,
        config: QueueConfiguration | None = None,
        dispatcher: DispatchGateway | None = None,
    ):
        self.queue_id, self.repository_id, self.open_repo = queue_id, repository_id, open_repo
        self.config, self.dispatcher = config, dispatcher
        self.open_recovery_repo = open_recovery_repo

    async def _timeout(self, repo: RepoClient, policy: QueuePolicy | None = None) -> int:
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
        policy = policy or config.policy
        validation = policy.validation
        assert validation is not None
        timeout = rule.check_response_timeout_minutes * 60
        if (
            rule.merge_method != "MERGE"
            or rule.grouping_strategy != "HEADGREEN"
            or rule.min_entries_to_merge != 1
            or policy.max_batch_size > min(rule.max_entries_to_build, rule.max_entries_to_merge)
            or policy.collection_seconds + validation.deadline_seconds + 30 >= timeout
        ):
            raise ValueError("Native queue limits cannot support this batch size and time budget")
        return timeout

    async def _validate(
        self, queue: MergeQueue, repo: RepoClient, snapshot: NativeQueueSnapshot, lease: Lease
    ) -> None:
        config, dispatcher = self.config, self.dispatcher
        assert config is not None and dispatcher is not None
        revision = config.workflow_policy_revision if config.sync_workflow_policy else None
        workflow_digest = config.workflow_digest if config.sync_workflow_policy else None
        trust = config.native_trust
        history = await queue.native_validation_history(lease)
        previous = history[0] if history else None
        for work in history:
            if (
                work.attempt.state == "succeeded"
                and snapshot.main_sha != work.request.workflow_sha
                and await repo.merge_base(work.request.candidate_sha, snapshot.main_sha)
                == work.request.candidate_sha
            ):
                await queue.record_native_landing(work, lease)
                previous = None
                break
        policy = (
            QueuePolicy.model_validate(previous.batch.policy["policy"])
            if previous is not None and previous.batch.policy.get("trust") == trust
            else config.policy
        )
        timeout = await self._timeout(repo, policy)
        assert policy.validation is not None
        if policy.validation.suite == "services":
            actual_digest = await repo.validation_source_digest(snapshot.main_sha)
        else:
            workflow = await repo.raw_file(f".github/workflows/{WORKFLOW}", snapshot.main_sha)
            actual_digest = hashlib.sha256(workflow).hexdigest() if workflow is not None else None
        if actual_digest != config.workflow_digest:
            raise ValueError("Queue workflow changed")
        work = await queue.prepare_native_validation(
            snapshot, timeout, lease, policy=config.policy, trust=trust
        )
        if work is None:
            return
        if work.dispatch:
            if config.sync_workflow_policy:
                await queue.record_evidence(
                    work.attempt.id,
                    "workflow-policy",
                    {
                        "revision": revision,
                        "digest": workflow_digest,
                    },
                    lease,
                )
            await queue.renew(lease, timedelta(seconds=60))
            try:
                # The saved intent authorizes tests, not merging. Do not hold database
                # locks during dispatch; publication below rechecks current authority.
                await dispatcher.dispatch(
                    work.request,
                    work.attempt.deadline,
                    workflow_policy_revision=revision,
                    workflow_digest=workflow_digest,
                )
            except WorkflowPolicyMismatch:
                logger.info(
                    "Queue dispatch deferred until gateway policy agrees",
                    extra={"attempt_id": str(work.attempt.id)},
                )
                await queue.record_evidence(work.attempt.id, "dispatch-policy-deferred", {}, lease)
                return
            except DispatchRejected as exc:
                await queue.finish_native_validation(
                    work, {"error": str(exc)}, lease, succeeded=False
                )
                return
            async with queue.native_publication(lease):
                await dispatcher.report(
                    work.request,
                    work.attempt.deadline,
                    "in_progress",
                    workflow_policy_revision=revision,
                    workflow_digest=workflow_digest,
                )
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
        if await self._timeout(repo, policy) != timeout:
            raise ValueError("Native queue deadline changed during validation")
        await queue.renew(lease, timedelta(seconds=60))
        if await queue.authorize_native_result(work, snapshot, lease):
            async with queue.native_publication(lease):
                await dispatcher.report(
                    work.request,
                    work.attempt.deadline,
                    "success",
                    run_id=run_id,
                    workflow_policy_revision=revision,
                    workflow_digest=workflow_digest,
                )

    async def _recover(
        self,
        queue: MergeQueue,
        repo: RepoClient,
        snapshot: NativeQueueSnapshot,
        lease: Lease,
    ) -> bool:
        decision = await queue.pending_native_recovery(lease)
        if decision is None:
            return False
        if decision.status == "applying":
            # A previous worker may have sent the mutation. Read the outcome; never resend it.
            await queue.settle_native_deferral(decision, snapshot, lease)
            return True
        try:
            if self.open_recovery_repo is None:
                raise ValueError("Native deferral transport is unavailable")
            history = await queue.history(decision.proposal.batch_id)
            if self.config is None or history.batch.policy.get("trust") != self.config.native_trust:
                raise ValueError("Recovery policy changed")
            if history.batch.state != "active" or history.batch.deadline <= datetime.now(UTC):
                raise ValueError("Recovery batch is inactive or expired")
            if decision.proposal.candidate_sha not in snapshot.candidates:
                raise ValueError("Recovery candidate changed")
            policy = QueuePolicy.model_validate(history.batch.policy["policy"])
            if await self._timeout(repo, policy) != history.batch.policy.get("timeout_seconds"):
                raise ValueError("Native queue deadline changed")
            await decision.check_dependencies(repo, snapshot)
            async with self.open_recovery_repo(queue) as writer:
                current = await writer.native_queue(queue.target_branch)
                if current.repository_id != self.repository_id:
                    raise ValueError("Native queue repository identity changed")
                if current != snapshot:
                    raise ValueError(
                        "Queue changed during dependency checks; operator review required"
                    )
                await queue.renew(lease, timedelta(seconds=60))
                await queue.start_native_deferral(decision, current, lease)
                member = decision.proposal.member
                assert member is not None and current.queue is not None
                entry = next(
                    entry for entry in current.queue.entries.nodes if entry.id == member.entry_id
                )
                # Serialize this short mutation with human pause and configuration changes.
                try:
                    async with queue.native_publication(lease):
                        await writer.dequeue_native_pull(entry, str(decision.proposal.request_key))
                except httpx2.HTTPError:
                    logger.warning(
                        "Reconciling ambiguous native dequeue response",
                        extra={"queue_id": str(queue.id)},
                        exc_info=True,
                    )
                after = await writer.native_queue(queue.target_branch)
                if after.repository_id != self.repository_id:
                    raise ValueError("Native queue repository identity changed")
                await queue.settle_native_deferral(decision, after, lease)
        except LeaseLost:
            raise
        except Exception as exc:
            logger.exception(
                "Native deferral requires operator review", extra={"queue_id": str(queue.id)}
            )
            # Save the ambiguity and stop new work. A timeout outside this block leaves the
            # applying intent for the next reconciliation, which also never repeats removal.
            await queue.settle_native_deferral(
                decision, snapshot, lease, error=f"{type(exc).__name__}: {exc}"
            )
        return True

    async def _reconcile_checks(self, queue: MergeQueue, repo: RepoClient, lease: Lease) -> None:
        assert self.dispatcher is not None
        snapshot = await repo.native_queue(queue.target_branch)
        if snapshot.repository_id != self.repository_id:
            raise ValueError("Native queue repository identity changed")
        async with queue.native_check_update(snapshot, lease) as update:
            if update is None:
                return
            envelope = update.envelope
            if envelope.expires_at <= datetime.now(UTC) - timedelta(days=1):
                logger.warning(
                    "Native check cleanup exceeded receipt retention",
                    extra={"attempt_id": str(envelope.contract.attempt_id)},
                )
                return
            await self.dispatcher.report(
                envelope.contract,
                envelope.expires_at,
                update.state,
                workflow_policy_revision=envelope.workflow_policy_revision,
                workflow_digest=envelope.workflow_digest,
            )

    async def reconcile(self, payload: QueueJob) -> None:
        if payload.queue_id != self.queue_id:
            return
        queue = await MergeQueue.load(payload.queue_id, payload.workspace_id)
        if queue is None or queue.mode != "native":
            return
        lease = await queue.claim(uuid7(), timedelta(seconds=60), config=self.config)
        if lease is None:
            return
        try:
            async with asyncio.timeout(45), self.open_repo(queue) as repo:
                snapshot = await repo.native_queue(queue.target_branch)
                if snapshot.repository_id != self.repository_id:
                    raise ValueError("Native queue repository identity changed")
                await queue.record_native_snapshot(snapshot, lease)
                if self.dispatcher is not None:
                    try:
                        if not await self._recover(queue, repo, snapshot, lease):
                            await self._validate(queue, repo, snapshot, lease)
                    finally:
                        try:
                            async with asyncio.timeout(8):
                                await self._reconcile_checks(queue, repo, lease)
                        except LeaseLost:
                            logger.info(
                                "Native check cleanup lost its lease",
                                extra={"queue_id": str(queue.id)},
                            )
                        except Exception:
                            logger.exception(
                                "Native check reconciliation failed",
                                extra={"queue_id": str(queue.id)},
                            )
        finally:
            if self.config is not None and self.config.recovery is not None:
                from openswe.merge_queue.diagnosis import QueueRecovery

                try:
                    async with asyncio.timeout(8):
                        current = await MergeQueue.load(queue.id, queue.workspace_id)
                        if current is not None:
                            recovery = QueueRecovery(self.config, current)
                            await recovery.reconcile(lease)
                except Exception:
                    logger.exception(
                        "Queue recovery delivery deferred", extra={"queue_id": str(queue.id)}
                    )
            try:
                async with asyncio.timeout(10):
                    await queue.prune_history(
                        datetime.now(UTC)
                        - timedelta(days=self.config.retention_days if self.config else 90),
                        lease,
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
