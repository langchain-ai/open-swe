from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from openswe.threads import runtimes


@pytest.mark.parametrize("assistant_id", ["agent", "reviewer", "review-scout"])
@pytest.mark.parametrize("commit", ["backend-source-sha", None])
@pytest.mark.parametrize("revision", ["historical-revision", None])
async def test_mda_run_uses_serving_backend_provenance(
    monkeypatch: pytest.MonkeyPatch,
    assistant_id: str,
    commit: str | None,
    revision: str | None,
) -> None:
    monkeypatch.setattr(runtimes, "backend_build_info", lambda: {"commit": commit}, raising=False)
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", "backend-deployment-version")
    monkeypatch.setattr(runtimes, "remote_run_context", AsyncMock(return_value={}))
    metadata: dict[str, JsonValue] = {
        "scout_head_sha": "pull-request-head",
        "event_match_ids": ["event-1"],
        "LANGSMITH_AGENT_VERSION": "previous-deployment-version",
    }
    if revision:
        metadata["revision_id"] = revision
    payload: dict[str, JsonValue] = {"assistant_id": assistant_id, "metadata": metadata}
    original_metadata = dict(metadata)

    rewritten = await runtimes.MdaRuntime("https://mda.test").run_payload("thread-1", payload)

    expected = {**metadata, "LANGSMITH_AGENT_VERSION": "backend-deployment-version"}
    if commit:
        expected["revision_id"] = commit
    assert rewritten["metadata"] == expected
    assert metadata == original_metadata


async def test_mda_run_without_metadata_or_build_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes, "backend_build_info", lambda: {"commit": None}, raising=False)
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    monkeypatch.setattr(runtimes, "remote_run_context", AsyncMock(return_value={}))

    rewritten = await runtimes.MdaRuntime("https://mda.test").run_payload(
        "thread-1", {"assistant_id": "review-scout"}
    )

    assert rewritten["metadata"] == {}
