import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import httpx2
import pytest
from pydantic import SecretStr

from openswe.merge_queue import dispatch
from openswe.merge_queue.admission import QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.dispatch import (
    DeferralForbidden,
    DispatchEnvelope,
    DispatchGateway,
    DispatchRejected,
    WorkflowPolicy,
    WorkflowPolicyMismatch,
)
from openswe.merge_queue.recovery import RecoveryMember, RecoverySettings
from openswe.merge_queue.validation_contract import ValidationPolicy, ValidationRequest


@pytest.mark.parametrize("fault", [None, "signature", "replay"])
async def test_workflow_policy_requires_fresh_authenticated_response(monkeypatch, fault):
    def respond(request: httpx2.Request) -> httpx2.Response:
        nonce = request.url.params["nonce"]
        assert request.method == "GET" and request.url.path == "/workflow-policy"
        assert (
            request.headers["X-Merge-Queue-Signature"]
            == hmac.digest(b"s" * 32, b"workflow-policy-request:" + nonce.encode(), "sha256").hex()
        )
        body = json.dumps(
            {
                "nonce": str(uuid7()) if fault == "replay" else nonce,
                "repository": "acme/widgets",
                "repository_id": 42,
                "workflow_digest": "a" * 64,
                "workflow_policy_revision": 1,
                "dispatch_actor_id": 123,
                "check_app_id": 456,
            }
        ).encode()
        signature = hmac.digest(b"s" * 32, b"workflow-policy-response:" + body, "sha256").hex()
        return httpx2.Response(
            200,
            content=body,
            headers={"X-Merge-Queue-Signature": "invalid" if fault == "signature" else signature},
        )

    monkeypatch.setattr(dispatch, "pooled_transport", lambda: httpx2.MockTransport(respond))
    gateway = DispatchGateway("https://dispatcher.example/dispatch", SecretStr("s" * 32))
    if fault:
        with pytest.raises(ValueError):
            await gateway.workflow_policy()
    else:
        assert (await gateway.workflow_policy()).workflow_digest == "a" * 64


@pytest.mark.parametrize(
    ("status", "retry"),
    [
        (204, "none"),
        (403, "none"),
        (502, "none"),
        (503, "none"),
        (307, "none"),
        (412, "none"),
        (412, "wrong-request"),
        (412, "policy"),
        (503, "wrong-request"),
        (503, "once"),
        (503, "always"),
    ],
)
async def test_dispatch_retries_only_authenticated_preflight_failures(
    monkeypatch: pytest.MonkeyPatch, status: int, retry: str
) -> None:
    sent: list[httpx2.Request] = []
    secret = "s" * 32
    contract = ValidationRequest(
        repository="acme/widgets",
        batch_id=uuid7(),
        attempt_id=uuid7(),
        candidate_sha="a" * 40,
        workflow_sha="b" * 40,
        dispatch_actor_id=123,
    )
    deadline = datetime.now(UTC) + timedelta(minutes=5)

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        assert (
            request.headers["X-Merge-Queue-Signature"]
            == hmac.digest(secret.encode(), request.content, "sha256").hex()
        )
        assert json.loads(request.content)["contract"] == contract.model_dump(mode="json")
        headers = {"Location": "https://other.example/"}
        if retry != "none":
            policy_response = status == 412
            headers[
                "X-Merge-Queue-Policy-Signature"
                if policy_response
                else "X-Merge-Queue-Retry-Signature"
            ] = hmac.digest(
                secret.encode(),
                (b"policy-mismatch:" if policy_response else b"retry:")
                + (b"different request" if retry == "wrong-request" else request.content),
                "sha256",
            ).hex()
        return httpx2.Response(
            204 if retry == "once" and len(sent) > 1 else status, headers=headers
        )

    monkeypatch.setattr(dispatch, "pooled_transport", lambda: httpx2.MockTransport(handle))
    gateway = DispatchGateway("https://dispatcher.example/queue", SecretStr(secret))
    if status == 204 or retry == "once":
        await gateway.dispatch(contract, deadline)
    else:
        with pytest.raises(
            WorkflowPolicyMismatch
            if retry == "policy"
            else DispatchRejected
            if status == 403 or retry == "always"
            else RuntimeError
        ):
            await gateway.dispatch(contract, deadline)
    assert len(sent) == {"once": 2, "always": 3}.get(retry, 1)
    assert all(request.content == sent[0].content for request in sent)


