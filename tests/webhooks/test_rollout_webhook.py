"""The rollout webhook verifies the signature and acknowledges the delivery."""

import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI

from openswe.rollout_events import router

_SECRET = "test-rollout-webhook-secret"


def _signature(body: bytes, secret: str = _SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("ROLLOUT_WEBHOOK_SECRET", _SECRET)
    api = FastAPI()
    api.include_router(router)
    return api


@pytest.mark.asyncio
async def test_rollout_webhook_acknowledges_a_signed_deploy(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40, "b" * 40, "not-a-sha"]}).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/rollout",
            content=body,
            headers={"X-Rollout-Signature-256": _signature(body)},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "target": "gcp-dev", "commits": 2}


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_bad_signature(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/rollout",
            content=body,
            headers={"X-Rollout-Signature-256": _signature(body, secret="other-secret")},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_when_the_secret_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROLLOUT_WEBHOOK_SECRET", raising=False)
    api = FastAPI()
    api.include_router(router)
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/rollout",
            content=body,
            headers={"X-Rollout-Signature-256": _signature(body)},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_an_empty_commit_list(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": []}).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/rollout",
            content=body,
            headers={"X-Rollout-Signature-256": _signature(body)},
        )
    assert response.status_code == 400
