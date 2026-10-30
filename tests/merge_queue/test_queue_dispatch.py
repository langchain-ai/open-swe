import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import httpx2
import pytest
from pydantic import SecretStr

from openswe.merge_queue import dispatch
from openswe.merge_queue.dispatch import DispatchGateway, DispatchRejected
from openswe.merge_queue.validation_contract import ValidationRequest


@pytest.mark.parametrize(
    ("status", "retry"),
    [
        (204, "none"),
        (403, "none"),
        (502, "none"),
        (503, "none"),
        (307, "none"),
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
            headers["X-Merge-Queue-Retry-Signature"] = hmac.digest(
                secret.encode(),
                b"retry:" + (b"different request" if retry == "wrong-request" else request.content),
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
            DispatchRejected if status == 403 or retry == "always" else RuntimeError
        ):
            await gateway.dispatch(contract, deadline)
    assert len(sent) == {"once": 2, "always": 3}.get(retry, 1)
    assert all(request.content == sent[0].content for request in sent)
