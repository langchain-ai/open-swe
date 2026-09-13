import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.incidents import service, worker
from agent.incidents.models import (
    Evidence,
    Incident,
    IncidentMessage,
    IncidentPolicy,
    IncidentReport,
    Receipt,
)


@pytest.fixture
async def incident(fake_store, monkeypatch):
    policy = IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1")
    await service.POLICIES.put("default", policy)
    record = Incident(
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
                    IncidentMessage(
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
        AsyncMock(return_value=IncidentReport(summary="Cause remains unknown.")),
    )
    monkeypatch.setattr(service, "schedule_wake", AsyncMock())
    monkeypatch.setattr(service, "wake", AsyncMock())
    monkeypatch.setattr(worker.slack, "publish", AsyncMock(return_value="100.0"))
    monkeypatch.setattr(worker.slack, "set_session_status", AsyncMock())
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


@pytest.mark.parametrize("subtype", ["channel_join", "channel_leave"])
async def test_membership_events_do_not_extend_watching_or_queue_analysis(incident, subtype):
    previous_activity = incident.last_source_activity_at
    await service.RECEIPTS.put(
        "membership",
        Receipt(
            id="membership",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload={"subtype": subtype, "ts": str(time.time() + 10), "text": "Membership update"},
            received_at=time.time(),
        ),
    )
    await worker._consume_receipts(
        incident, await service.get_policy(), await worker.slack.channel_info("C1")
    )
    assert incident.last_source_activity_at == previous_activity
    assert incident.messages == []
    assert incident.pending_since == 0
    assert "membership" in incident.processed_receipts


@pytest.mark.parametrize("paused", [False, True])
async def test_system_followup_runs_without_authorizing_actions_or_resuming_pause(
    incident, paused, monkeypatch
):
    from agent.incidents.followups import enqueue_followup

    await worker.process_channel(incident.id)
    record = await service.INVESTIGATIONS.get(incident.id)
    record.agent_thread_id = "conversation"
    if paused:
        record.status = "paused"
    await service.INVESTIGATIONS.put(record.id, record)
    worker.run_engine.reset_mock()
    await enqueue_followup(record.agent_thread_id, "finished", "Build completed. Check output.")
    await worker.process_channel(record.id)
    now = time.time()
    monkeypatch.setattr(worker.time, "time", lambda: now + 15)
    await worker.process_channel(record.id)
    if paused:
        worker.run_engine.assert_not_awaited()
        assert (await service.INVESTIGATIONS.get(record.id)).status == "paused"
    else:
        worker.run_engine.assert_awaited_once()
        assert worker.run_engine.await_args.kwargs["explicit"] is False
        messages = worker.run_engine.await_args.args[0]
        assert any(message.event_type == "agent_followup" for message in messages)


@pytest.mark.parametrize("has_report", [False, True])
async def test_message_burst_waits_for_quiet_then_runs_once(incident, monkeypatch, has_report):
    if not has_report:
        worker.slack.history.return_value = ([], [])
    await worker.process_channel("I1")
    worker.run_engine.reset_mock()
    worker.slack.publish.reset_mock()
    started = time.time()
    clock = [started]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])

    for index, offset in enumerate([0, 10]):
        clock[0] = started + offset
        await service.RECEIPTS.put(
            f"burst-{index}",
            Receipt(
                id=f"burst-{index}",
                workspace_id="T1",
                channel_id="C1",
                kind="message",
                payload={"ts": str(clock[0]), "text": f"Alert update {index}", "user": "U1"},
                received_at=clock[0],
            ),
        )
        assert await worker.process_channel("I1") == {"status": "debouncing"}
    clock[0] = started + 15
    assert await worker.process_channel("I1") == {"status": "debouncing"}
    worker.run_engine.assert_not_awaited()
    worker.slack.publish.assert_not_awaited()

    clock[0] = started + 25
    await worker.process_channel("I1")
    await worker.process_channel("I1")
    worker.run_engine.assert_awaited_once()
    messages = worker.run_engine.await_args.args[0]
    assert {m.text for m in messages} >= {"Alert update 0", "Alert update 1"}
    record = await service.INVESTIGATIONS.get("I1")
    assert record.pending_since == record.pending_message_at == 0


async def test_continuous_messages_cannot_defer_analysis_past_one_minute(incident, monkeypatch):
    worker.slack.history.return_value = ([], [])
    await worker.process_channel("I1")
    started = time.time()
    clock = [started]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    for offset in range(0, 61, 10):
        clock[0] = started + offset
        await service.RECEIPTS.put(
            str(offset),
            Receipt(
                id=str(offset),
                workspace_id="T1",
                channel_id="C1",
                kind="message",
                payload={"ts": str(clock[0]), "text": f"Symptom {offset}", "user": "U1"},
                received_at=clock[0],
            ),
        )
        await worker.process_channel("I1")
        assert worker.run_engine.await_count == (1 if offset == 60 else 0)


