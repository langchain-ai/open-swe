import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import httpx2
import pytest
from pydantic import SecretStr

from openswe.merge_queue import dispatch
from openswe.merge_queue.dispatch import DispatchGateway, DispatchRejected, WorkflowPolicyMismatch
from openswe.merge_queue.validation_contract import ValidationRequest


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
