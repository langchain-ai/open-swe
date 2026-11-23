import hmac
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from openswe.api.github_errors import add_github_error_handlers
from openswe.github.http import GitHubAppUnavailable
from openswe.merge_queue import control


@pytest.mark.parametrize("fault", [None, "signature", "expired", "long_expiry", "oversized"])
async def test_control_requires_a_fresh_domain_signed_request(monkeypatch, fault):
    monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
    handler = Mock(limit=AsyncMock(return_value=10), update=AsyncMock())
    opened = AsyncMock(return_value=handler)
    monkeypatch.setattr(control.QueueControl, "open", opened)
    body = json.dumps(
        {
            "repository": "acme/widgets",
            "repository_id": 42,
            "actor_email": "operator@example.com",
            "expires_at": (
                datetime.now(UTC)
                + timedelta(
                    seconds=-1 if fault == "expired" else 120 if fault == "long_expiry" else 30
                )
            ).isoformat(),
            "operation": "pause",
            "paused": True,
            "reason": "Investigating an outage",
            "expected_version": "a" * 64,
        }
    ).encode()
    if fault == "oversized":
        body += b" " * 4096
    signature = hmac.digest(
        b"s" * 32, (b"deferral:" if fault == "signature" else b"queue-control:") + body, "sha256"
    ).hex()
    app = FastAPI()
    app.include_router(control.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://queue.example"
    ) as client:
        response = await client.post(
            "/merge-queue/control", content=body, headers={"X-Merge-Queue-Signature": signature}
        )
    assert response.status_code == (413 if fault == "oversized" else 401 if fault else 200)
    if fault:
        opened.assert_not_awaited()
    else:
        assert handler.update.await_args.args[0].paused is True


@pytest.mark.parametrize("operation", ["status", "pause", "batch_size"])
async def test_controls_during_github_token_failure(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
    status = control.ControlStatus(
        version="a" * 64,
        paused=False,
        pause_owner=None,
        pause_reason=None,
        max_batch_size=5,
        max_batch_size_limit=None,
        batch=None,
    )
    handler = Mock(
        spec=control.QueueControl,
        limit=AsyncMock(side_effect=GitHubAppUnavailable("GitHub App token unavailable")),
        status=AsyncMock(return_value=status),
        update=AsyncMock(),
    )
    monkeypatch.setattr(control.QueueControl, "open", AsyncMock(return_value=handler))
    body = json.dumps(
        {
            "repository": "acme/widgets",
            "repository_id": 42,
            "actor_email": "operator@example.com",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "operation": operation,
            "paused": True if operation == "pause" else None,
            "max_batch_size": 3 if operation == "batch_size" else None,
            "reason": "Investigating an outage",
            "expected_version": status.version,
        }
    ).encode()
    signature = hmac.digest(b"s" * 32, b"queue-control:" + body, "sha256").hex()
    app = FastAPI()
    app.include_router(control.router)
    add_github_error_handlers(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://queue.example"
    ) as client:
        response = await client.post(
            "/merge-queue/control", content=body, headers={"X-Merge-Queue-Signature": signature}
        )
    assert response.status_code == (503 if operation == "batch_size" else 200)
    if operation == "status":
        assert response.json() == status.model_dump(mode="json")
    elif operation == "pause":
        assert response.json() is None
        handler.limit.assert_not_awaited()
        handler.update.assert_awaited_once()
    if operation != "pause":
        handler.update.assert_not_awaited()


async def test_control_rejects_other_repositories(monkeypatch):
    config = Mock(mode="native", check_app_id=456, repository="acme/widgets", repository_id=42)
    monkeypatch.setattr(control.QueueConfiguration, "load", AsyncMock(return_value=config))
    request = control.ControlRequest(
        repository="acme/other",
        repository_id=43,
        actor_email="operator@example.com",
        expires_at=datetime.now(UTC),
        operation="status",
    )
    with pytest.raises(control.HTTPException) as error:
        await control.QueueControl.open(request)
    assert error.value.status_code == 404


@pytest.mark.parametrize(
    "status,actions",
    [
        ("pending", []),
        ("applying", []),
        ("superseded", []),
        ("applied", ["Robin dequeued PR #103"]),
        ("escalated", ["Robin escalated an unconfirmed PR removal for operator review"]),
    ],
)
async def test_batch_history_only_reports_confirmed_removals(status, actions):
    from uuid import uuid7

    from openswe.github.queue import NativeQueueSnapshot
    from openswe.merge_queue.models import Attempt, Batch, Decision, Evidence
    from openswe.merge_queue.store import BatchHistory, NativeSelection
    from tests.merge_queue.test_queue_controller import native_response

    batch_id, attempt_id = uuid7(), uuid7()
    started = datetime(2026, 11, 20, tzinfo=UTC)
    snapshot = NativeQueueSnapshot.model_validate(native_response((101, 102)))
    assert snapshot.queue is not None
    history = BatchHistory(
        batch=Mock(
            spec=Batch,
            candidate=NativeSelection(entries=snapshot.queue.entries.nodes).model_dump(mode="json"),
            policy={
                "recovery": {
                    "settings": {"channel_id": "C123"},
                    "thread_ts": "123.456",
                }
            },
        ),
        members=[],
        attempts=[
            Mock(
                spec=Attempt,
                id=attempt_id,
                number=1,
                state="failed",
                created_at=started,
                finished_at=started + timedelta(minutes=10),
            )
        ],
        evidence=[
            Mock(
                spec=Evidence,
                attempt_id=attempt_id,
                key="validation-run",
                reference={"run_id": 123},
            )
        ],
        decisions=[
            Mock(
                spec=Decision,
                plan={
                    "kind": "native-recovery",
                    "status": status,
                    "thread_id": "recovery-thread",
                    "generation": "a" * 64,
                    "members": [],
                    "proposal": {
                        "request_key": str(uuid7()),
                        "batch_id": str(batch_id),
                        "attempt_id": str(attempt_id),
                        "candidate_sha": "b" * 40,
                        "action": "defer",
                        "reason": "This PR introduced the regression",
                        "member": {"entry_id": "entry-103", "number": 103, "head_sha": "c" * 40},
                    },
                },
            )
        ],
    )
    result = await control.BatchStatus.from_history(history, "acme/widgets")
    assert result.model_dump() == {
        "members": [
            {"entry_id": f"entry-{n}", "number": n, "head_sha": f"{n:040x}"} for n in (101, 102)
        ],
        "slack_url": "https://app.slack.com/archives/C123/p123456",
        "attempts": [
            {
                "id": attempt_id,
                "number": 1,
                "state": "failed",
                "created_at": started,
                "finished_at": started + timedelta(minutes=10),
                "workflow_url": "https://github.com/acme/widgets/actions/runs/123",
                "actions": actions,
            }
        ],
    }
