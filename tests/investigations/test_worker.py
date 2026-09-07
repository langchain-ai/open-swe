import time
from unittest.mock import AsyncMock

import pytest

from agent.investigations import service, worker
from agent.investigations.models import (
    Evidence,
    Investigation,
    InvestigationMessage,
    InvestigationPolicy,
    InvestigationReport,
    Receipt,
)


@pytest.fixture
async def incident(fake_store, monkeypatch):
    policy = InvestigationPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")
    await service.POLICIES.put("default", policy)
    record = Investigation(
        id="I1",
        workspace_id="T1",
        channel_id="C1",
        thread_id="I1",
        watch_started_at=time.time(),
        last_source_activity_at=time.time(),
    )
    await service.INVESTIGATIONS.put(record.id, record)
    info = {
        "id": "C1",
        "name": "inc-api",
        "is_channel": True,
        "is_private": False,
        "is_im": False,
        "is_mpim": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
        "is_member": True,
        "is_archived": False,
    }
    monkeypatch.setattr(worker.slack, "channel_info", AsyncMock(return_value=info))
    monkeypatch.setattr(
        worker.slack,
        "history",
        AsyncMock(
            return_value=(
                [
                    InvestigationMessage(
                        id="200.1",
                        ts="200.1",
                        text="Checkout requests fail with HTTP 500 after deployment.",
                    )
                ],
                [],
            )
        ),
    )
    monkeypatch.setattr(
        worker,
        "run_engine",
        AsyncMock(return_value=InvestigationReport(summary="Cause remains unknown.")),
    )
    monkeypatch.setattr(worker, "schedule_wake", AsyncMock())
    monkeypatch.setattr(service, "wake", AsyncMock())
    monkeypatch.setattr(worker.slack, "publish", AsyncMock(return_value="100.0"))
    return record


async def _request(key: str, text: str = "", action: str = "ask") -> None:
    await service.RECEIPTS.put(
        key,
        Receipt(
            id=key,
            workspace_id="T1",
            channel_id="C1",
            kind="command",
            payload={"action": action, "text": text},
            received_at=time.time(),
        ),
    )


@pytest.mark.parametrize(
    "newer,older,expected",
    [
        ("pause", "resume", "paused"),
        ("complete", "reopen", "completed"),
        ("resume", "pause", "watching"),
    ],
)
async def test_delayed_older_slack_control_cannot_override_newer(
    incident, monkeypatch, newer, older, expected
):
    incident.status = "paused"
    await service.INVESTIGATIONS.put("I1", incident)
    monkeypatch.setattr(
        worker.slack,
        "request",
        AsyncMock(
            return_value={
                "user": {"profile": {"email": "responder@example.com"}},
            }
        ),
    )
    monkeypatch.setattr(worker, "is_observability_authorized", lambda _: True)
    for key, action, source in [("newer", newer, 200), ("older", older, 100)]:
        await service.RECEIPTS.put(
            key,
            Receipt(
                id=key,
                workspace_id="T1",
                channel_id="C1",
                kind="app_mention",
                payload={"text": f"<@UBOT> {action}", "user": "U1", "ts": str(source)},
                received_at=time.time(),
                source_time=source,
            ),
        )
        await worker.process_channel("I1")
    assert (await service.INVESTIGATIONS.get("I1")).status == expected


async def test_real_engine_model_outage_keeps_question_for_retry(incident, monkeypatch):
    from agent.investigations import engine

    model = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr(engine, "_resolve_model", model)
    monkeypatch.setattr(worker, "run_engine", engine.investigate)
    await _request("q1", "What changed?")
    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    assert [request.id for request in record.pending_requests] == ["q1"]
    assert record.retry_after > time.time()
    assert not record.report
    record.retry_after = 0
    await service.INVESTIGATIONS.put("I1", record)
    await worker.process_channel("I1")
    assert model.await_count == 2


