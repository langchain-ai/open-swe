"""Validate selected GitHub candidates; GitHub alone constructs and lands them."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import httpx2
from pydantic import BaseModel, Field

from openswe.github.http import RepoClient
from openswe.github.queue import NativeQueueSnapshot
from openswe.jobs import queue as jobs
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.dispatch import (
    CandidateAcceptance,
    ConflictRemoval,
    ConflictRemovalResult,
    DispatchEnvelope,
    DispatchGateway,
    DispatchRejected,
    WorkflowPolicyMismatch,
)
from openswe.merge_queue.models import Batch, Lease
from openswe.merge_queue.runtime import JOB_KIND, QueueJob
from openswe.merge_queue.store import LeaseLost, MergeQueue, NativeSelection
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
        config: QueueConfiguration | None = None,
        dispatcher: DispatchGateway | None = None,
    ):
        self.queue_id, self.repository_id, self.open_repo = queue_id, repository_id, open_repo
        self.config, self.dispatcher = config, dispatcher

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
            if work.attempt.state == "succeeded" and snapshot.main_sha != work.request.workflow_sha:
                landed = False
                for candidate_sha in (*work.accepted_candidates, work.request.candidate_sha):
                    if await repo.merge_base(candidate_sha, snapshot.main_sha) == candidate_sha:
                        await queue.record_native_landing(work, lease)
                        previous, landed = None, True
                        break
                if landed:
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
        if config.workflow_digest is not None and actual_digest != config.workflow_digest:
            raise ValueError("Queue workflow changed")
        main_advance = (
            await self._validation_advance_from(repo, snapshot, previous.batch)
            if previous
            else None
        )
        work = await queue.prepare_native_validation(
            snapshot,
            timeout,
            lease,
            policy=config.policy,
            trust=trust,
            allow_main_advance=config.allow_main_advance,
            main_advance_from=main_advance,
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
                    members=work.members,
                )
            except WorkflowPolicyMismatch:
                logger.info(
                    "Queue dispatch deferred until gateway policy agrees",
                    extra={"attempt_id": str(work.attempt.id)},
                )
                await queue.record_evidence(work.attempt.id, "dispatch-policy-deferred", {}, lease)
                return
            except DispatchRejected as exc:
                await queue.record_evidence(
                    work.attempt.id, "dispatch-rejected", {"status": exc.status}, lease
                )
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
                    members=work.members,
                )
            return
        await self._reconcile_checks(queue, repo, lease, attempt_id=work.attempt.id)
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
        main_advance = await self._validation_advance_from(repo, snapshot, work.batch)
        if await queue.authorize_native_result(
            work, snapshot, lease, main_advance_from=main_advance
        ):
            candidate = NativeSelection.model_validate(work.batch.candidate).candidate(snapshot)
            assert candidate is not None
            acceptance = (
                CandidateAcceptance(base_sha=snapshot.main_sha, candidate_sha=candidate.head_sha)
                if candidate.head_sha != work.request.candidate_sha
                else None
            )
            async with queue.native_publication(lease, timeout_seconds=30):
                await dispatcher.report(
                    work.request,
                    work.attempt.deadline,
                    "success",
                    run_id=run_id,
                    workflow_policy_revision=revision,
                    workflow_digest=workflow_digest,
                    members=work.members,
                    acceptance=acceptance,
                    test_exception=work.test_exception,
                )

            if work.test_exception is not None:
                await queue.record_evidence(work.attempt.id, "test-exception-published", {}, lease)

    async def _main_advance_from(
        self, repo: RepoClient, snapshot: NativeQueueSnapshot, batch: Batch
    ) -> str | None:
        if self.config is None or not self.config.allow_main_advance:
            return None
        observed = batch.policy.get("observed_main_sha", batch.base_sha)
        if not isinstance(observed, str):
            raise ValueError("Invalid observed main revision")
        for base in {batch.base_sha, observed}:
            if base != snapshot.main_sha and await repo.merge_base(base, snapshot.main_sha) != base:
                return None
        return batch.base_sha

    async def _validation_advance_from(
        self, repo: RepoClient, snapshot: NativeQueueSnapshot, batch: Batch
    ) -> str | None:
        base = await self._main_advance_from(repo, snapshot, batch)
        if base is None:
            return None
        assert self.config is not None
        if self.config.workflow_digest is None and base != snapshot.main_sha:
            if await repo.validation_source_digest(base) != await repo.validation_source_digest(
                snapshot.main_sha
            ):
                return None
        return base

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
        try:
            history = await queue.history(decision.proposal.batch_id)
            main_advance = await self._main_advance_from(repo, snapshot, history.batch)
            if (
                main_advance is not None
                and snapshot.main_sha != history.batch.base_sha
                and decision.status == "pending"
                and history.batch.policy.get("allow_main_advance") is True
            ):
                await queue.settle_native_deferral(decision, snapshot, lease, supersede=True)
                return False
            if decision.status == "applying":
                # A previous worker may have sent the mutation. Read the outcome; never resend it.
                await queue.settle_native_deferral(
                    decision, snapshot, lease, main_advance_from=main_advance
                )
                return True
            if self.dispatcher is None:
                raise ValueError("Native deferral transport is unavailable")
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
            attempt = next(
                attempt
                for attempt in history.attempts
                if attempt.id == decision.proposal.attempt_id
            )
            references = {
                evidence.key: evidence.reference
                for evidence in history.evidence
                if evidence.attempt_id == attempt.id
            }
            workflow_policy = references.get("workflow-policy", {})
            envelope = DispatchEnvelope.model_validate(
                {
                    "contract": references["native-request"],
                    "expires_at": attempt.deadline,
                    "workflow_policy_revision": workflow_policy.get("revision"),
                    "workflow_digest": workflow_policy.get("digest"),
                    "members": references.get("native-members", {}).get("members", []),
                }
            )
            current = await repo.native_queue(queue.target_branch)
            exclude = {"queue": {"entries": {"nodes": {"__all__": {"pull": {"mergeable"}}}}}}
            if current.model_dump(exclude=exclude) != snapshot.model_dump(exclude=exclude):
                raise ValueError("Queue changed during dependency checks; operator review required")
            await queue.renew(lease, timedelta(seconds=60))
            await queue.start_native_deferral(decision, current, lease)
            member = decision.proposal.member
            assert member is not None
            try:
                async with queue.native_publication(
                    lease,
                    timeout_seconds=30 if decision.proposal.additional_members else 5,
                ):
                    await self.dispatcher.defer(
                        envelope,
                        decision.proposal.request_key,
                        member,
                        additional_members=decision.proposal.additional_members,
                        expires_at=history.batch.deadline,
                    )
            except (httpx2.HTTPError, TimeoutError) as exc:
                if isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                logger.warning(
                    "Reconciling ambiguous dispatch gateway deferral response",
                    extra={"queue_id": str(queue.id)},
                    exc_info=True,
                )
            after = await repo.native_queue(queue.target_branch)
            if after.repository_id != self.repository_id:
                raise ValueError("Native queue repository identity changed")
            await queue.settle_native_deferral(
                decision,
                after,
                lease,
                main_advance_from=await self._main_advance_from(repo, after, history.batch),
            )
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

    async def _reconcile_checks(
        self, queue: MergeQueue, repo: RepoClient, lease: Lease, *, attempt_id: UUID | None = None
    ) -> None:
        assert self.dispatcher is not None
        snapshot = await repo.native_queue(queue.target_branch)
        if snapshot.repository_id != self.repository_id:
            raise ValueError("Native queue repository identity changed")
        history = await queue.native_validation_history(lease)
        main_advance = (
            await self._validation_advance_from(repo, snapshot, history[0].batch)
            if history
            else None
        )
        async with queue.native_check_update(
            snapshot, lease, main_advance_from=main_advance, attempt_id=attempt_id
        ) as update:
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
                members=envelope.members,
                acceptance=update.acceptance,
            )

    async def _schedule_conflict(
        self, queue: MergeQueue, snapshot: NativeQueueSnapshot, lease: Lease
    ) -> None:
        if snapshot.queue is None or queue.paused:
            return
        entry = next(
            (
                entry
                for entry in sorted(snapshot.queue.entries.nodes, key=lambda entry: entry.position)
                if entry.pull.mergeable == "CONFLICTING"
                and entry.pull.state == "OPEN"
                and entry.pull.branch == queue.target_branch
                and (entry.pull.repository or "").lower() == snapshot.repository.lower()
            ),
            None,
        )
        if entry is None:
            return
        request = ConflictRemoval(
            repository_id=snapshot.repository_id,
            entry_id=entry.id,
            number=entry.pull.number,
            head_sha=entry.pull.head_sha,
            main_sha=snapshot.main_sha,
            enqueued_at=entry.enqueued_at,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        async with queue.native_publication(lease):
            await jobs.schedule(
                JOB_KIND,
                QueueJob(
                    queue_id=queue.id, routing_id=queue.bound_routing_id, conflict=request
                ).model_dump(mode="json"),
                key=f"merge-queue-conflict:{queue.id}:{entry.id}:{entry.pull.head_sha}:{snapshot.main_sha}",
                replace=False,
            )

    async def reconcile(self, payload: QueueJob) -> None:
        if payload.queue_id != self.queue_id:
            return
        queue = await MergeQueue.load(payload.queue_id, payload.routing_id)
        if queue is None or queue.mode != "native":
            return
        lease = await queue.claim(uuid7(), timedelta(seconds=60), config=self.config)
        if lease is None:
            if payload.conflict is not None:
                raise LeaseLost("Conflict removal is waiting for the queue lease")
            return
        result: ConflictRemovalResult | None = None
        recovery = None
        try:
            async with asyncio.timeout(45), self.open_repo(queue) as repo:
                snapshot = await repo.native_queue(queue.target_branch)
                if snapshot.repository_id != self.repository_id:
                    raise ValueError("Native queue repository identity changed")
                await queue.record_native_snapshot(snapshot, lease)
                if self.dispatcher is not None:
                    if payload.conflict is not None:
                        request = payload.conflict.model_copy(
                            update={
                                "receipt_only": queue.paused
                                or payload.conflict.expires_at <= datetime.now(UTC)
                            }
                        )
                        async with (
                            nullcontext()
                            if request.receipt_only
                            else queue.native_publication(lease, timeout_seconds=30)
                        ):
                            result = await self.dispatcher.remove_conflict(request)
                        return
                    try:
                        await self._schedule_conflict(queue, snapshot, lease)
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
                        current = await MergeQueue.load(queue.id, queue.bound_routing_id)
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

            if (
                payload.conflict is not None
                and result is not None
                and (result.removed is not None or result.uncertain or result.forbidden)
                and recovery is not None
            ):
                async with asyncio.timeout(90):
                    await recovery.notify_conflict(payload.conflict, result)
