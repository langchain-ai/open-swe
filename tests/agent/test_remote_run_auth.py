from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from openswe.agent_api import client, hosted
from openswe.agent_api.run_catalog import RunCatalog
from openswe.threads.identity import ThreadIdentity


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["dashboard", "slack", "web"])
async def test_human_github_runs_require_langsmith_before_catalog_resolution(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    authorize = AsyncMock(side_effect=HTTPException(401, "Sign in with LangSmith"))
    monkeypatch.setattr(ThreadIdentity, "authorize", authorize)
    catalog = AsyncMock()
    monkeypatch.setattr(RunCatalog, "of", catalog)

    with pytest.raises(HTTPException) as exc:
        await client.start_remote_run(
            {"github_login": "alice", "source": source},
            metadata={},
            thread_id="thread-1",
            assistant_id="agent",
        )

    assert exc.value.status_code == 401
    authorize.assert_awaited_once()
    catalog.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["github", "linear", "schedule", None])
async def test_external_runs_keep_their_existing_authorization_policy(
    monkeypatch: pytest.MonkeyPatch,
    source: str | None,
) -> None:
    authorize = AsyncMock(side_effect=AssertionError("System runs are not human sign-ins"))
    monkeypatch.setattr(ThreadIdentity, "authorize", authorize)
    resolved = SimpleNamespace(settings={}, catalog=Mock())
    catalog = AsyncMock(return_value=SimpleNamespace(start=AsyncMock(return_value=resolved)))
    monkeypatch.setattr(RunCatalog, "of", catalog)
    monkeypatch.setattr(hosted, "run_kinds", lambda: {"agent": Mock()})
    monkeypatch.setattr(client, "sign_runtime_token", Mock(return_value="signed-runtime-token"))

    started = await client.start_remote_run(
        {"github_login": "alice", "source": source},
        metadata={},
        thread_id="thread-1",
        assistant_id="agent",
    )

    assert started.token == "signed-runtime-token"
    assert started.catalog is resolved.catalog
    authorize.assert_not_awaited()
    catalog.assert_awaited_once()
