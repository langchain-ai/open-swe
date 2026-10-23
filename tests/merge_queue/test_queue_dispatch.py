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


@pytest.mark.parametrize("status", [204, 403, 503, 307])
async def test_dispatch_signs_exact_contract_and_never_retries_or_redirects(
    monkeypatch: pytest.MonkeyPatch, status: int
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
        return httpx2.Response(status, headers={"Location": "https://other.example/"})

    monkeypatch.setattr(dispatch, "pooled_transport", lambda: httpx2.MockTransport(handle))
    gateway = DispatchGateway("https://dispatcher.example/queue", SecretStr(secret))
    if status == 204:
        await gateway.dispatch(contract, deadline)
    else:
        with pytest.raises(DispatchRejected if status == 403 else RuntimeError):
            await gateway.dispatch(contract, deadline)
    assert len(sent) == 1


def test_dispatch_secret_disables_local_agent_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    from openswe.sandboxes.providers.local import create_local_sandbox

    monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
    with pytest.raises(ValueError, match="queue credentials"):
        create_local_sandbox()
