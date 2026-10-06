"""The rollout webhook trusts a GitHub Actions OIDC token for an allowed repo."""

import json
import time
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from openswe.rollout_events import router

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_REPO = "langchain-ai/langchainplus"
_DEV = f"{_REPO}/.github/workflows/deploy_build_push_migrate_dev.yaml"
_PROD = f"{_REPO}/.github/workflows/deploy_tag_migrate_prod.yaml"
_WORKFLOWS = f"{_DEV},{_PROD}"
_AUDIENCE = "openswe-rollout"


class _SigningKey:
    def __init__(self) -> None:
        self.key = _PRIVATE.public_key()


class _Keys:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey()


def _token(**overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": _AUDIENCE,
        "sub": f"repo:{_REPO}:ref:refs/heads/main",
        "repository": _REPO,
        "workflow_ref": f"{_DEV}@refs/heads/main",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    encoded = jwt.encode(claims, _PRIVATE, algorithm="RS256")
    return encoded if isinstance(encoded, str) else encoded.decode()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "langchain-ai")
    monkeypatch.setenv("ROLLOUT_OIDC_WORKFLOWS", _WORKFLOWS)
    monkeypatch.setattr("agent.federation.github_oidc._keys", lambda: _Keys())
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("agent.rollout_events.EventLog.record", record)
    api = FastAPI()
    api.state.record = record
    api.include_router(router)
    return api


async def _post(app: FastAPI, body: bytes, token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/webhooks/rollout", content=body, headers=headers)


@pytest.mark.asyncio
async def test_rollout_webhook_acknowledges_a_github_actions_token(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40, "b" * 40, "not-a-sha"]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "target": "gcp-dev", "commits": 2}
    args = app.state.record.await_args
    assert args.args[2] == "deployment"
    assert args.kwargs["event_type"] == "deployed"
    assert args.kwargs["refs"].github_repository == _REPO
    assert json.loads(args.args[1]) == {
        "target": "gcp-dev",
        "channel": "release",
        "commits": ["a" * 40, "b" * 40],
    }


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_token_for_another_audience(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token(aud="https://openswe.langchain.dev"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_repository_that_is_not_allowed(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token(repository="other/repo"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_different_workflow(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(
        app,
        body,
        _token(workflow_ref=f"{_REPO}/.github/workflows/other.yaml@refs/heads/main"),
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_the_same_filename_in_another_repository(
    app: FastAPI,
) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    other = "langchain-ai/scratch/.github/workflows/deploy_build_push_migrate_dev.yaml"
    response = await _post(
        app,
        body,
        _token(repository="langchain-ai/scratch", workflow_ref=f"{other}@refs/heads/main"),
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_accepts_each_listed_workflow(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-us-prod", "commits": ["c" * 40]}).encode()
    response = await _post(app, body, _token(workflow_ref=f"{_PROD}@refs/heads/main"))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_when_no_workflow_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "langchain-ai")
    monkeypatch.delenv("ROLLOUT_OIDC_WORKFLOWS", raising=False)
    monkeypatch.setattr("agent.federation.github_oidc._keys", lambda: _Keys())
    api = FastAPI()
    api.include_router(router)
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(api, body, _token())
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_when_no_organization_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.setattr("agent.federation.github_oidc._keys", lambda: _Keys())
    api = FastAPI()
    api.include_router(router)
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(api, body, _token())
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_non_utf8_body(app: FastAPI) -> None:
    response = await _post(app, b"\xff\xfe\xfa", _token())
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rollout_webhook_stores_a_lowercase_full_sha(app: FastAPI) -> None:
    body = json.dumps({"target": " GCP-Dev ", "commits": [f"  {'A' * 40}  ", "a" * 40]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 200
    assert response.json()["commits"] == 1
    assert json.loads(app.state.record.await_args.args[1])["commits"] == ["a" * 40]


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_short_sha(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 7]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 400
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_an_empty_commit_list(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": []}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rollout_webhook_records_a_development_deploy_separately(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "channel": "dev", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 200
    assert app.state.record.await_args.kwargs["event_type"] == "deployed.dev"


@pytest.mark.asyncio
async def test_rollout_webhook_asks_for_a_retry_when_the_event_is_not_stored(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.record.return_value = False
    monkeypatch.setattr("agent.rollout_events.configured", lambda: True)
    body = json.dumps({"target": "gcp-staging", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 503
