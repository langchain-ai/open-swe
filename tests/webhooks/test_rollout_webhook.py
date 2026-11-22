"""The rollout webhook trusts only Robin's bot: a LangSmith service key from its workspace."""

import json
from unittest.mock import DEFAULT, AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from openswe.dashboard.langsmith_api_keys import LANGSMITH_API_KEYS, LangSmithApiKey
from openswe.rollout_events import router

_REPO = "langchain-ai/langchainplus"
_BOT = "lsv2_sk_bot"
_PAT = "lsv2_pt_ada"


class _Rollout:
    """Robin's bot key, a person's key, and the deploy notices they send."""

    @staticmethod
    async def verify(token: str) -> LangSmithApiKey | None:
        if token == _BOT:
            return LangSmithApiKey(
                kind="service",
                fingerprint="bot",
                organization_id="org",
                workspace_id="ws",
                audit_id="langsmith:bot",
            )
        if token == _PAT:
            return LangSmithApiKey(
                kind="personal",
                fingerprint="ada",
                organization_id="org",
                workspace_id="ws",
                audit_id="langsmith:ada",
                ls_user_id="ls-ada",
            )
        if token.startswith("lsv2_"):
            raise HTTPException(401, "invalid LangSmith API key")
        return None

    @staticmethod
    def event(target: str, commits: list[str], **extra: object) -> bytes:
        return json.dumps(
            {"repository": _REPO, "target": target, "commits": commits, **extra}
        ).encode()


@pytest.fixture
async def app(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(LANGSMITH_API_KEYS, "verify", _Rollout.verify)
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
async def test_rollout_webhook_acknowledges_robins_bot(app: FastAPI) -> None:
    body = _Rollout.event("gcp-dev", ["a" * 40, "b" * 40, "not-a-sha"])
    response = await _post(app, body, _BOT)
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


@pytest.mark.parametrize("token", [None, _PAT, "lsv2_sk_elsewhere", "not-a-langsmith-key"])
@pytest.mark.asyncio
async def test_rollout_webhook_rejects_anything_but_the_bot(
    app: FastAPI, token: str | None
) -> None:
    response = await _post(app, _Rollout.event("gcp-dev", ["a" * 40]), token)
    assert response.status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.parametrize("repository", [None, "", "langchainplus", "a/b/c"])
@pytest.mark.asyncio
async def test_rollout_webhook_requires_a_repository(app: FastAPI, repository: str | None) -> None:
    payload: dict[str, object] = {"target": "gcp-dev", "commits": ["a" * 40]}
    if repository is not None:
        payload["repository"] = repository
    response = await _post(app, json.dumps(payload).encode(), _BOT)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_non_utf8_body(app: FastAPI) -> None:
    response = await _post(app, b"\xff\xfe\xfa", _BOT)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rollout_webhook_stores_a_lowercase_full_sha(app: FastAPI) -> None:
    body = _Rollout.event(" GCP-Dev ", [f"  {'A' * 40}  ", "a" * 40])
    response = await _post(app, body, _BOT)
    assert response.status_code == 200
    assert response.json()["commits"] == 1
    assert json.loads(app.state.record.await_args.args[1])["commits"] == ["a" * 40]


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_a_short_sha(app: FastAPI) -> None:
    body = _Rollout.event("gcp-dev", ["a" * 7])
    response = await _post(app, body, _BOT)
    assert response.status_code == 400
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollout_webhook_rejects_an_empty_commit_list(app: FastAPI) -> None:
    body = _Rollout.event("gcp-dev", [])
    response = await _post(app, body, _BOT)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rollout_webhook_stores_target_and_commits_without_a_channel(app: FastAPI) -> None:
    body = _Rollout.event("gcp-staging", ["a" * 40], channel="dev")
    response = await _post(app, body, _BOT)
    assert response.status_code == 200
    recorded = app.state.record.await_args
    assert recorded.kwargs["event_type"] == "deployed"
    assert json.loads(recorded.args[1]) == {"target": "gcp-staging", "commits": ["a" * 40]}


@pytest.mark.asyncio
async def test_rollout_webhook_asks_for_a_retry_when_the_event_is_not_stored(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.record.return_value = False
    body = _Rollout.event("gcp-staging", ["a" * 40])
    response = await _post(app, body, _BOT)
    assert response.status_code == 503
    app.state.record.return_value = DEFAULT
    assert (await _post(app, body, _BOT)).status_code == 200
    assert (await _post(app, body, _BOT)).status_code == 200
    assert app.state.record.await_count == 3


class TestRolloutRetries:
    async def test_reordered_normalized_commits_are_recorded_once(self, app: FastAPI) -> None:
        for target, commits in [
            (" GCP-Dev ", ["A" * 40, "b" * 40, "a" * 40]),
            ("gcp-dev", ["b" * 40, "a" * 40]),
        ]:
            body = _Rollout.event(target, commits)
            assert (await _post(app, body, _BOT)).status_code == 200
        from sqlalchemy import text

        from openswe.database import transaction

        async with transaction() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM event_log")) == 1

    async def test_distinct_repositories_targets_and_commits_are_recorded(
        self, app: FastAPI
    ) -> None:
        for repository, target, commit in [
            (_REPO, "gcp-dev", "a" * 40),
            ("other/repo", "gcp-dev", "a" * 40),
            (_REPO, "gcp-us-prod", "a" * 40),
            (_REPO, "gcp-dev", "b" * 40),
        ]:
            body = _Rollout.event(target, [commit], repository=repository)
            assert (await _post(app, body, _BOT)).status_code == 200
        assert app.state.record.await_count == 4
