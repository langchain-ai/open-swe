from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.utils import langsmith
from openswe.utils.langsmith import parse_langsmith_locator


@pytest.mark.parametrize(
    "locator",
    [
        "run-1",
        "https://evil.example/o/org/projects/p/project/t/thread-1",
        "https://user@smith.langchain.com/o/org/projects/p/project/t/thread-1",
        "ftp://smith.langchain.com/o/org/projects/p/project/t/thread-1",
        "https://smith.langchain.com/o/org/projects/p/project/t/thread-1/extra",
        "https://smith.langchain.com/o/org/projects/p/project/x/thread-1",
    ],
)
def test_parse_langsmith_locator_rejects_invalid_trace_urls(locator: str) -> None:
    assert parse_langsmith_locator(locator) is None


@pytest.mark.parametrize("agent_path", ["", "/a/open-swe-v3"])
@pytest.mark.parametrize("kind", ["t", "r"])
def test_parse_langsmith_locator_accepts_agent_scoped_links(agent_path: str, kind: str) -> None:
    locator = parse_langsmith_locator(
        f"https://smith.langchain.com/o/org{agent_path}/projects/p/project/{kind}/id-1"
        "?start_time=2026-10-25T14%3A44%3A45Z&run_id=run-1&conversationTab=trace"
    )
    assert locator == langsmith.LangSmithLocator(kind="thread" if kind == "t" else "run", id="id-1")


async def test_get_open_swe_thread_id_from_langsmith_resolves_run_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        read_run=AsyncMock(return_value=SimpleNamespace(metadata={"thread_id": "thread-1"}))
    )
    monkeypatch.setattr(langsmith, "_build_langsmith_client", lambda: client)

    result = await langsmith.get_open_swe_thread_id_from_langsmith(
        "11111111-1111-4111-8111-111111111111"
    )

    assert result == "thread-1"
    client.read_run.assert_awaited_once_with("11111111-1111-4111-8111-111111111111")
