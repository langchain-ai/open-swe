from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agent.github.pull_request_checks as checks_module
from agent.github.pull_request_checks import get_pull_request_check_states
from agent.github.pull_requests import PullRequest, PullRequestCheck
from agent.github.repositories import Repository


def _rollup(state: str | None, pr_state: str = "OPEN", is_draft: bool = False) -> dict[str, object]:
    return {
        "pullRequest": {
            "state": pr_state,
            "isDraft": is_draft,
            "commits": {
                "nodes": [{"commit": {"statusCheckRollup": {"state": state} if state else None}}]
            },
        }
    }


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _patch_github(monkeypatch, payload, calls: list[dict[str, object]]):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    def client(**_kwargs):
        return _Client()

    async def request(_client, _method, _url, *, json, **_kwargs):
        calls.append(json)
        return _Response(payload)

    monkeypatch.setattr(checks_module, "github_client", client)
    monkeypatch.setattr(checks_module, "github_request", request)


@pytest.fixture(autouse=True)
def write_backs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, int, str]]:
    scheduled: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr(
        checks_module,
        "schedule_pull_request_sync",
        lambda owner, repo, number, *, token: scheduled.append((owner, repo, number, token)),
    )
    return scheduled


@pytest.fixture(autouse=True)
def stored_rows(monkeypatch: pytest.MonkeyPatch) -> list[PullRequest]:
    """Stand in for the pull request table; empty unless a test fills it."""
    rows: list[PullRequest] = []
    checks_module._cache.clear()
    monkeypatch.setattr(checks_module.postgres, "configured", lambda: True)
    monkeypatch.setattr(PullRequest, "get_all", AsyncMock(return_value=rows))
    monkeypatch.setattr(checks_module, "read_cached_repos", AsyncMock(return_value=None))
    return rows


def _stored_row(
    *,
    private: bool | None = False,
    age: timedelta = timedelta(0),
    failing: bool = True,
) -> PullRequest:
    row = PullRequest(owner="acme", repo="alpha", number=1, state="open", head_sha="a" * 40)
    row.last_synced_at = datetime.now(UTC) - age
    row.repository = Repository(full_name="acme/alpha", private=private)
    row.checks = (
        [
            PullRequestCheck(
                head_sha="a" * 40,
                kind="check_run",
                external_id="1",
                name="unit",
                status="completed",
                conclusion="failure",
            )
        ]
        if failing
        else []
    )
    return row


def _cached_repos(*full_names: str) -> AsyncMock:
    payload: dict[str, Any] = {
        "repositories": [{"full_name": name, "private": True} for name in full_names]
    }
    return AsyncMock(return_value=(payload, 0))


async def test_maps_pull_request_state_and_skips_invalid_records(monkeypatch):
    calls: list[dict[str, object]] = []
    _patch_github(
        monkeypatch,
        {
            "data": {
                "p0": _rollup("FAILURE"),
                "p1": _rollup(None),
                "p2": _rollup("SUCCESS", pr_state="MERGED"),
                "p3": _rollup("PENDING", is_draft=True),
                "p4": _rollup(None, pr_state="CLOSED"),
            }
        },
        calls,
    )

    result = await get_pull_request_check_states(
        [
            {"repoFullName": "acme/alpha", "number": 1},
            {"repoFullName": "acme/beta", "number": 2},
            {"repoFullName": "acme/gamma", "number": 3},
            {"repoFullName": "acme/delta", "number": 4},
            {"repoFullName": "acme/epsilon", "number": 5},
            {"repoFullName": "acme/../etc", "number": 6},
            {"repoFullName": "no-slash", "number": 7},
        ],
        "octocat",
        "token",
    )

    # A merged or closed PR must stop reading as open — the sidebar renders from this.
    assert result == {
        "acme/alpha#1": {"checks": "failing", "state": "open"},
        "acme/beta#2": {"checks": "passing", "state": "open"},
        "acme/gamma#3": {"checks": "passing", "state": "merged"},
        "acme/delta#4": {"checks": "pending", "state": "draft"},
        "acme/epsilon#5": {"checks": "passing", "state": "closed"},
    }
    # Invalid identities never reach GitHub.
    assert len(calls) == 1
    assert "etc" not in str(calls[0]["variables"])