def test_slack_findings_link_evidence_without_untrusted_mentions():
    report = InvestigationReport(
        summary="Found a symptom [slack:1] <!channel>",
        evidence=[
            Evidence(
                id="slack:1",
                source="slack",
                url="https://slack.com/archives/C1/p123",
                summary="Symptom",
            ),
            Evidence(id="bad", source="slack", url="javascript:alert(1)", summary="invalid"),
        ],
    )
    text = worker.report_text(report, report.summary)
    assert "<https://slack.com/archives/C1/p123|[1]>" in text
    assert "[slack:1]" not in text
    assert "<!channel>" not in text
    assert "javascript:" not in text


async def test_queued_question_before_pause_cannot_restart_stopped_work(incident):
    await _request("earlier", "Do work")
    await _request("later", action="pause")

    await worker.process_channel("I1")
    await worker.process_channel("I1")

    worker.run_engine.assert_not_awaited()
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "paused"
    assert record.pending_requests == []


@pytest.mark.parametrize("earlier_action", ["ask", "resume"])
async def test_inflight_stop_consumes_earlier_controls_before_next_worker(
    incident, monkeypatch, earlier_action
):
    async def engine(*args, before_tool_call, **kwargs):
        await _request("earlier", "This request precedes pause", action=earlier_action)
        await _request("stop", action="pause")
        await before_tool_call()
        pytest.fail("Stop was not applied")

    mock_engine = AsyncMock(side_effect=engine)
    monkeypatch.setattr(worker, "run_engine", mock_engine)
    await worker.process_channel("I1")
    await worker.process_channel("I1")

    assert mock_engine.await_count == 1
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "paused"
    assert record.pending_requests == []
    assert "earlier" in record.processed_receipts


async def test_crash_during_explicit_pass_restores_paused_watch(incident, monkeypatch):
    class WorkerCrash(BaseException):
        pass

    incident.status, incident.reason = "paused", "responder_pause"
    await service.INVESTIGATIONS.put("I1", incident)
    await _request("q1", "Recover in-flight question")
    engine = AsyncMock(side_effect=WorkerCrash("worker lost"))
    monkeypatch.setattr(worker, "run_engine", engine)
    with pytest.raises(WorkerCrash):
        await worker.process_channel("I1")

    engine.side_effect = None
    engine.return_value = InvestigationReport(summary="Recovered answer")
    await worker.process_channel("I1")

    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "paused"
    assert record.reason == "responder_pause"
    assert engine.await_count == 2


async def test_access_check_failure_clears_cached_read_eligibility(incident, monkeypatch):
    info = await worker.slack.channel_info("C1")
    monkeypatch.setattr(
        worker.slack, "channel_info", AsyncMock(side_effect=[info, RuntimeError("offline")])
    )

    await worker.process_channel("I1")

    record = await service.INVESTIGATIONS.get("I1")
    assert not record.can_read
    assert record.last_verified_at == 0


@pytest.mark.parametrize("status", ["paused", "completed"])
async def test_slack_failure_preserves_stopped_watch_state(incident, monkeypatch, status):
    incident.status, incident.reason = status, "responder_" + status
    await service.INVESTIGATIONS.put("I1", incident)
    info = await worker.slack.channel_info("C1")
    monkeypatch.setattr(
        worker.slack, "channel_info", AsyncMock(side_effect=RuntimeError("unavailable"))
    )

    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == status

    record.retry_after = 0
    await service.INVESTIGATIONS.put("I1", record)
    monkeypatch.setattr(worker.slack, "channel_info", AsyncMock(return_value=info))
    await service.RECEIPTS.put(
        "message",
        Receipt(
            id="message",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload={"ts": "201.0", "text": "New ordinary evidence"},
            received_at=time.time(),
        ),
    )
    await worker.process_channel("I1")
    assert (await service.INVESTIGATIONS.get("I1")).status == status
    worker.run_engine.assert_not_awaited()


async def test_multiple_accepted_questions_each_receive_a_pass(incident):
    await _request("q1", "First question")
    await _request("q2", "Second question")

    await worker.process_channel("I1")
    await worker.process_channel("I1")

    assert [call.args[3] for call in worker.run_engine.await_args_list] == [
        "First question",
        "Second question",
    ]
    assert (await service.INVESTIGATIONS.get("I1")).pending_requests == []


