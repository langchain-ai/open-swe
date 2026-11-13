"""The rollout webhook trusts a GitHub Actions OIDC token for an allowed repo."""

import json
import time
from unittest.mock import DEFAULT, AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from openswe.rollout_events import router

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_REPO = "langchain-ai/langchainplus"
_DEV = f"{_REPO}/.github/workflows/deploy_build_push_migrate_dev.yaml"
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
def app(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    async def allowed(full_name: str) -> str | None:
        return "default" if full_name == _REPO else None

    monkeypatch.setattr("openswe.federation.github_oidc._keys", lambda: _Keys())
    monkeypatch.setattr("openswe.rollout_events.WORKSPACES.thread_starter_of_repo", allowed)
    from openswe.webhooks.event_log import EventLog

    record = AsyncMock(wraps=EventLog.record)
    monkeypatch.setattr("openswe.rollout_events.EventLog.record", record)
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
        "commits": ["a" * 40, "b" * 40],
    }


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_token_for_another_audience(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token(aud="https://openswe.langchain.dev"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_repository_that_may_not_start_threads(
    app: FastAPI,
) -> None:
    body = json.dumps({"target": "gcp-dev", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token(repository="other/repo"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollout_webhook_accepts_another_workflow_in_a_granted_repository(
    app: FastAPI,
) -> None:
    body = json.dumps({"target": "gcp-us-prod", "commits": ["c" * 40]}).encode()
    response = await _post(
        app,
        body,
        _token(workflow_ref=f"{_REPO}/.github/workflows/other.yaml@refs/heads/main"),
    )
    assert response.status_code == 200


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
async def test_rollout_webhook_stores_target_and_commits_without_a_channel(app: FastAPI) -> None:
    body = json.dumps({"target": "gcp-staging", "channel": "dev", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 200
    recorded = app.state.record.await_args
    assert recorded.kwargs["event_type"] == "deployed"
    assert json.loads(recorded.args[1]) == {"target": "gcp-staging", "commits": ["a" * 40]}


@pytest.mark.asyncio
async def test_rollout_webhook_asks_for_a_retry_when_the_event_is_not_stored(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.record.return_value = False
    body = json.dumps({"target": "gcp-staging", "commits": ["a" * 40]}).encode()
    response = await _post(app, body, _token())
    assert response.status_code == 503
    app.state.record.return_value = DEFAULT
    assert (await _post(app, body, _token())).status_code == 200
    assert (await _post(app, body, _token())).status_code == 200
    assert app.state.record.await_count == 3


class TestRolloutRetries:
    async def test_reordered_normalized_commits_are_recorded_once(self, app: FastAPI) -> None:
        for target, commits in [
            (" GCP-Dev ", ["A" * 40, "b" * 40, "a" * 40]),
            ("gcp-dev", ["b" * 40, "a" * 40]),
        ]:
            body = json.dumps({"target": target, "commits": commits}).encode()
            assert (await _post(app, body, _token())).status_code == 200
        from sqlalchemy import text

        from openswe.database import transaction

        async with transaction() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM event_log")) == 1

    async def test_distinct_repositories_targets_and_commits_are_recorded(
        self, app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "openswe.rollout_events.WORKSPACES.thread_starter_of_repo",
            AsyncMock(return_value="default"),
        )
        for repository, target, commit in [
            (_REPO, "gcp-dev", "a" * 40),
            ("other/repo", "gcp-dev", "a" * 40),
            (_REPO, "gcp-us-prod", "a" * 40),
            (_REPO, "gcp-dev", "b" * 40),
        ]:
            body = json.dumps({"target": target, "commits": [commit]}).encode()
            assert (await _post(app, body, _token(repository=repository))).status_code == 200
        assert app.state.record.await_count == 4