async def test_caches_per_login(monkeypatch):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {"p0": _rollup("FAILURE")}}, calls)
    record = [{"repoFullName": "acme/alpha", "number": 1}]

    expected = {"acme/alpha#1": {"checks": "failing", "state": "open"}}
    assert await get_pull_request_check_states(record, "octocat", "token") == expected
    assert await get_pull_request_check_states(record, "octocat", "token") == expected
    assert len(calls) == 1

    await get_pull_request_check_states(record, "someone-else", "token")
    assert len(calls) == 2


async def test_returns_unknown_when_github_fails(monkeypatch):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"errors": [{"message": "nope"}]}, calls)

    result = await get_pull_request_check_states(
        [{"repoFullName": "acme/alpha", "number": 1}], "octocat", "token"
    )

    assert result == {"acme/alpha#1": {"checks": "unknown", "state": None}}
    assert not checks_module._cache


async def test_fresh_public_row_is_served_without_touching_github(
    monkeypatch, stored_rows: list[PullRequest], write_backs
):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {}}, calls)
    stored_rows.append(_stored_row())

    result = await get_pull_request_check_states(
        [{"repoFullName": "acme/alpha", "number": 1}], "octocat", "token"
    )

    assert result == {"acme/alpha#1": {"checks": "failing", "state": "open"}}
    assert calls == []
    assert write_backs == []


async def test_private_row_outside_the_callers_repo_cache_falls_through_to_github(
    monkeypatch, stored_rows: list[PullRequest], write_backs
):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {"p0": _rollup("SUCCESS")}}, calls)
    stored_rows.append(_stored_row(private=True))
    monkeypatch.setattr(checks_module, "read_cached_repos", _cached_repos("acme/other"))

    result = await get_pull_request_check_states(
        [{"repoFullName": "acme/alpha", "number": 1}], "intruder", "token"
    )

    # The stored row says failing; GitHub, asked with the caller's token, says passing.
    assert result == {"acme/alpha#1": {"checks": "passing", "state": "open"}}
    assert len(calls) == 1


async def test_private_row_inside_the_callers_repo_cache_is_served(
    monkeypatch, stored_rows: list[PullRequest]
):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {}}, calls)
    stored_rows.append(_stored_row(private=True))
    monkeypatch.setattr(checks_module, "read_cached_repos", _cached_repos("Acme/Alpha"))

    result = await get_pull_request_check_states(
        [{"repoFullName": "acme/alpha", "number": 1}], "octocat", "token"
    )

    assert result == {"acme/alpha#1": {"checks": "failing", "state": "open"}}
    assert calls == []


async def test_stale_row_falls_back_to_github_and_schedules_a_write_back(
    monkeypatch, stored_rows: list[PullRequest], write_backs
):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {"p0": _rollup("SUCCESS")}}, calls)
    stored_rows.append(_stored_row(age=timedelta(hours=2)))

    result = await get_pull_request_check_states(
        [{"repoFullName": "acme/alpha", "number": 1}], "octocat", "token"
    )

    assert result == {"acme/alpha#1": {"checks": "passing", "state": "open"}}
    assert len(calls) == 1
    assert write_backs == [("acme", "alpha", 1, "token")]


async def test_write_back_is_skipped_when_too_many_rows_are_missing(monkeypatch, write_backs):
    calls: list[dict[str, object]] = []
    _patch_github(monkeypatch, {"data": {}}, calls)
    records = [{"repoFullName": f"acme/repo{index}", "number": 1} for index in range(11)]

    await get_pull_request_check_states(records, "octocat", "token")

    assert write_backs == []