async def test_crash_after_receipt_checkpoint_preserves_question(incident, monkeypatch):
    incident.status = "paused"
    await service.INVESTIGATIONS.put("I1", incident)
    await _request("q1", "Recover this question")
    save = worker.save

    async def crash_after_checkpoint(record):
        await save(record)
        if "q1" in record.processed_receipts:
            raise SystemExit("worker crashed after receipt checkpoint")

    monkeypatch.setattr(worker, "save", crash_after_checkpoint)
    with pytest.raises(SystemExit):
        await worker.process_channel("I1")
    monkeypatch.setattr(worker, "save", save)

    await worker.process_channel("I1")

    assert worker.run_engine.await_count == 1
    assert worker.run_engine.await_args.args[3] == "Recover this question"
    assert (await service.INVESTIGATIONS.get("I1")).status == "paused"


@pytest.mark.parametrize("status", ["paused", "completed"])
async def test_question_on_stopped_watch_is_answered_without_reopening(incident, status):
    incident.status = status
    await service.INVESTIGATIONS.put("I1", incident)
    await _request("q1", "Question while stopped")

    await worker.process_channel("I1")

    assert worker.run_engine.await_args.args[3] == "Question while stopped"
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == status
    assert record.pending_requests == []


@pytest.mark.parametrize("status", ["paused", "completed", "watching"])
async def test_model_failure_preserves_lifecycle_and_backs_off(incident, monkeypatch, status):
    incident.status = status
    await service.INVESTIGATIONS.put("I1", incident)
    await _request("q1", "Try to answer")
    engine = AsyncMock(side_effect=RuntimeError("model unavailable"))
    monkeypatch.setattr(worker, "run_engine", engine)

    await worker.process_channel("I1")
    await worker.process_channel("I1")

    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == (status if status != "watching" else "needs_attention")
    assert record.retry_after > time.time()
    assert [request.text for request in record.pending_requests] == ["Try to answer"]
    assert engine.await_count == 1


@pytest.mark.parametrize("kind", ["command", "app_mention"])
async def test_stop_arriving_during_model_applies_without_failure_state(
    incident, monkeypatch, kind
):
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "sre@example.com")
    monkeypatch.setattr(
        worker.slack,
        "request",
        AsyncMock(return_value={"user": {"profile": {"email": "sre@example.com"}}}),
    )

    async def engine(*args, before_tool_call, **kwargs):
        await service.RECEIPTS.put(
            "stop",
            Receipt(
                id="stop",
                workspace_id="T1",
                channel_id="C1",
                kind=kind,
                payload={"action": "pause"}
                if kind == "command"
                else {"user": "U1", "text": "<@BOT> pause", "ts": "202.0"},
                received_at=time.time(),
            ),
        )
        await before_tool_call()
        pytest.fail("A tool was permitted after an authorized pause")

    monkeypatch.setattr(worker, "run_engine", engine)
    await worker.process_channel("I1")

    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "paused"
    assert record.reason == "responder_pause"
    assert "stop" in record.processed_receipts


async def test_first_findings_publish_immediately_after_introduction(incident, monkeypatch):
    policy = await service.get_policy()
    await service.POLICIES.put("default", policy)
    publish = AsyncMock(side_effect=["100.1", "100.2"])
    monkeypatch.setattr(worker.slack, "publish", publish)

    await worker.process_channel("I1")

    assert publish.await_count == 2
    assert publish.await_args_list[1].args[1] == "Cause remains unknown."
    assert publish.await_args_list[1].args[2] is None


async def test_crash_after_report_checkpoint_recovers_publication_without_rerunning_model(
    incident, monkeypatch
):
    policy = await service.get_policy()
    await service.POLICIES.put("default", policy)
    publish = AsyncMock(side_effect=["100.1", "100.2"])
    monkeypatch.setattr(worker.slack, "publish", publish)
    save = worker.save

    async def crash_after_report(record):
        await save(record)
        if record.report:
            raise SystemExit("worker crashed after report checkpoint")

    monkeypatch.setattr(worker, "save", crash_after_report)
    with pytest.raises(SystemExit):
        await worker.process_channel("I1")
    monkeypatch.setattr(worker, "save", save)

    await worker.process_channel("I1")
    await worker.process_channel("I1")

    assert worker.run_engine.await_count == 1
    assert publish.await_count == 2
    assert publish.await_args_list[1].args[1] == "Cause remains unknown."


