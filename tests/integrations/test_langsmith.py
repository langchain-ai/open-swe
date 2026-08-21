from contextlib import AbstractContextManager
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langsmith.sandbox import ResourceNotFoundError

from agent.integrations.langsmith import LANGSMITH_WORK_DIR, LangSmithProvider
from agent.utils.sandbox import SandboxGoneError


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")


def _sandbox_client(get_sandbox: AsyncMock) -> AbstractContextManager[MagicMock]:
    client = MagicMock(get_sandbox=get_sandbox)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return cast(
        AbstractContextManager[MagicMock],
        patch("agent.integrations.langsmith.AsyncSandboxClient", return_value=client),
    )


def test_langsmith_is_the_proxy_and_snapshot_provider() -> None:
    provider = LangSmithProvider()

    assert provider.uses_github_proxy is True
    assert provider.supports_snapshots is True


async def test_connect_reports_a_deleted_sandbox_as_gone() -> None:
    get_sandbox = AsyncMock(side_effect=ResourceNotFoundError("Sandbox 'openswe-abc' not found"))

    with _sandbox_client(get_sandbox):
        with pytest.raises(SandboxGoneError, match="openswe-abc"):
            await LangSmithProvider().connect("openswe-abc")


async def test_connect_keeps_other_failures_untyped() -> None:
    with _sandbox_client(AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError) as excinfo:
            await LangSmithProvider().connect("openswe-abc")

    assert not isinstance(excinfo.value, SandboxGoneError)


async def test_connect_wraps_the_sandbox_in_the_timeout_backend() -> None:
    sandbox = MagicMock()
    sandbox.to_sync.return_value = "sync-sandbox"

    with (
        _sandbox_client(AsyncMock(return_value=sandbox)),
        patch(
            "agent.integrations.langsmith.TimeoutLangSmithSandbox",
            MagicMock(return_value="backend"),
        ) as backend_class,
    ):
        backend = await LangSmithProvider().connect("openswe-abc")

    assert backend == "backend"
    backend_class.assert_called_once_with("sync-sandbox")


async def test_work_dir_is_the_snapshot_home() -> None:
    assert await LangSmithProvider().work_dir(MagicMock()) == LANGSMITH_WORK_DIR
    assert LANGSMITH_WORK_DIR == "/workspace"


async def test_a_missing_api_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY_PROD", raising=False)

    with pytest.raises(ValueError, match="LANGSMITH_API_KEY"):
        await LangSmithProvider().connect("openswe-abc")