@pytest.mark.parametrize("ignored", ["duplicate", "own_bot"])
async def test_duplicate_and_own_messages_do_not_extend_debounce(incident, monkeypatch, ignored):
    worker.slack.history.return_value = ([], [])
    await worker.process_channel("I1")
    started = time.time()
    clock = [started]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    payload = {"ts": str(started), "text": "Error rate increased", "user": "U1"}
    await service.RECEIPTS.put(
        "alert",
        Receipt(
            id="alert",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload=payload,
            received_at=clock[0],
        ),
    )
    await worker.process_channel("I1")
    clock[0] += 10
    await service.RECEIPTS.put(
        "ignored",
        Receipt(
            id="ignored",
            workspace_id="T1",
            channel_id="C1",
            kind="message",
            payload=payload
            if ignored == "duplicate"
            else {
                "ts": str(clock[0]),
                "text": "Bot update",
                "app_id": "A1",
            },
            received_at=clock[0],
        ),
    )
    await worker.process_channel("I1")
    clock[0] = started + 15
    await worker.process_channel("I1")
    worker.run_engine.assert_awaited_once()
    assert [message.text for message in worker.run_engine.await_args.args[0]] == [payload["text"]]


@pytest.mark.parametrize("action", ["ask", "pause", "complete"])
async def test_explicit_requests_bypass_message_debounce(incident, action):
    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    record.pending_since = time.time()
    await service.INVESTIGATIONS.put("I1", record)
    await _request("control", "Check impact", action=action)
    worker.run_engine.reset_mock()
    await worker.process_channel("I1")
    assert worker.run_engine.await_count == (1 if action == "ask" else 0)
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == {"ask": "watching", "pause": "paused", "complete": "completed"}[action]