async def test_pause_after_report_checkpoint_prevents_findings_publication(incident, monkeypatch):
    policy = await service.get_policy()
    await service.POLICIES.put("default", policy)
    publish = AsyncMock(return_value="100.1")
    monkeypatch.setattr(worker.slack, "publish", publish)
    save = worker.save
    stopped = False

    async def pause_after_report(record):
        nonlocal stopped
        await save(record)
        if record.report and not stopped:
            stopped = True
            await _request("stop", action="pause")

    monkeypatch.setattr(worker, "save", pause_after_report)
    await worker.process_channel("I1")

    assert publish.await_count == 1
    assert (await service.INVESTIGATIONS.get("I1")).status == "paused"


async def test_failed_publication_preflight_retries_without_rerunning_model(incident, monkeypatch):
    policy = await service.get_policy()
    await service.POLICIES.put("default", policy)
    info = await worker.slack.channel_info("C1")
    publish = AsyncMock(side_effect=["100.1", "100.2"])
    monkeypatch.setattr(worker.slack, "publish", publish)
    put = service.PUBLICATIONS.put
    failed = False

    async def lose_connection_before_send(key, publication):
        nonlocal failed
        await put(key, publication)
        if publication.reason == "findings" and publication.status == "sending" and not failed:
            failed = True
            monkeypatch.setattr(
                worker.slack, "channel_info", AsyncMock(side_effect=RuntimeError("offline"))
            )

    monkeypatch.setattr(service.PUBLICATIONS, "put", lose_connection_before_send)
    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    record.retry_after = 0
    await service.INVESTIGATIONS.put("I1", record)
    monkeypatch.setattr(worker.slack, "channel_info", AsyncMock(return_value=info))

    await worker.process_channel("I1")

    assert worker.run_engine.await_count == 1
    assert publish.await_count == 2
    assert (await service.INVESTIGATIONS.get("I1")).pending_publications == []


async def test_bootstrap_merges_history_even_when_message_receipt_arrived_first(incident):
    info = await worker.slack.channel_info("C1")
    info["topic"] = {"value": "Checkout incident"}
    await service.RECEIPTS.put(
        "new-message",
        Receipt(
            id="new-message",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload={"user": "U1", "ts": "300.0", "text": "Latest evidence"},
            received_at=time.time(),
        ),
    )

    await worker.process_channel("I1")

    texts = {message.text for message in worker.run_engine.await_args.args[0]}
    assert texts == {
        "Checkout requests fail with HTTP 500 after deployment.",
        "Latest evidence",
        "Checkout incident",
    }
    assert (await service.INVESTIGATIONS.get("I1")).bootstrap_complete


async def test_bootstrap_history_cannot_resurrect_deleted_message(incident):
    await service.RECEIPTS.put(
        "deletion",
        Receipt(
            id="deletion",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload={"subtype": "message_deleted", "deleted_ts": "200.1", "event_ts": "300.0"},
            received_at=time.time(),
        ),
    )

    await worker.process_channel("I1")

    record = await service.INVESTIGATIONS.get("I1")
    assert record.bootstrap_complete
    assert len(record.messages) == 1
    assert record.messages[0].deleted
    worker.run_engine.assert_not_awaited()


@pytest.mark.parametrize(
    "identity",
    [
        {"metadata": {"event_type": "open_swe_investigation"}},
        {"metadata": {"event_type": "investigate_report"}},
        {"app_id": "A1"},
        {"bot_profile": {"app_id": "A1"}},
        {"user": "OWNBOT"},
    ],
)
async def test_own_bot_receipt_cannot_trigger_work(incident, monkeypatch, identity):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "OWNBOT")
    monkeypatch.setattr(worker.slack, "history", AsyncMock(return_value=([], [])))
    await service.RECEIPTS.put(
        "own-message",
        Receipt(
            id="own-message",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload={"ts": "300.0", "text": "Own output", "bot_id": "B1", **identity},
            received_at=time.time(),
        ),
    )

    await worker.process_channel("I1")

    worker.run_engine.assert_not_awaited()
    assert (await service.INVESTIGATIONS.get("I1")).messages == []


