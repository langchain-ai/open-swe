"""Recovery delivery reconciles uncertain sends and rejects unbound callers."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import pytest
from pydantic import JsonValue
from tenacity import wait_none

from openswe.merge_queue import diagnosis
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.diagnosis import QueueBusy, QueueRecovery
from openswe.merge_queue.models import Lease
from openswe.merge_queue.recovery import (
    RecoveryDecision,
    RecoveryMember,
    RecoveryProposal,
    RecoverySettings,
    RecoveryThread,
)
from openswe.merge_queue.store import MergeQueue
from openswe.run_config import RunConfig


class SlackDelivery:
    def __init__(self):
        self.messages: list[dict[str, JsonValue]] = []
        self.posts = 0
        self.incomplete = False

    async def conversations_history(self, **kwargs: object) -> dict[str, JsonValue]:
        return {"messages": list(self.messages), "has_more": self.incomplete}

    async def conversations_replies(self, **kwargs: object) -> dict[str, JsonValue]:
        return await self.conversations_history(**kwargs)

    async def chat_postMessage(self, **kwargs: JsonValue) -> dict[str, JsonValue]:
        self.posts += 1
        self.messages.append(
            {
                "ts": "123.456",
                "user": "UROBIN",
                "metadata": kwargs["metadata"],
            }
        )
        raise TimeoutError("Slack accepted the message but the response was lost")


class RecoveryTurn:
    def __init__(self, service, state, history, outcome):
        self.service, self.state, self.history = service, state, history
        self.outcome = outcome
        self.held = False
        self.active = False
        self.wakes = 0
        self.wake_claims: set[tuple[str, str]] = set()
        self.dispatch_started = asyncio.Event()
        self.allow_dispatch = asyncio.Event()

    async def claim_wake(self, scope: str, key: str, *, ttl: timedelta) -> bool:
        if (scope, key) in self.wake_claims:
            return False
        self.wake_claims.add((scope, key))
        return True

    async def release_wake(self, scope: str, key: str) -> None:
        self.wake_claims.remove((scope, key))

    async def latest(self, thread_id: str) -> Mock:
        return Mock(active=self.active)

    async def claim(self, owner, ttl, *, config):
        if self.held:
            return None
        self.held = True
        return Lease(owner=owner, fence=1)

    async def release(self, lease):
        self.held = False

    async def ensure(self, *args):
        return self.state

    async def save(self, batch_id, state, lease):
        assert self.held
        self.state = state

    async def bound(self):
        return self.history, self.state

    async def wake(self, *args, **kwargs):
        self.wakes += 1
        if self.outcome == "overlap":
            self.dispatch_started.set()
            await self.allow_dispatch.wait()
        if self.outcome == "failed":
            raise TimeoutError("Dispatch did not start")
        self.active = True
        await self.service.notify("notice")
        await self.service.follow_up(30)
        if self.outcome == "lost_response":
            raise TimeoutError("Dispatch accepted but response lost")
        return self.state.thread_id


class TestRecoveryDelivery:
    @staticmethod
    async def setup(monkeypatch):
        settings = RecoverySettings(channel_id="C123456", operator_id=uuid7())
        config = Mock(spec=QueueConfiguration, recovery=settings, allow_test_exceptions=False)
        service = QueueRecovery(config, Mock(spec=MergeQueue))
        state = RecoveryThread(settings=settings, thread_id="bound-thread", thread_ts="123.000")
        slack = SlackDelivery()
        connection = AsyncMock()
        connection.__aenter__.return_value = slack
        monkeypatch.setattr(diagnosis.SlackClient, "bot", lambda: connection)
        monkeypatch.setenv("SLACK_BOT_USER_ID", "UROBIN")
        return service, state, slack

    @pytest.mark.parametrize("root", [False, True])
    async def test_lost_slack_response_is_reconciled_without_another_send(self, monkeypatch, root):
        service, state, slack = await self.setup(monkeypatch)
        oldest = datetime(2026, 1, 1, tzinfo=UTC) if root else None
        with pytest.raises(TimeoutError):
            await service.post_once(state, "event-1", "Confirmed outcome", oldest=oldest)
        assert (
            await service.post_once(state, "event-1", "Confirmed outcome", oldest=oldest)
            == "123.456"
        )
        assert slack.posts == 1

    async def test_incomplete_history_does_not_resend(self, monkeypatch):
        service, state, slack = await self.setup(monkeypatch)
        slack.incomplete = True
        with pytest.raises(RuntimeError, match="incomplete"):
            await service.post_once(state, "event-1", "Confirmed outcome")
        assert slack.posts == 0

    async def test_bound_thread_requires_current_operator_and_slack_binding(self, monkeypatch):
        service, state, _ = await self.setup(monkeypatch)
        history = Mock(batch=Mock(policy={"recovery": state.model_dump(mode="json")}))
        service.queue.recovery_for_thread = AsyncMock(return_value=history)
        service.operator = AsyncMock(return_value=Mock(id=state.settings.operator_id))
        monkeypatch.setattr(
            diagnosis, "lookup_slack_thread_id", AsyncMock(return_value=state.thread_id)
        )
        cfg = RunConfig(thread_id=state.thread_id, user_id=str(uuid7()))
        monkeypatch.setattr(RunConfig, "from_runtime", lambda: cfg)
        with pytest.raises(PermissionError, match="configured recovery operator"):
            await service.bound()
        cfg.user_id = str(state.settings.operator_id)
        assert await service.bound() == (history, state)
        monkeypatch.setattr(
            diagnosis, "lookup_slack_thread_id", AsyncMock(return_value="other-thread")
        )
        with pytest.raises(PermissionError, match="Slack binding"):
            await service.bound()
        monkeypatch.setattr(
            diagnosis, "lookup_slack_thread_id", AsyncMock(return_value=state.thread_id)
        )
        service.config.recovery = state.settings.model_copy(update={"channel_id": "C654321"})
        with pytest.raises(PermissionError, match="authorization changed"):
            await service.bound()

    @pytest.mark.parametrize(
        "mapped_author,status", [(False, "applied"), (True, "applied"), (False, "superseded")]
    )
    async def test_notice_waits_for_removal_and_uses_verified_identity(
        self, monkeypatch, mapped_author, status
    ):
        service, state, _ = await self.setup(monkeypatch)
        service.config.repository = "acme/widgets"
        service.operator = AsyncMock(return_value=Mock(slack_user_id="UOPERATOR"))
        proposal = RecoveryProposal(
            request_key=uuid7(),
            batch_id=uuid7(),
            attempt_id=uuid7(),
            candidate_sha="a" * 40,
            action="defer",
            reason="Assertion introduced by this PR. <@UNTRUSTED>",
            suggested_fix="Restore the prior expected behavior.",
            member=RecoveryMember(entry_id="entry-7", number=7, head_sha="b" * 40),
        )
        decision = RecoveryDecision(
            proposal=proposal,
            thread_id=state.thread_id,
            members=(proposal.member,),
            generation=None,
            status="pending",
        )
        row = Mock(id=uuid7(), plan=decision.model_dump(mode="json"))
        history = Mock(
            decisions=[row],
            evidence=[],
            attempts=[],
            batch=Mock(
                id=proposal.batch_id,
                state="active",
                policy={},
                candidate={"entries": []},
                deadline=datetime.now(UTC),
                max_plans=1,
                max_attempts=3,
            ),
        )
        assert await service.notices(history, state) == {}
        assert (await service.context(history, state))["remaining_decisions"] == 0
        row.plan = decision.model_copy(update={"status": status}).model_dump(mode="json")
        if status == "superseded":
            context = await service.context(history, state)
            assert context["remaining_decisions"] == 1
            key = f"decision:{row.id}:superseded"
            assert "did not dequeue" in (await service.notices(history, state))[key]
            state = state.model_copy(update={"notices": {key: "123.456"}})
            assert await service.notices(history, state) == {}
            return
        connection = AsyncMock()
        github = connection.__aenter__.return_value
        github.repo = Mock(
            return_value=Mock(
                pull_request=Mock(
                    return_value=Mock(
                        pull=AsyncMock(return_value={"user": {"login": "developer"}}),
                    )
                )
            )
        )
        monkeypatch.setattr(diagnosis.GitHubClient, "as_app", lambda *args, **kwargs: connection)
        monkeypatch.setattr(
            diagnosis.User,
            "for_login",
            AsyncMock(
                return_value=Mock(slack_user_id="UAUTHOR") if mapped_author else None,
            ),
        )
        notices = await service.notices(history, state)
        message = notices[f"decision:{row.id}:applied"]
        assert ("<@UAUTHOR>" if mapped_author else "<@UOPERATOR>") in message
        assert "<@UNTRUSTED>" not in message
        assert "github.com/acme/widgets/pull/7" in message
        assert "The PR remains open" in message
        assert proposal.suggested_fix in message

    async def test_exception_notice_waits_for_publication_and_tags_owner(self, monkeypatch):
        service, state, _ = await self.setup(monkeypatch)
        service.operator = AsyncMock(return_value=Mock(slack_user_id="UOPERATOR"))
        monkeypatch.setattr(
            diagnosis.User, "for_login", AsyncMock(return_value=Mock(slack_user_id="UAUTHOR"))
        )
        proposal = RecoveryProposal(
            request_key=uuid7(),
            batch_id=uuid7(),
            attempt_id=uuid7(),
            candidate_sha="a" * 40,
            action="except_tests",
            tests=("cloud-tests/example.spec.ts: loads",),
            reason="Obsolete test assertion; the documented API confirms the intended behavior.",
            suggested_fix="Use the documented replacement field.",
            responsible_login="developer",
        )
        decision = RecoveryDecision(
            proposal=proposal,
            thread_id=state.thread_id,
            members=(),
            generation=None,
            status="applied",
        )
        row = Mock(id=uuid7(), plan=decision.model_dump(mode="json"))
        history = Mock(
            decisions=[row],
            evidence=[],
            batch=Mock(id=proposal.batch_id, state="active", policy={}),
        )
        assert await service.notices(history, state) == {}
        history.evidence = [Mock(attempt_id=proposal.attempt_id, key="test-exception-published")]
        key = f"decision:{row.id}:applied"
        message = (await service.notices(history, state))[key]
        assert "<@UAUTHOR>" in message and "allowed the batch to merge" in message
        assert proposal.tests[0] in message and proposal.suggested_fix in message
        assert proposal.reason in message and "workflow remains red" in message
        state = state.model_copy(update={"notices": {key: "123.456"}})
        assert await service.notices(history, state) == {}

    @pytest.mark.parametrize(
        "requested,busy,paused,outcome",
        [
            (False, False, False, "ok"),
            (True, False, False, "ok"),
            (True, True, False, "ok"),
            (False, False, True, "ok"),
            (True, False, False, "failed"),
            (True, False, False, "lost_response"),
            (False, False, False, "overlap"),
        ],
    )
    async def test_followup_during_unchanged_replacement_validation(
        self, monkeypatch, requested, busy, paused, outcome
    ):
        service, state, _ = await self.setup(monkeypatch)
        service.config.repository = "acme/widgets"
        service.queue.id, service.queue.routing_id = uuid7(), uuid7()
        pause_id = uuid7()
        service.queue.paused = paused
        service.queue.recovery_pause_id = pause_id if paused else None
        history = Mock(
            batch=Mock(id=uuid7(), state="active", policy={}),
            attempts=[Mock(id=uuid7(), state="failed"), Mock(id=uuid7(), state="running")],
            decisions=[Mock(id=pause_id, plan={})] if paused else [],
        )
        state = state.model_copy(
            update={
                "delivered": "previous"
                if (requested and not busy) or outcome == "overlap"
                else await service.fingerprint(history)
            }
        )
        turn = RecoveryTurn(service, state, history, outcome)
        turn.active = busy
        monkeypatch.setattr(diagnosis.event_claims, "claim", turn.claim_wake)
        monkeypatch.setattr(diagnosis.event_claims, "release", turn.release_wake)
        service.queue.ensure_recovery_thread = turn.ensure
        service.queue.history = AsyncMock(return_value=history)
        service.queue.update_recovery_thread = turn.save
        service.queue.claim, service.queue.release = turn.claim, turn.release
        service.bound = turn.bound
        service.notices = AsyncMock(return_value={"notice": "Confirmed result"})
        service.post_once = AsyncMock(return_value="123.789")
        monkeypatch.setattr(QueueRecovery.claim.retry, "wait", wait_none())
        monkeypatch.setattr(MergeQueue, "load", AsyncMock(return_value=service.queue))
        coalesce = AsyncMock()
        monkeypatch.setattr(diagnosis.jobs, "coalesce", coalesce)
        service.operator = AsyncMock(
            return_value=Mock(id=state.settings.operator_id, slack_user_id="UOPERATOR")
        )
        service.schedule = AsyncMock()
        monkeypatch.setattr(
            diagnosis.jobs, "get", AsyncMock(return_value=Mock() if paused else None)
        )
        monkeypatch.setattr(QueueConfiguration, "load", AsyncMock(return_value=service.config))
        monkeypatch.setattr(QueueRecovery, "current", AsyncMock(return_value=service))
        monkeypatch.setattr(diagnosis, "bind_slack_thread_id", AsyncMock())
        monkeypatch.setattr(
            diagnosis, "THREADS", Mock(create=AsyncMock(), get=AsyncMock(return_value=Mock()))
        )
        monkeypatch.setattr(
            diagnosis,
            "RUNTIMES",
            Mock(runs_on=Mock(return_value=Mock(latest_run=turn.latest))),
        )
        monkeypatch.setattr(diagnosis, "wake_thread_owner", turn.wake)
        job = Mock(
            key=f"{diagnosis.JOB}:{history.batch.id}" + (":followup" if requested else ""),
            payload={
                "queue_id": str(service.queue.id),
                "routing_id": str(service.queue.routing_id),
                "batch_id": str(history.batch.id),
                "investigate": requested,
            },
        )
        if outcome == "overlap":
            first = asyncio.create_task(QueueRecovery.run(job))
            followup = Mock(
                key=f"{diagnosis.JOB}:{history.batch.id}:followup",
                payload={**job.payload, "investigate": True},
            )
            try:
                async with asyncio.timeout(2):
                    await turn.dispatch_started.wait()
                    assert not turn.held
                    with pytest.raises(QueueBusy, match="wake is already in progress"):
                        await QueueRecovery.run(followup)
                    assert turn.wakes == 1
                    turn.allow_dispatch.set()
                    await first
                    await QueueRecovery.run(followup)
                    assert turn.wakes == 1
            finally:
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
        elif outcome == "ok":
            await QueueRecovery.run(job)
        else:
            with pytest.raises(TimeoutError):
                await QueueRecovery.run(job)
        assert not turn.held
        assert not turn.wake_claims
        if (requested and not busy) or outcome == "overlap":
            assert turn.wakes == 1
            if outcome != "failed":
                assert turn.active
                assert turn.state.notices == {"notice": "123.789"}
                assert turn.state.followup_minutes == 30
            if outcome in {"ok", "overlap"}:
                assert turn.state.delivered == await service.fingerprint(history)
            else:
                assert turn.state.delivered == state.delivered
        else:
            assert turn.wakes == 0
        if outcome == "overlap":
            assert coalesce.await_count == 2
            assert {call.kwargs["key"] for call in coalesce.await_args_list} == {
                job.key,
                followup.key,
            }
        elif requested:
            coalesce.assert_awaited_once()
            assert coalesce.call_args.kwargs["key"] == job.key
            assert coalesce.call_args.args[1] == job.payload
        else:
            coalesce.assert_not_awaited()
        if outcome == "lost_response":
            await QueueRecovery.run(job)
            assert turn.wakes == 1
            service.post_once.assert_awaited_once()
        elif outcome == "failed":
            turn.outcome = "ok"
            await QueueRecovery.run(job)
            assert turn.wakes == 2
            assert turn.state.notices == {"notice": "123.789"}

    async def test_brief_contention_retries_before_running_a_tool(self, monkeypatch):
        service, _, _ = await self.setup(monkeypatch)
        lease = Lease(owner=uuid7(), fence=1)
        service.queue.claim = AsyncMock(side_effect=[None, lease])
        service.queue.release = AsyncMock()
        monkeypatch.setattr(QueueRecovery.claim.retry, "wait", wait_none())
        monkeypatch.setattr(MergeQueue, "load", AsyncMock(return_value=service.queue))
        async with service.leased() as claimed:
            assert claimed == lease
        service.queue.release.assert_awaited_once_with(lease)
