"""Recovery delivery reconciles uncertain sends and rejects unbound callers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import pytest
from pydantic import JsonValue

from openswe.merge_queue import diagnosis
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.diagnosis import QueueRecovery
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


class TestRecoveryDelivery:
    @staticmethod
    async def setup(monkeypatch):
        settings = RecoverySettings(channel_id="C123456", operator_id=uuid7())
        config = Mock(spec=QueueConfiguration, recovery=settings)
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

    @pytest.mark.parametrize("mapped_author", [False, True])
    async def test_notice_waits_for_removal_and_uses_verified_identity(
        self, monkeypatch, mapped_author
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
            batch=Mock(id=proposal.batch_id, state="active", policy={}),
        )
        assert await service.notices(history, state) == {}
        row.plan = decision.model_copy(update={"status": "applied"}).model_dump(mode="json")
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

    @pytest.mark.parametrize(
        "requested,busy,paused",
        [(False, False, False), (True, False, False), (True, True, False), (False, False, True)],
    )
    async def test_followup_during_unchanged_replacement_validation(
        self, monkeypatch, requested, busy, paused
    ):
        service, state, _ = await self.setup(monkeypatch)
        service.config.repository = "acme/widgets"
        service.queue.id, service.queue.workspace_id = uuid7(), uuid7()
        pause_id = uuid7()
        service.queue.paused = paused
        service.queue.recovery_pause_id = pause_id if paused else None
        history = Mock(
            batch=Mock(id=uuid7(), state="active", policy={}),
            attempts=[Mock(id=uuid7(), state="failed"), Mock(id=uuid7(), state="running")],
            decisions=[Mock(id=pause_id, plan={})] if paused else [],
        )
        state = state.model_copy(update={"delivered": await service.fingerprint(history)})
        service.queue.ensure_recovery_thread = AsyncMock(return_value=state)
        service.queue.history = AsyncMock(return_value=history)
        service.queue.update_recovery_thread = AsyncMock()
        service.leased = Mock(return_value=AsyncMock())
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
            Mock(
                runs_on=Mock(
                    return_value=Mock(latest_run=AsyncMock(return_value=Mock(active=busy)))
                )
            ),
        )
        wake = AsyncMock()
        monkeypatch.setattr(diagnosis, "wake_thread_owner", wake)
        job = Mock(
            payload={
                "queue_id": str(service.queue.id),
                "workspace_id": str(service.queue.workspace_id),
                "batch_id": str(history.batch.id),
                "investigate": requested,
            }
        )
        await QueueRecovery.run(job)
        if requested and not busy:
            wake.assert_awaited_once()
        else:
            wake.assert_not_awaited()
        if requested and busy:
            service.schedule.assert_any_await(
                history.batch.id,
                minutes=state.followup_minutes,
                replace=True,
                investigate=True,
            )
        elif not requested:
            service.schedule.assert_not_awaited()