async def test_bootstrap_filters_own_app_messages(incident, monkeypatch):
    own = InvestigationMessage.model_validate(
        {"id": "300.0", "ts": "300.0", "text": "Own output", "bot_id": "B1", "app_id": "A1"}
    )
    monkeypatch.setattr(worker.slack, "history", AsyncMock(return_value=([own], [])))

    await worker.process_channel("I1")

    worker.run_engine.assert_not_awaited()


async def test_matching_code_channel_is_not_joined_or_investigated(incident, monkeypatch):
    info = await worker.slack.channel_info("C1")
    info["properties"] = {"record_channel": {"record_type": "agent_channel"}}
    join = AsyncMock()
    monkeypatch.setattr(worker.slack, "join", join)
    await service.RECEIPTS.put(
        "created",
        Receipt(
            id="created",
            workspace_id="T1",
            channel_id="C1",
            kind="channel_created",
            payload={"channel": {"id": "C1", "name": "inc-code"}},
            received_at=time.time(),
        ),
    )

    await worker.process_channel("I1")

    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "paused"
    assert record.reason == "code_channel"
    assert not record.can_read
    assert await worker.channel_receipts(record) == []
    worker.slack.history.assert_not_awaited()
    worker.run_engine.assert_not_awaited()
    join.assert_not_awaited()


async def test_findings_post_carries_the_report_digest(incident, monkeypatch):
    from agent.investigations.models import Hypothesis

    worker.run_engine.return_value = InvestigationReport(
        summary="Retries spiked after the deploy [slack:200.1].",
        impact="Checkout latency doubled.",
        hypotheses=[Hypothesis(title="Deploy changed retry policy", assessment="plausible")],
        questions=["Which service owns the retry loop?"],
        gaps=["No Datadog access."],
        evidence=[
            Evidence(
                id="slack:200.1",
                source="slack",
                url="https://slack.com/archives/C1/p2001",
                summary="Report",
            )
        ],
    )
    publish = AsyncMock(side_effect=["100.1", "100.2"])
    monkeypatch.setattr(worker.slack, "publish", publish)

    await worker.process_channel("I1")

    assert publish.await_count == 2
    findings = publish.await_args_list[1].args[1]
    for fragment in (
        "Retries spiked after the deploy",
        "*Impact:* Checkout latency doubled.",
        "• Deploy changed retry policy (plausible)",
        "*Open questions:*\n• Which service owns the retry loop?",
        "*Coverage gaps:*\n• No Datadog access.",
        "<https://slack.com/archives/C1/p2001|[1]>",
    ):
        assert fragment in findings
    assert publish.await_args_list[1].args[2] is None


async def test_pause_and_resume_notices_are_posted_in_the_channel(incident, monkeypatch):
    publish = AsyncMock(side_effect=["100.1", "100.2", "100.3", "100.4"])
    monkeypatch.setattr(worker.slack, "publish", publish)
    await worker.process_channel("I1")
    await _request("p1", action="pause")
    await worker.process_channel("I1")
    await worker.process_channel("I1")
    assert "Investigation paused." in publish.await_args_list[2].args[1]
    await _request("r1", action="resume")
    await worker.process_channel("I1")
    await worker.process_channel("I1")
    texts = [call.args[1] for call in publish.await_args_list]
    assert any(text == "Investigation resumed." for text in texts)
    assert all(call.args[2] is None for call in publish.await_args_list[1:])


async def test_empty_channel_waits_for_context_without_model_call(incident, monkeypatch):
    monkeypatch.setattr(worker.slack, "history", AsyncMock(return_value=([], [])))
    await worker.process_channel("I1")
    result = await service.INVESTIGATIONS.get("I1")
    assert result.reason == "awaiting_context"
    assert not worker.run_engine.called


