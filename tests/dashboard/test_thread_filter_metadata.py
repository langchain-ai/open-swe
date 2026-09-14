"""Derived thread metadata, and the filter pushdown it unlocks."""

from types import SimpleNamespace
from typing import Any

import pytest

from agent.dashboard.threads import listing as thread_listing
from agent.dashboard.threads import runs as thread_runs
from agent.utils import thread_filters
from agent.utils.thread_filters import derive_filter_metadata, filter_metadata_is_current
from tests.conftest import patch_thread_module


def test_repo_key_folds_case_and_reads_legacy_metadata() -> None:
    flat = derive_filter_metadata({"repo_owner": "LangChain-AI", "repo_name": "Open-SWE"})
    legacy = derive_filter_metadata({"repo": {"owner": "langchain-ai", "name": "open-swe"}})

    assert flat["repo_key"] == "langchain-ai/open-swe"
    assert legacy["repo_key"] == flat["repo_key"]


def test_threads_without_a_repo_get_an_empty_repo_key() -> None:
    assert derive_filter_metadata({"title": "no repo yet"})["repo_key"] == ""
    assert derive_filter_metadata({"repo_owner": "langchain-ai"})["repo_key"] == ""


@pytest.mark.parametrize(
    "metadata",
    [
        {"thread_category": "automation"},
        {"source": "schedule"},
        {"schedule_id": "s1"},
    ],
)
def test_automation_threads_are_marked(metadata: dict[str, Any]) -> None:
    assert derive_filter_metadata(metadata)["is_automation"] is True


def test_interactive_threads_are_marked_even_when_categorised_otherwise() -> None:
    """``scope=interactive`` means "not an automation", which covers issue and
    pull-request threads too; containment cannot express that as a negation."""
    assert derive_filter_metadata({"thread_category": "pull_request"})["is_automation"] is False


def test_unresolved_is_written_rather_than_left_absent() -> None:
    derived = derive_filter_metadata({"title": "new thread"})

    assert derived["resolved"] is False
    assert filter_metadata_is_current({"title": "new thread", **derived})
    assert not filter_metadata_is_current(
        {**derived, "repo_owner": "langchain-ai", "repo_name": "open-swe"}
    )


def _fake_client(searches: list[dict[str, Any]], threads: list[dict[str, Any]]) -> object:
    class FakeThreads:
        async def search(self, *, metadata, limit, offset, sort_by, sort_order, select):
            searches.append(metadata)
            return [
                thread
                for thread in threads
                if all(thread["metadata"].get(key) == value for key, value in metadata.items())
            ][offset : offset + limit]

    return SimpleNamespace(threads=FakeThreads())


def _thread(thread_id: str, *, repo: str | None = None, resolved: bool = False) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "dashboard",
        "thread_category": "interactive",
        "participant_logins": {"octocat": True},
        "latest_run_status": "success",
        "title": thread_id,
        "resolved": resolved,
    }
    if repo:
        owner, _, name = repo.partition("/")
        metadata.update({"repo_owner": owner, "repo_name": name})
    metadata.update(derive_filter_metadata(metadata))
    return {"thread_id": thread_id, "metadata": metadata}


@pytest.fixture(autouse=True)
def _clear_pushdown_cache() -> None:
    thread_filters.reset_filter_pushdown_cache()


async def test_filters_stay_in_python_until_the_backfill_has_run(monkeypatch) -> None:
    searches: list[dict[str, Any]] = []
    threads = [_thread("t0", repo="langchain-ai/open-swe")]
    monkeypatch.setattr(thread_filters, "get_value", _no_backfill_record)
    monkeypatch.setattr(thread_listing, "langgraph_client", lambda: _fake_client(searches, threads))

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, resolved=False, scope="interactive", repo="langchain-ai/open-swe"
    )

    assert [item["id"] for item in result["items"]] == ["t0"]
    assert all("repo_key" not in search for search in searches)


async def test_backfilled_deployments_filter_inside_the_search(monkeypatch) -> None:
    searches: list[dict[str, Any]] = []
    threads = [
        _thread("t0", repo="langchain-ai/open-swe"),
        _thread("t1", repo="langchain-ai/langgraph"),
        _thread("t2", repo="langchain-ai/open-swe", resolved=True),
    ]
    monkeypatch.setattr(thread_filters, "get_value", _completed_backfill_record)
    monkeypatch.setattr(thread_listing, "langgraph_client", lambda: _fake_client(searches, threads))

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, resolved=False, scope="interactive", repo="LangChain-AI/Open-SWE"
    )

    assert [item["id"] for item in result["items"]] == ["t0"]
    assert searches[0] == {
        "participant_logins": {"octocat": True},
        "resolved": False,
        "is_automation": False,
        "repo_key": "langchain-ai/open-swe",
    }


async def test_ownerless_becomes_an_empty_repo_key(monkeypatch) -> None:
    searches: list[dict[str, Any]] = []
    threads = [_thread("t0")]
    monkeypatch.setattr(thread_filters, "get_value", _completed_backfill_record)
    monkeypatch.setattr(thread_listing, "langgraph_client", lambda: _fake_client(searches, threads))

    result = await thread_listing.list_dashboard_threads_page(
        "octocat", email=None, resolved=False, scope="interactive", ownerless=True
    )

    assert [item["id"] for item in result["items"]] == ["t0"]
    assert searches[0]["repo_key"] == ""


async def test_new_dashboard_threads_carry_the_filter_keys(monkeypatch) -> None:
    created: dict[str, Any] = {}

    class FakeThreads:
        async def create(self, *, thread_id, metadata, if_exists):
            created.update(metadata)
            return {"thread_id": thread_id, "metadata": metadata}

        async def get(self, thread_id):
            return {"thread_id": thread_id, "metadata": created}

    async def fake_profile(_login: str) -> dict[str, Any]:
        return {}

    async def fake_email(_login: str, _profile: dict[str, Any]) -> str:
        return "octo@example.com"

    patch_thread_module(
        monkeypatch, "langgraph_client", lambda: SimpleNamespace(threads=FakeThreads())
    )
    patch_thread_module(monkeypatch, "get_profile", fake_profile)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_email)

    await thread_runs._create_dashboard_thread_record(
        "thread-id",
        login="octocat",
        repo_config={"owner": "LangChain-AI", "name": "Open-SWE"},
        prompt="do work",
    )

    assert created["repo_key"] == "langchain-ai/open-swe"
    assert created["is_automation"] is False
    assert created["resolved"] is False


async def _no_backfill_record(_namespace: object, _key: str) -> dict[str, Any] | None:
    return None


async def _completed_backfill_record(_namespace: object, _key: str) -> dict[str, Any]:
    return {"completed_at_ms": 1, "threads_scanned": 1, "threads_updated": 0}
