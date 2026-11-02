"""Explicitly invoked candidate preparation; no workers, CI dispatch or promotion."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta

from openswe.github.http import RepoClient
from openswe.merge_queue.admission import (
    ADMISSION_MAX_AGE,
    AdmissionReader,
    AdmissionRejected,
    QueuePolicy,
)
from openswe.merge_queue.git import Candidate, CandidateConflict, PullHead, candidate_ref
from openswe.merge_queue.models import Batch, Lease
from openswe.merge_queue.store import LeaseLost, MergeQueue, QueueEntry

logger = logging.getLogger(__name__)


class BatchScheduler:
    def __init__(self, queue: MergeQueue, repo: RepoClient, policy: QueuePolicy):
        if queue.mode != "custom":
            raise ValueError("Custom scheduling is disabled for native queues")
        self.queue, self.repo, self.policy = queue, repo, policy
        self.admission = AdmissionReader(repo, queue.target_branch, policy)

    async def reconcile(self, lease: Lease, *, seconds: float = 240) -> None:
        if not 0 < seconds <= 600:
            raise ValueError("Invalid reconciliation limit")

        async def work() -> None:
            admission_deadline = asyncio.get_running_loop().time() + min(
                seconds / 2, ADMISSION_MAX_AGE.total_seconds() / 2
            )
            entries = await self.queue.entries(lease)
            entries.sort(
                key=lambda entry: (
                    entry.request.state != "batched",
                    entry.request.last_checked_at or datetime.min.replace(tzinfo=UTC),
                    entry.request.created_at,
                )
            )
            for entry in entries[:100]:
                remaining = admission_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(remaining):
                        await self._refresh(entry, lease)
                except TimeoutError:
                    logger.info(
                        "Admission reconciliation reached its time limit",
                        extra={"queue_id": str(self.queue.id)},
                    )
                    break
            await self._schedule(lease)

        await self._run_with_lease(lease, work, seconds=seconds)

    async def _run_with_lease(
        self,
        lease: Lease,
        work: Callable[[], Coroutine[object, object, None]],
        *,
        seconds: float,
    ) -> None:
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(20)
                await self.queue.renew(lease, timedelta(seconds=60))

        async with asyncio.timeout(seconds):
            if (await self.queue.repository_name(lease)).lower() != self.repo.full_name.lower():
                raise ValueError("GitHub client does not belong to this queue")
            await self.queue.renew(lease, timedelta(seconds=60))
            renewal = asyncio.create_task(heartbeat())
            operation = asyncio.create_task(work())
            try:
                await asyncio.wait({renewal, operation}, return_when=asyncio.FIRST_COMPLETED)
                if renewal.done():
                    await renewal
                await operation
            finally:
                renewal.cancel()
                operation.cancel()
                await asyncio.gather(renewal, operation, return_exceptions=True)

    async def _refresh(self, entry: QueueEntry, lease: Lease) -> None:
        request, head = entry.request, entry.request.head_sha
        try:
            current = await self.repo.pull_request(entry.number).queue_state()
            if current.head != head:
                await self.queue.refresh_head(request.id, current.head, lease)
                head = current.head
            if current.merged or current.state != "OPEN":
                raise AdmissionRejected(
                    "PR is no longer open", terminal=True, merged=current.merged
                )
            admission = await self.admission.check(entry.number, head, tuple(request.dependencies))
        except LeaseLost:
            raise
        except AdmissionRejected as exc:
            await self.queue.refresh_admission(
                request.id,
                head,
                None,
                lease,
                reason=str(exc),
                terminal=exc.terminal,
                merged=exc.merged,
            )
        except Exception:
            logger.warning(
                "Queue admission unavailable",
                extra={"queue_id": str(self.queue.id), "pr_number": entry.number},
                exc_info=True,
            )
            await self.queue.refresh_admission(
                request.id,
                head,
                None,
                lease,
                reason="GitHub admission unavailable; awaiting revalidation",
            )
        else:
            await self.queue.refresh_admission(request.id, head, admission, lease)

    async def _current(self, batch: Batch, lease: Lease) -> bool:
        if await self.repo.branch_sha(self.queue.target_branch) != batch.base_sha:
            await self.queue.invalidate(batch.id, lease)
            return False
        history = await self.queue.history(batch.id)
        entries = {entry.request.id: entry for entry in await self.queue.entries(lease)}
        for member in history.members:
            entry = entries.get(member.request_id)
            if entry is None or entry.request.head_sha != member.head_sha:
                return False
            await self._refresh(entry, lease)
        try:
            await self.queue.candidate_inputs(batch.id, lease)
        except LeaseLost:
            raise
        except ValueError:
            logger.info("Candidate no longer ready", extra={"batch_id": str(batch.id)})
            return False
        return True

    async def _schedule(self, lease: Lease) -> None:
        base = await self.repo.branch_sha(self.queue.target_branch)
        batch = await self.queue.schedule(base, self.policy, lease)
        if batch is None or not await self._current(batch, lease):
            return
        if batch.candidate is not None:
            candidate = Candidate.model_validate(batch.candidate)
            remote = await self.repo.ref_sha(candidate_ref(batch.id))
            if remote == candidate.sha:
                await self.queue.save_candidate(batch.id, candidate, lease, published=True)
                return
            if remote is not None or batch.published_at is not None:
                await self.queue.candidate_failed(
                    batch.id, "Published candidate ref changed or disappeared", lease
                )
                return
        if not await self.queue.begin_build(batch.id, lease):
            return
        async with self.repo.candidate_git() as git:
            batch, entries = await self.queue.candidate_inputs(batch.id, lease)
            remaining = (batch.deadline - datetime.now(UTC)).total_seconds()
            async with asyncio.timeout(remaining):
                try:
                    candidate = await git.build(
                        self.queue.target_branch,
                        batch.base_sha,
                        tuple(
                            PullHead(number=entry.number, sha=entry.request.head_sha)
                            for entry in entries
                        ),
                        timestamp=int(batch.created_at.timestamp()),
                    )
                except CandidateConflict as exc:
                    await self.queue.candidate_failed(
                        batch.id, str(exc), lease, conflicting_pr=exc.number
                    )
                    return
                if not await self._current(batch, lease):
                    return
                await self.queue.save_candidate(batch.id, candidate, lease)
                await git.publish(candidate, batch.id)
                if await self._current(batch, lease):
                    await self.queue.save_candidate(batch.id, candidate, lease, published=True)

    async def cleanup(self, lease: Lease, *, retention_days: int = 90, seconds: float = 60) -> None:
        """Bound ref deletion separately from sandbox setup, renewing the lease throughout."""
        if not 7 <= retention_days <= 365 or not 0 < seconds <= 180:
            raise ValueError("Invalid cleanup or retention limit")

        async def work() -> None:
            cutoff = datetime.now(UTC) - timedelta(days=retention_days)
            batches = await self.queue.expired_candidates(cutoff, lease)
            if not batches:
                return
            async with self.repo.candidate_git() as git:
                async with asyncio.timeout(seconds):
                    for batch in batches:
                        try:
                            candidate = Candidate.model_validate(batch.candidate)
                            await git.delete(batch.id, candidate.sha)
                            await self.queue.candidate_deleted(batch.id, candidate.sha, lease)
                        except LeaseLost:
                            raise
                        except Exception:
                            logger.warning(
                                "Candidate ref cleanup failed",
                                extra={"batch_id": str(batch.id)},
                                exc_info=True,
                            )

        try:
            # Sandbox startup and teardown get three minutes beyond the deletion budget.
            await self._run_with_lease(lease, work, seconds=180 + seconds)
        except LeaseLost:
            raise
        except Exception:
            logger.warning(
                "Candidate cleanup pass failed",
                extra={"queue_id": str(self.queue.id)},
                exc_info=True,
            )