async def test_pause_receipt_wins_over_new_evidence(incident):
    await service.RECEIPTS.put(
        "pause",
        Receipt(
            id="pause",
            workspace_id="T1",
            channel_id="C1",
            kind="command",
            payload={"action": "pause"},
            received_at=time.time(),
        ),
    )
    await worker.process_channel("I1")
    assert (await service.INVESTIGATIONS.get("I1")).status == "paused"
    assert not worker.run_engine.called


async def test_archived_channel_cannot_run_or_publish(incident, monkeypatch):
    info = await worker.slack.channel_info("C1")
    monkeypatch.setattr(
        worker.slack, "channel_info", AsyncMock(return_value=info | {"is_archived": True})
    )
    await worker.process_channel("I1")
    assert (await service.INVESTIGATIONS.get("I1")).status == "completed"
    assert not worker.run_engine.called


async def test_loss_of_membership_does_not_auto_rejoin(incident, monkeypatch):
    incident.joined = True
    await service.INVESTIGATIONS.put("I1", incident)
    info = await worker.slack.channel_info("C1")
    monkeypatch.setattr(
        worker.slack, "channel_info", AsyncMock(return_value=info | {"is_member": False})
    )
    join = AsyncMock()
    monkeypatch.setattr(worker.slack, "join", join)
    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "paused" and not record.can_read
    assert not join.called and not worker.run_engine.called


async def test_duplicate_worker_does_not_repeat_unchanged_pass(incident):
    await worker.process_channel("I1")
    await worker.process_channel("I1")
    assert worker.run_engine.call_count == 1


async def test_new_messages_steer_passes_without_an_hourly_cap(incident, monkeypatch):
    publish = AsyncMock(return_value="100.1")
    monkeypatch.setattr(worker.slack, "publish", publish)
    await worker.process_channel("I1")
    for index in range(8):
        record = await service.INVESTIGATIONS.get("I1")
        record.pending_since = time.time() - 60
        await service.INVESTIGATIONS.put("I1", record)
        await service.RECEIPTS.put(
            f"m{index}",
            Receipt(
                id=f"m{index}",
                workspace_id="T1",
                channel_id="C1",
                kind="message",
                payload={"ts": f"30{index}.0", "text": f"update {index}", "user": "U1"},
                received_at=time.time(),
                source_time=300 + index,
            ),
        )
        await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    assert worker.run_engine.call_count == 9
    assert record.status == "watching" and record.reason == ""
    # The report never changed, so the thread heard the findings once, after the introduction.
    assert publish.await_count == 2


async def test_findings_post_again_only_when_the_report_changes(incident, monkeypatch):
    publish = AsyncMock(return_value="100.1")
    monkeypatch.setattr(worker.slack, "publish", publish)
    await worker.process_channel("I1")
    assert publish.await_count == 2

    async def steer(text: str, index: int) -> None:
        record = await service.INVESTIGATIONS.get("I1")
        record.pending_since = time.time() - 60
        await service.INVESTIGATIONS.put("I1", record)
        await service.RECEIPTS.put(
            f"s{index}",
            Receipt(
                id=f"s{index}",
                workspace_id="T1",
                channel_id="C1",
                kind="message",
                payload={"ts": f"40{index}.0", "text": text, "user": "U1"},
                received_at=time.time(),
                source_time=400 + index,
            ),
        )
        await worker.process_channel("I1")

    await steer("same conclusion", 1)
    assert publish.await_count == 2
    worker.run_engine.return_value = InvestigationReport(summary="Deploy 42 caused it.")
    await steer("look at deploy 42", 2)
    assert publish.await_count == 3
    assert "Deploy 42 caused it." in publish.await_args.args[1]


async def test_introduction_links_the_dashboard_and_anchors_the_thread(incident, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://swe.example.com")
    publish = AsyncMock(side_effect=["100.1", "100.2"])
    monkeypatch.setattr(worker.slack, "publish", publish)

    await worker.process_channel("I1")

    intro = publish.await_args_list[0].args[1]
    assert "<https://swe.example.com/investigate/I1|Open investigation>" in intro
    assert "Findings will appear here" in intro
    assert (await service.INVESTIGATIONS.get("I1")).anchor_ts == "100.1"
