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


async def test_get_open_swe_thread_id_from_langsmith_resolves_run_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(
        runs=SimpleNamespace(
            retrieve=AsyncMock(return_value=SimpleNamespace(metadata={"thread_id": "thread-1"}))
        )
    )
    monkeypatch.setattr(langsmith, "_build_langsmith_client", lambda: client)
    monkeypatch.setattr(langsmith, "_resolve_project_id_by_name", AsyncMock(return_value="proj-1"))

    result = await langsmith.get_open_swe_thread_id_from_langsmith(
        "11111111-1111-4111-8111-111111111111"
    )

    assert result == "thread-1"
    client.runs.retrieve.assert_awaited_once_with(
        "11111111-1111-4111-8111-111111111111", project_id="proj-1", selects=["METADATA", "EXTRA"]
    )


def test_parse_langsmith_locator_keeps_run_url_project_id() -> None:
    locator = parse_langsmith_locator("https://smith.langchain.com/o/org/projects/p/proj-9/r/run-1")

    assert locator == langsmith.LangSmithLocator(kind="run", id="run-1", project_id="proj-9")
