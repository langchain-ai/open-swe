"""MDA's Linear channel hands the backend genuine Linear deliveries, and only it can."""

import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openswe.linear import channel

_SERVICE_SECRET = "service-secret"  # noqa: S105 - test value
_SIGNING_SECRET = "lin_wh_signing"  # noqa: S105 - test value
_THREAD = str(uuid.uuid4())


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setattr("openswe.webhooks.common.LINEAR_WEBHOOK_SECRET", _SIGNING_SECRET)
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.linear.channel.EventLog.record", record)
    automations = AsyncMock()
    monkeypatch.setattr(channel, "_launch_automations", automations)
    api = FastAPI()
    api.state.record = record
    api.state.automations = automations
    api.include_router(channel.router)
    return api


def _delivery(payload: dict[str, object], *, signed_with: str = _SIGNING_SECRET) -> dict[str, str]:
    raw = json.dumps({"webhookTimestamp": int(time.time() * 1000), **payload})
    return {
        "thread_id": _THREAD,
        "delivery_id": "delivery-1",
        "raw_body": raw,
        "signature": hmac.new(signed_with.encode(), raw.encode(), hashlib.sha256).hexdigest(),
    }


async def _post(
    app: FastAPI, path: str, body: dict[str, str], secret: str | None = _SERVICE_SECRET
) -> httpx.Response:
    headers = {"x-mda-service-secret": secret} if secret is not None else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(f"/linear/channel{path}", json=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, "guess"])
async def test_only_the_mda_deployment_may_call(app: FastAPI, secret: str | None) -> None:
    response = await _post(app, "/events", _delivery({"type": "Issue"}), secret)
    assert response.status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_genuine_fresh_linear_deliveries_count(app: FastAPI) -> None:
    forged = _delivery({"type": "Issue", "action": "create"}, signed_with="other-secret")
    assert (await _post(app, "/events", forged)).status_code == 401
    stale = _delivery({"type": "Issue", "action": "create"})
    stale["raw_body"] = json.dumps({"type": "Issue", "action": "create", "webhookTimestamp": 1})
    stale["signature"] = hmac.new(
        _SIGNING_SECRET.encode(), stale["raw_body"].encode(), hashlib.sha256
    ).hexdigest()
    assert (await _post(app, "/events", stale)).status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_events_are_logged_and_run_automations(app: FastAPI) -> None:
    response = await _post(app, "/events", _delivery({"type": "Issue", "action": "create"}))
    assert response.status_code == 200
    app.state.record.assert_awaited_once()
    payload, delivery_id = app.state.automations.await_args.args
    assert (payload["type"], delivery_id) == ("Issue", "delivery-1")


@pytest.mark.asyncio
async def test_a_delivery_that_is_no_session_turn_starts_no_run(app: FastAPI) -> None:
    response = await _post(app, "/runs", _delivery({"type": "Comment", "action": "create"}))
    assert response.status_code == 200
    assert response.json() == {"run": None}