@pytest.mark.parametrize(
    "change",
    [
        {"validation_suite": "services"},
        {"allow_main_advance": True},
        {"allow_test_exceptions": True},
    ],
)
def test_service_suite_policy_requires_new_revision_and_changes_validation_trust(change):
    config = QueueConfiguration(
        routing_id=uuid7(),
        repository="acme/widgets",
        repository_id=42,
        mode="native",
        dispatch_url="https://dispatcher.example/dispatch",
        workflow_digest="a" * 64,
        sync_workflow_policy=True,
        workflow_policy_revision=1,
        check_app_id=456,
        operator_emails=frozenset({"operator@example.com"}),
        recovery=RecoverySettings(channel_id="C123", operator_id=uuid7()),
        policy=QueuePolicy(checks={}, validation=ValidationPolicy(dispatch_actor_id=123)),
    )
    approved = WorkflowPolicy(
        nonce=uuid7(),
        repository=config.repository,
        repository_id=42,
        workflow_digest="a" * 64,
        workflow_policy_revision=1,
        dispatch_actor_id=123,
        check_app_id=456,
        **change,
    )
    with pytest.raises(ValueError, match="revision"):
        config.with_workflow_policy(approved)
    updated = config.with_workflow_policy(
        approved.model_copy(update={"workflow_policy_revision": 2})
    )
    assert updated.policy.validation.suite == approved.validation_suite
    assert updated.allow_main_advance == approved.allow_main_advance
    assert updated.allow_test_exceptions == approved.allow_test_exceptions
    assert updated.native_trust != config.native_trust
    assert updated.generation != config.generation


@pytest.mark.parametrize(
    "outcome",
    ["ok", "timeout", "gateway", "redirect", "rejected", "forbidden", "unsigned", "wrong-request"],
)
async def test_deferral_uses_gateway_without_replaying_uncertain_removal(monkeypatch, outcome):
    envelope = DispatchEnvelope(
        contract=ValidationRequest(
            repository="acme/widgets",
            batch_id=uuid7(),
            attempt_id=uuid7(),
            candidate_sha="a" * 40,
            workflow_sha="b" * 40,
            dispatch_actor_id=123,
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    member = RecoveryMember(entry_id="entry-2", number=2, head_sha="c" * 40)
    key = uuid7()
    sent = []

    def respond(request):
        sent.append(request)
        assert str(request.url) == "https://dispatcher.example/merge-queue/deferrals"
        assert (
            request.headers["X-Merge-Queue-Signature"]
            == hmac.digest(b"s" * 32, b"deferral:" + request.content, "sha256").hex()
        )
        assert json.loads(request.content) == {
            "dispatch_body": envelope.model_dump_json(exclude_none=True),
            "request_key": str(key),
            "expires_at": envelope.model_dump(mode="json")["expires_at"],
            **member.model_dump(mode="json"),
        }
        if outcome == "timeout":
            raise httpx2.ReadTimeout("Response lost", request=request)
        headers = {"Location": "https://other.example/"}
        if outcome in {"forbidden", "wrong-request"}:
            headers["X-Merge-Queue-Deferral-Signature"] = hmac.digest(
                b"s" * 32,
                b"deferral-forbidden:" + (request.content if outcome == "forbidden" else b"other"),
                "sha256",
            ).hex()
        return httpx2.Response(
            {"ok": 204, "gateway": 502, "redirect": 307, "rejected": 409}.get(outcome, 403),
            headers=headers,
        )

    monkeypatch.setattr(dispatch, "pooled_transport", lambda: httpx2.MockTransport(respond))
    gateway = DispatchGateway(
        "https://dispatcher.example/merge-queue/dispatch", SecretStr("s" * 32)
    )
    if outcome == "ok":
        await gateway.defer(envelope, key, member, expires_at=envelope.expires_at)
    else:
        with pytest.raises((httpx2.HTTPError, ValueError)) as error:
            await gateway.defer(envelope, key, member, expires_at=envelope.expires_at)
        assert isinstance(error.value, DeferralForbidden) is (outcome == "forbidden")
        if outcome == "forbidden":
            assert "GitHub denied queue removal" in str(error.value)
    assert len(sent) == 1
