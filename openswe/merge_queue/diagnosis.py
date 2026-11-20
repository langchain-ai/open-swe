"""Deliver queue recovery to its owning Robin thread through durable jobs."""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Final, Self
from uuid import NAMESPACE_URL, UUID, uuid5, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from openswe import event_claims
from openswe.config import ENV
from openswe.database import postgres
from openswe.github.http import GitHubClient
from openswe.github.repositories import Repository
from openswe.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from openswe.jobs import queue as jobs
from openswe.jobs.queue import Job
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.models import Decision, Lease
from openswe.merge_queue.recovery import RecoveryDecision, RecoveryProposal, RecoveryThread
from openswe.merge_queue.store import BatchHistory, LeaseLost, MergeQueue, NativeSelection
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.slack.blocks import escape
from openswe.slack.channels import SlackChannel
from openswe.slack.http import SlackClient
from openswe.slack.records import bind_slack_thread_id, lookup_slack_thread_id
from openswe.slack.thread_owner import wake_thread_owner
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.store import THREADS
from openswe.users.models import User

JOB: Final = "merge_queue_recovery"
logger = logging.getLogger(__name__)


class SlackMessage(BaseModel):
    ts: str
    user: str | None = None
    metadata: dict[str, JsonValue] | None = None


class QueueBusy(ValueError):
    """Another queue operation or recovery wake still holds its bounded claim."""