@pytest.mark.parametrize("expired", [False, True])
async def test_completed_incident_applies_document_edits_without_restarting_analysis(
    incident, expired
):
    from agent.incidents import documents

    incident.status = "completed"
    incident.expired = expired
    await service.INVESTIGATIONS.put(incident.id, incident)
    operation = await documents.submit_edit(
        incident.id,
        kind="status_page_draft",
        markdown="Service has recovered.",
        expected_revision=0,
        request_id="final-update",
        actor={"id": "github:responder"},
    )
    assert operation["status"] == "pending"
    assert await documents.REVISIONS.search_all() == []

    await worker.process_channel(incident.id)
    await worker.process_channel(incident.id)

    current = (await documents.document_context(incident.id))["status_page_draft"]
    assert current["revision"] == 1
    assert current["markdown"] == "Service has recovered."
    assert (await documents.get_operation(incident.id, operation["id"]))["status"] == "applied"
    assert len((await documents.list_revisions(incident.id, "status_page_draft"))["items"]) == 1
    record = await service.INVESTIGATIONS.get(incident.id)
    assert record.status == "completed" and record.expired == expired
    assert not record.report
    worker.run_engine.assert_not_awaited()


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
    from agent.incidents import engine

    model = AsyncMock(side_effect=RuntimeError("platform unavailable"))
    monkeypatch.setattr(
        service, "store_client", lambda: SimpleNamespace(threads=SimpleNamespace(create=model))
    )
    monkeypatch.setattr(worker, "run_engine", engine.incidents)
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
    from agent.incidents.presentation import report_message

    report = IncidentReport(
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
    text, _ = report_message(report, report.summary, None)
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
    engine.return_value = IncidentReport(summary="Recovered answer")
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
    assert "Cause remains unknown." in publish.await_args_list[1].args[1]
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
    checkpoint = await service.INVESTIGATIONS.get("I1")
    blocks = checkpoint.pending_publications[0].blocks
    assert blocks

    await worker.process_channel("I1")
    await worker.process_channel("I1")

    assert worker.run_engine.await_count == 1
    assert publish.await_count == 2
    assert "Cause remains unknown." in publish.await_args_list[1].args[1]
    assert publish.await_args_list[1].kwargs["blocks"] == blocks


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


async def test_bootstrap_merges_history_even_when_message_receipt_arrived_first(
    incident, monkeypatch
):
    started = time.time()
    monkeypatch.setattr(worker.time, "time", lambda: started)
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
    monkeypatch.setattr(worker.time, "time", lambda: started + 15)
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
        {"metadata": {"event_type": "open_swe_incident"}},
        {"metadata": {"event_type": "incidents_report"}},
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
    own = IncidentMessage.model_validate(
        {"id": "300.0", "ts": "300.0", "text": "Own output", "bot_id": "B1", "app_id": "A1"}
    )
    monkeypatch.setattr(worker.slack, "history", AsyncMock(return_value=([own], [])))

    await worker.process_channel("I1")

    worker.run_engine.assert_not_awaited()


async def test_matching_code_channel_is_not_joined_or_incidentsd(incident, monkeypatch):
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
    from agent.incidents.models import Hypothesis

    worker.run_engine.return_value = IncidentReport(
        summary="Retries spiked after the deploy [slack:200.1].",
        impact="Checkout latency doubled.",
        next_steps=["Review the retry policy before rolling back [slack:200.1]."],
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
        "Review the retry policy before rolling back",
        "<https://slack.com/archives/C1/p2001|[1]>",
    ):
        assert fragment in findings
    assert "Deploy changed retry policy" not in findings
    assert "Checkout latency doubled" not in findings
    assert "No Datadog access" not in findings
    assert "Open questions" not in findings
    assert publish.await_args_list[1].args[2] is None
    assert publish.await_args_list[1].kwargs["blocks"]


async def test_pause_and_resume_notices_are_posted_in_the_channel(incident, monkeypatch):
    publish = AsyncMock(side_effect=["100.1", "100.2", "100.3", "100.4"])
    monkeypatch.setattr(worker.slack, "publish", publish)
    await worker.process_channel("I1")
    await _request("p1", action="pause")
    await worker.process_channel("I1")
    await worker.process_channel("I1")
    assert "Incident paused." in publish.await_args_list[2].args[1]
    await _request("r1", action="resume")
    await worker.process_channel("I1")
    await worker.process_channel("I1")
    texts = [call.args[1] for call in publish.await_args_list]
    assert any(text == "Incident resumed." for text in texts)
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
    from agent.incidents.models import Hypothesis

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
    worker.run_engine.return_value = IncidentReport(
        summary="Cause remains unknown.",
        hypotheses=[Hypothesis(title="Another speculative explanation")],
        questions=["Any more details?"],
    )
    await steer("another hypothesis", 2)
    assert publish.await_count == 2
    worker.run_engine.return_value = IncidentReport(summary="Deploy 42 caused it.")
    await steer("look at deploy 42", 3)
    assert publish.await_count == 3
    assert "Deploy 42 caused it." in publish.await_args.args[1]


async def test_introduction_links_the_dashboard_and_anchors_the_thread(incident, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://swe.example.com")
    publish = AsyncMock(side_effect=["100.1", "100.2"])
    monkeypatch.setattr(worker.slack, "publish", publish)

    await worker.process_channel("I1")

    intro = publish.await_args_list[0].args[1]
    assert "<https://swe.example.com/incidents/I1|Open incident>" in intro
    assert "Findings will appear in this channel" in intro
    assert (await service.INVESTIGATIONS.get("I1")).anchor_ts == "100.1"


@pytest.mark.parametrize("failure", [False, True])
async def test_agent_session_exits_processing_after_success_or_failure(incident, failure):
    if failure:
        worker.run_engine.side_effect = RuntimeError("model unavailable")
    await worker.process_channel("I1")
    statuses = worker.slack.set_session_status.await_args_list
    assert statuses[0].args[1:3] == ("100.0", "processing")
    assert statuses[-1].args[2] == ("suspended" if failure else "active")
    assert (await service.INVESTIGATIONS.get("I1")).slack_session_thread_ts is None


async def test_session_api_failure_does_not_block_report_delivery(incident):
    worker.slack.set_session_status.side_effect = RuntimeError("unsupported agent")
    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    assert record.status == "watching" and record.report
    assert worker.slack.publish.await_count == 2


@pytest.mark.parametrize("thread_ts", [None, "250.1"])
async def test_answers_follow_the_question_channel_or_thread(incident, monkeypatch, thread_ts):
    monkeypatch.setattr(worker, "is_observability_authorized", lambda _: True)
    monkeypatch.setattr(
        worker.slack,
        "request",
        AsyncMock(return_value={"user": {"profile": {"email": "responder@example.com"}}}),
    )
    await service.RECEIPTS.put(
        "question",
        Receipt(
            id="question",
            workspace_id="T1",
            channel_id="C1",
            kind="app_mention",
            payload={
                "text": "<@UBOT> What is affected?",
                "user": "U1",
                "ts": "300.1",
                **({"thread_ts": thread_ts} if thread_ts else {}),
            },
            received_at=time.time(),
        ),
    )
    await worker.process_channel("I1")
    assert worker.slack.publish.await_args.args[2] == thread_ts
    assert worker.slack.set_session_status.await_args_list[0].args[1] == (thread_ts or "100.0")


@pytest.mark.parametrize(
    "authorized,thread_ts,stopped",
    [(True, "100.0", True), (False, "100.0", False), (True, "other", False)],
)
async def test_native_stop_is_authorized_and_scoped_to_incident_session(
    incident, monkeypatch, authorized, thread_ts, stopped
):
    monkeypatch.setattr(worker, "is_observability_authorized", lambda _: authorized)
    monkeypatch.setattr(
        worker.slack,
        "request",
        AsyncMock(return_value={"user": {"profile": {"email": "responder@example.com"}}}),
    )

    async def run(*args, **kwargs):
        await service.RECEIPTS.put(
            "stop",
            Receipt(
                id="stop",
                workspace_id="T1",
                channel_id="C1",
                kind="agent_session_stopped",
                payload={"user": "U1", "thread_ts": thread_ts, "event_ts": str(time.time())},
                received_at=time.time(),
            ),
        )
        await kwargs["before_tool_call"]()
        return IncidentReport(summary="New findings.")

    worker.run_engine.side_effect = run
    await worker.process_channel("I1")
    record = await service.INVESTIGATIONS.get("I1")
    assert (record.status == "paused") is stopped
    assert (record.report is None) is stopped
    assert worker.slack.set_session_status.await_args.args[2] == (
        "suspended" if stopped else "active"
    )