class QueueRecovery:
    def __init__(self, config: QueueConfiguration, queue: MergeQueue):
        self.config, self.queue = config, queue

    @classmethod
    async def current(cls) -> Self:
        config = await QueueConfiguration.load()
        if config is None or config.mode != "native" or config.recovery is None:
            raise PermissionError("Automatic queue recovery is disabled")
        repository = await Repository.get(config.repository)
        if repository is None:
            raise PermissionError("Recovery repository is unavailable")
        async with postgres.session() as session:
            queue = await session.scalar(
                select(MergeQueue).where(
                    MergeQueue.routing_id == config.routing_id,
                    MergeQueue.repository_id == repository.id,
                    MergeQueue.target_branch == "main",
                    MergeQueue.mode == "native",
                )
            )
        if queue is None:
            raise PermissionError("Native queue is unavailable")
        return cls(config, queue)

    async def operator(self) -> User:
        settings = self.config.recovery
        assert settings is not None
        user = await User.get(settings.operator_id)
        if (
            user is None
            or not user.slack_user_id
            or not user.github_login
            or user.email.lower() not in self.config.operator_emails
        ):
            raise PermissionError("Recovery requires a linked, authorized queue operator")
        channel = await SlackChannel.load(settings.channel_id, use_cache=False)
        if channel is None or not channel.can_listen:
            raise PermissionError("Recovery channel is no longer verified and readable")
        return user

    @retry(
        retry=retry_if_exception_type(QueueBusy),
        stop=stop_after_attempt(4),
        wait=wait_fixed(1),
        reraise=True,
    )
    async def claim(self) -> Lease:
        lease = await self.queue.claim(uuid7(), timedelta(seconds=60), config=self.config)
        if lease is None:
            raise QueueBusy("Queue reconciliation is busy; try this recovery tool again shortly")
        return lease

    @asynccontextmanager
    async def leased(self) -> AsyncIterator[Lease]:
        lease = await self.claim()
        try:
            async with asyncio.timeout(45):
                current = await MergeQueue.load(self.queue.id, self.queue.bound_routing_id)
                if current is None:
                    raise PermissionError("Queue no longer exists")
                self.queue = current
                yield lease
        finally:
            try:
                await self.queue.release(lease)
            except LeaseLost:
                logger.info("Recovery lease was replaced", extra={"queue_id": str(self.queue.id)})

    async def bound(self) -> tuple[BatchHistory, RecoveryThread]:
        cfg = RunConfig.from_runtime()
        if not cfg.thread_id:
            raise PermissionError("Recovery requires an authenticated agent thread")
        history = await self.queue.recovery_for_thread(cfg.thread_id)
        state = RecoveryThread.model_validate(history.batch.policy.get("recovery"))
        if state.thread_id != cfg.thread_id or state.settings != self.config.recovery:
            raise PermissionError("Recovery authorization changed")
        if (
            not state.thread_ts
            or await lookup_slack_thread_id(state.settings.channel_id, state.thread_ts)
            != cfg.thread_id
        ):
            raise PermissionError("Recovery conversation moved or lost its Slack binding")
        operator = await self.operator()
        if cfg.user_id != str(operator.id):
            raise PermissionError("This run is not acting for the configured recovery operator")
        return history, state

    async def fingerprint(self, history: BatchHistory) -> str:
        content = {
            "batch": history.batch.state,
            "attempts": [(str(a.id), a.state) for a in history.attempts],
            "decisions": [d.plan for d in history.decisions],
            "paused": self.queue.paused if history.batch.state == "active" else False,
            "owns_pause": self.queue.recovery_pause_id in {d.id for d in history.decisions},
            "resumed": history.batch.policy.get("recovery_resumes"),
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    async def schedule(
        self,
        batch_id: UUID,
        *,
        minutes: int = 0,
        replace: bool = False,
        investigate: bool = False,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "queue_id": str(self.queue.id),
            "routing_id": str(self.queue.routing_id),
            "batch_id": str(batch_id),
            "investigate": investigate,
        }
        # State-change delivery must not consume an explicitly requested investigation.
        key = f"{JOB}:{batch_id}" + (":followup" if investigate else "")
        if minutes == 0:
            await jobs.coalesce(JOB, payload, key=key)
            return
        await jobs.schedule(
            JOB,
            payload,
            due_at=datetime.now(UTC) + timedelta(minutes=minutes),
            key=key,
            replace=replace,
        )

    async def reconcile(self, lease: Lease) -> None:
        for batch_id in await self.queue.recovery_batches(lease):
            history = await self.queue.history(batch_id)
            saved = history.batch.policy.get("recovery")
            if saved is None:
                await self.schedule(batch_id)
                continue
            state = RecoveryThread.model_validate(saved)
            if state.settings != self.config.recovery:
                continue
            if state.delivered != await self.fingerprint(history):
                await self.schedule(batch_id)
            elif (
                await self.pending_keys(history, state)
                or (history.batch.state != "active" and not state.settled)
                or self.queue.recovery_pause_id in {d.id for d in history.decisions}
                or (
                    history.batch.state == "active"
                    and any(a.state in {"failed", "timed_out"} for a in history.attempts[-1:])
                )
            ):
                await self.schedule(batch_id, minutes=state.followup_minutes)

    async def pending_keys(self, history: BatchHistory, state: RecoveryThread) -> set[str]:
        keys = {
            f"decision:{d.id}:{d.plan.get('status')}"
            for d in history.decisions
            if d.plan.get("kind") == "native-recovery"
            and (saved := RecoveryDecision.model_validate(d.plan)).status
            in {"applied", "escalated", "superseded"}
            and saved.proposal.action != "retry"
        }
        resumed = history.batch.policy.get("recovery_resumes", {})
        if isinstance(resumed, dict):
            keys.update(f"resume:{pause_id}" for pause_id in resumed)
        if history.batch.state != "active":
            keys.add(history.batch.state)
        return keys - state.notices.keys()

    async def post_once(
        self, state: RecoveryThread, event_id: str, text: str, *, oldest: datetime | None = None
    ) -> str:
        # Reconcile an uncertain send before posting again. The stable client id is a
        # second deduplication layer when Slack has not made its history visible yet.
        root = oldest is not None
        assert root or state.thread_ts is not None
        async with SlackClient.bot() as slack:
            assert slack is not None
            cursor = ""
            while True:
                if root:
                    assert oldest is not None
                    page = await slack.conversations_history(
                        channel=state.settings.channel_id,
                        cursor=cursor,
                        limit=200,
                        oldest=str(oldest.timestamp()),
                        include_all_metadata=True,
                    )
                else:
                    assert state.thread_ts is not None
                    page = await slack.conversations_replies(
                        channel=state.settings.channel_id,
                        ts=state.thread_ts,
                        cursor=cursor,
                        limit=200,
                        include_all_metadata=True,
                    )
                for raw in page.get("messages", []):
                    message = SlackMessage.model_validate(raw)
                    payload = (message.metadata or {}).get("event_payload")
                    if (
                        message.user == ENV.SLACK_BOT_USER_ID.get()
                        and isinstance(payload, dict)
                        and payload.get("recovery_event") == event_id
                    ):
                        return message.ts
                cursor = str(page.get("response_metadata", {}).get("next_cursor") or "")
                if not cursor:
                    if page.get("has_more"):
                        raise RuntimeError("Slack history is incomplete; notification not resent")
                    break

            posted = await slack.chat_postMessage(
                channel=state.settings.channel_id,
                text=text,
                thread_ts=None if root else state.thread_ts,
                client_msg_id=str(uuid5(NAMESPACE_URL, event_id)),
                metadata={
                    "event_type": "merge_queue_recovery",
                    "event_payload": {
                        "recovery_event": event_id,
                    },
                },
                unfurl_links=False,
                unfurl_media=False,
            )
            ts = posted.get("ts")
            if not isinstance(ts, str) or not ts:
                raise RuntimeError("Slack did not confirm recovery notification delivery")
            return ts

    async def context(self, history: BatchHistory, state: RecoveryThread) -> dict[str, JsonValue]:
        selection = NativeSelection.model_validate(history.batch.candidate)
        return {
            "repository": self.config.repository,
            "conversation": {"channel_id": state.settings.channel_id, "thread_ts": state.thread_ts},
            "batch_id": str(history.batch.id),
            "state": history.batch.state,
            "terminal_reason": history.batch.candidate_error,
            "base_sha": history.batch.base_sha,
            "candidate": history.batch.candidate,
            "deadline": history.batch.deadline.isoformat(),
            "remaining_attempts": max(0, history.batch.max_attempts - len(history.attempts)),
            "remaining_decisions": max(
                0, history.batch.max_plans - await Decision.budget_used(history.decisions)
            ),
            "members": [entry.model_dump(mode="json") for entry in selection.entries],
            "attempts": [
                {"id": str(a.id), "state": a.state, "deadline": a.deadline.isoformat()}
                for a in history.attempts
            ],
            "evidence": [
                {"attempt_id": str(e.attempt_id), "key": e.key, "reference": e.reference}
                for e in history.evidence
            ],
            "decisions": [d.plan for d in history.decisions],
            "paused": self.queue.paused,
            "pause_id": str(self.queue.recovery_pause_id) if self.queue.recovery_pause_id else None,
            "notifications": {**await self.notices(history, state)},
        }

    async def notices(self, history: BatchHistory, state: RecoveryThread) -> dict[str, str]:
        operator = await self.operator()
        keys = await self.pending_keys(history, state)
        pending: dict[str, str] = {}
        for row in history.decisions:
            if row.plan.get("kind") != "native-recovery":
                continue
            decision = RecoveryDecision.model_validate(row.plan)
            if decision.status not in {"applied", "escalated", "superseded"}:
                continue
            proposal = decision.proposal
            if proposal.action == "retry":
                continue
            key = f"decision:{row.id}:{decision.status}"
            if key not in keys:
                continue
            reason = escape(proposal.reason)
            fix = escape(proposal.suggested_fix)
            prefix = f"Merge queue batch `{history.batch.id}`: "
            if decision.status == "superseded":
                member = proposal.member
                assert member is not None
                text = (
                    f"{prefix}cancelled the planned removal of PR #{member.number} because main "
                    "advanced before removal. Robin did not dequeue it. The current candidate "
                    "must be validated before diagnosing its PRs; the decision budget is unchanged."
                )
            elif decision.status == "escalated" or proposal.action == "escalate":
                next_step = (
                    "Inspect the current GitHub queue membership and resolve the controller error "
                    "before resuming. Removal is unconfirmed."
                    if proposal.action == "defer"
                    else fix
                )
                text = (
                    f"{prefix}<@{operator.slack_user_id}> human action required.\n"
                    f"{reason}\n{escape(decision.detail)}\nNext step: {next_step or reason}"
                )
            elif proposal.action == "pause":
                impact = (
                    "Robin will keep checking and resume its pause when recovery is verified."
                    if self.queue.recovery_pause_id == row.id
                    else "This pause has since been cleared or superseded."
                )
                text = f"{prefix}paused for a shared outage.\n{reason}\n{impact}"
            else:
                member = proposal.member
                assert member is not None
                owner, repo_name = self.config.repository.split("/", 1)
                async with GitHubClient.as_app(
                    owner,
                    repo_name,
                    permissions={
                        "metadata": "read",
                        "pull_requests": "read",
                    },
                ) as github:
                    info = await github.repo(owner, repo_name).pull_request(member.number).pull()
                author = info.get("user")
                login = author.get("login") if isinstance(author, dict) else None
                user = await User.for_login("github", login) if isinstance(login, str) else None
                who = (
                    f"<@{user.slack_user_id}>"
                    if user and user.slack_user_id
                    else f"{escape(str(login or 'PR author'))} (Slack identity unavailable; "
                    f"<@{operator.slack_user_id}> please route this notice)"
                )
                text = (
                    f"{prefix}{who} deferred <https://github.com/{self.config.repository}/pull/"
                    f"{member.number}|#{member.number}> at `{member.head_sha}`.\n"
                    f"{reason}\nSuggested fix: {fix or 'Investigate the linked failure evidence.'}\n"
                    "The PR remains open. After fixing it and passing review/checks, add it back "
                    "to GitHub's merge queue; Robin does not automatically requeue it."
                )
            run_id = next(
                (
                    e.reference.get("run_id")
                    for e in history.evidence
                    if e.attempt_id == proposal.attempt_id and e.key == "validation-run"
                ),
                None,
            )
            if type(run_id) is int:
                text += (
                    f"\nEvidence: https://github.com/{self.config.repository}/actions/runs/{run_id}"
                )
            pending[key] = text
        resumed = history.batch.policy.get("recovery_resumes", {})
        if isinstance(resumed, dict):
            for pause_id, reason in resumed.items():
                key = f"resume:{pause_id}"
                if key in keys:
                    pending[key] = (
                        f"Robin cleared its outage pause: {escape(str(reason))}\n"
                        f"The queue is currently {'paused' if self.queue.paused else 'enabled'}. "
                        "Eligible candidates will receive normal validation. Expired or ejected PRs "
                        "must re-enter the GitHub queue; their deadlines were not extended."
                    )
        if history.batch.state in keys:
            pending[history.batch.state] = (
                f"Merge queue batch `{history.batch.id}` is {history.batch.state}. "
                f"{escape(history.batch.candidate_error or '')}\n"
                "Check GitHub for each PR's current queue position. Ejected PRs are not "
                "automatically requeued."
            )
        return pending

    @classmethod
    async def run(cls, job: Job) -> None:
        config = await QueueConfiguration.load()
        if config is None or config.recovery is None:
            return
        service = await cls.current()
        if str(service.queue.id) != job.payload.get("queue_id") or str(
            service.queue.routing_id
        ) != job.payload.get("routing_id"):
            return
        batch_id = UUID(str(job.payload["batch_id"]))
        scope = f"{JOB}:wake"
        key = f"{service.queue.routing_id}:{service.queue.id}:{batch_id}"
        if not await event_claims.claim(scope, key, ttl=timedelta(minutes=2)):
            raise QueueBusy("A recovery wake is already in progress; the job will retry")
        try:
            # Both delivery job keys share this claim, while agent tools use only the queue lease.
            # End delivery before the claim expires so another worker cannot overlap this wake.
            async with asyncio.timeout(90):
                await service.deliver(job, batch_id)
        finally:
            await event_claims.release(scope, key)

    async def deliver(self, job: Job, batch_id: UUID) -> None:
        settings = self.config.recovery
        assert settings is not None
        operator = await self.operator()
        async with self.leased() as lease:
            state = await self.queue.ensure_recovery_thread(batch_id, settings, lease)
            history = await self.queue.history(batch_id)
            if state.thread_ts is None:
                ts = await self.post_once(
                    state,
                    f"recovery:{batch_id}",
                    f"Robin is investigating merge queue batch `{batch_id}` "
                    f"in `{self.config.repository}`.",
                    oldest=history.batch.created_at,
                )
                state = state.model_copy(update={"thread_ts": ts})
                await self.queue.update_recovery_thread(batch_id, state, lease)
            assert state.thread_ts is not None
            await bind_slack_thread_id(settings.channel_id, state.thread_ts, state.thread_id)
            owner, repo_name = self.config.repository.split("/", 1)
            await THREADS.create(
                state.thread_id,
                title="Merge queue recovery",
                metadata={
                    "source": "slack",
                    "merge_queue_recovery": True,
                    "owner_type": "user",
                    "owner_user_id": str(operator.id),
                    "visibility": "private",
                    "owner_login": operator.github_login,
                    "repo": {"owner": owner, "name": repo_name},
                    GITHUB_TOKEN_REPOSITORIES_KEY: [self.config.repository],
                },
            )
            fingerprint = await self.fingerprint(history)
            pending = await self.pending_keys(history, state)
            paused = self.queue.paused and self.queue.recovery_pause_id in {
                d.id for d in history.decisions
            }
            if history.batch.state != "active" and not paused and not pending:
                await self.queue.update_recovery_thread(
                    batch_id, state.model_copy(update={"settled": True}), lease
                )
                return
            failed = history.batch.state == "active" and any(
                a.state in {"failed", "timed_out"} for a in history.attempts[-1:]
            )
            changed = state.delivered != fingerprint
            requested = job.payload.get("investigate") is True
            if not (changed or paused or failed or pending or requested):
                return
            if not changed and not requested and await jobs.get(f"{JOB}:{batch_id}:followup"):
                # Honor the owner's chosen backoff instead of an earlier automatic timer.
                return
            # Keep a durable follow-up before handing work to the model, including
            # when a previous run is active or a wake response is lost.
            assert job.key is not None
            await jobs.coalesce(
                JOB,
                job.payload,
                key=job.key,
                due_at=datetime.now(UTC) + timedelta(minutes=state.followup_minutes),
            )
            thread = await THREADS.get(state.thread_id)
            latest = (
                await RUNTIMES.runs_on(thread.runtime).latest_run(state.thread_id)
                if thread
                else None
            )
            if latest is not None and latest.active:
                return

        # The agent can immediately call tools that need this queue's lease.
        await wake_thread_owner(
            settings.channel_id,
            state.thread_ts,
            operator.slack_user_id,
            prompt("merge-queue/recovery", batch_id=str(batch_id)),
            wait_for_completion=False,
        )
        async with self.leased() as lease:
            current = await self.queue.ensure_recovery_thread(batch_id, settings, lease)
            if current.thread_id == state.thread_id and current.delivered == state.delivered:
                await self.queue.update_recovery_thread(
                    batch_id, current.model_copy(update={"delivered": fingerprint}), lease
                )

    async def act(self, proposal: RecoveryProposal) -> dict[str, JsonValue]:
        from openswe.merge_queue.controller import QueueController

        async with (
            self.leased() as lease,
            QueueController(self.config).open_repo(self.queue) as repo,
        ):
            history, state = await self.bound()
            if proposal.batch_id != history.batch.id:
                raise PermissionError("Proposal belongs to another recovery sequence")
            if not proposal.reason.strip() or (
                proposal.action in {"defer", "escalate"} and not proposal.suggested_fix.strip()
            ):
                raise ValueError(
                    "Give evidence and, for deferral or escalation, a concrete next step"
                )
            snapshot = await repo.native_queue(self.queue.target_branch)
            if snapshot.repository_id != self.config.repository_id:
                raise PermissionError("Queue repository identity changed")
            decision = await self.queue.decide_native_recovery(
                proposal,
                state.thread_id,
                snapshot,
                lease,
                trust=self.config.native_trust,
            )
        await self.schedule(history.batch.id)
        return decision.model_dump(mode="json")

    async def resume(self, pause_id: UUID, reason: str) -> dict[str, JsonValue]:
        if not reason.strip() or len(reason) > 4000:
            raise ValueError("Give a recovery observation in 1–4000 characters")
        async with self.leased() as lease:
            history, state = await self.bound()
            await self.queue.resume_native_recovery(
                history.batch.id,
                state.thread_id,
                pause_id,
                reason,
                lease,
                trust=self.config.native_trust,
            )
        await self.schedule(history.batch.id)
        return {"resumed_pause_id": str(pause_id)}

    async def follow_up(self, minutes: int) -> dict[str, JsonValue]:
        async with self.leased() as lease:
            history, state = await self.bound()
            owns_pause = self.queue.recovery_pause_id in {d.id for d in history.decisions}
            if not (
                owns_pause
                or history.batch.state == "active"
                or await self.pending_keys(history, state)
            ):
                raise ValueError("This recovery sequence has finished")
            state = RecoveryThread.model_validate(
                {**state.model_dump(), "followup_minutes": minutes}
            )
            await self.queue.update_recovery_thread(history.batch.id, state, lease)
            await self.schedule(
                history.batch.id, minutes=state.followup_minutes, replace=True, investigate=True
            )
        return {"followup_minutes": minutes}

    async def notify(self, event_id: str) -> dict[str, JsonValue]:
        async with self.leased() as lease:
            history, state = await self.bound()
            if ts := state.notices.get(event_id):
                return {"channel_id": state.settings.channel_id, "ts": ts}
            notices = await self.notices(history, state)
            if event_id not in notices:
                raise ValueError("Notification is not pending or its outcome is unconfirmed")
            ts = await self.post_once(state, f"{history.batch.id}:{event_id}", notices[event_id])
            await self.queue.update_recovery_thread(
                history.batch.id,
                state.model_copy(update={"notices": {**state.notices, event_id: ts}}),
                lease,
            )
            return {"channel_id": state.settings.channel_id, "ts": ts}
