"""Unit tests for GitHub CI read helpers used by the auto-fix flow."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent.utils import github_ci


class _FakeResponse:
    def __init__(self, payload: Any = None, error: bool = False) -> None:
        self._payload = payload if payload is not None else {}
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise httpx.HTTPError("boom")

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    response: _FakeResponse = _FakeResponse({})

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, **_: Any) -> _FakeResponse:
        return type(self).response


def _patch(monkeypatch: pytest.MonkeyPatch, payload: Any, error: bool = False) -> None:
    _FakeClient.response = _FakeResponse(payload, error=error)
    monkeypatch.setattr(github_ci.httpx, "AsyncClient", _FakeClient)


def test_branch_and_sha_from_check_run() -> None:
    payload = {
        "check_run": {
            "head_sha": "deadbeef",
            "check_suite": {"head_branch": "feat/x"},
        }
    }
    assert github_ci.branch_from_check_payload(payload, "check_run") == "feat/x"
    assert github_ci.head_sha_from_check_payload(payload, "check_run") == "deadbeef"


def test_branch_and_sha_from_workflow_run() -> None:
    payload = {"workflow_run": {"head_sha": "abc", "head_branch": "main"}}
    assert github_ci.branch_from_check_payload(payload, "workflow_run") == "main"
    assert github_ci.head_sha_from_check_payload(payload, "workflow_run") == "abc"


def test_sha_from_status_event() -> None:
    payload = {"sha": "sha1", "branches": [{"name": "b1"}]}
    assert github_ci.head_sha_from_check_payload(payload, "status") == "sha1"
    assert github_ci.branch_from_check_payload(payload, "status") == "b1"


def test_is_failing_ci_payload() -> None:
    assert github_ci.is_failing_ci_payload(
        {"check_run": {"status": "completed", "conclusion": "failure"}}, "check_run"
    )
    assert not github_ci.is_failing_ci_payload(
        {"check_run": {"status": "completed", "conclusion": "success"}}, "check_run"
    )
    assert not github_ci.is_failing_ci_payload(
        {"check_run": {"status": "in_progress", "conclusion": None}}, "check_run"
    )
    assert github_ci.is_failing_ci_payload({"state": "failure"}, "status")
    assert not github_ci.is_failing_ci_payload({"state": "pending"}, "status")


@pytest.mark.asyncio
async def test_list_failing_check_runs_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {
            "check_runs": [
                {"name": "lint", "status": "completed", "conclusion": "failure"},
                {"name": "test", "status": "completed", "conclusion": "success"},
                {"name": "build", "status": "in_progress", "conclusion": None},
                {"name": "Open SWE Auto-fix", "status": "completed", "conclusion": "failure"},
            ]
        },
    )
    failing = await github_ci.list_failing_check_runs(owner="o", repo="r", ref="sha", token="t")
    assert failing is not None
    names = {c["name"] for c in failing}
    assert names == {"lint"}


@pytest.mark.asyncio
async def test_list_failing_check_runs_returns_none_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, {}, error=True)
    assert await github_ci.list_failing_check_runs(owner="o", repo="r", ref="s", token="t") is None


@pytest.mark.asyncio
async def test_names_failing_on_base(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both check-runs and statuses calls return the same fake payload here;
    # only the check_runs shape is populated, statuses empty.
    _patch(
        monkeypatch,
        {
            "check_runs": [
                {"name": "flaky", "status": "completed", "conclusion": "failure"},
            ],
            "statuses": [],
        },
    )
    names = await github_ci.names_failing_on_base(owner="o", repo="r", base_sha="base", token="t")
    assert "flaky" in names


@pytest.mark.asyncio
async def test_names_failing_on_base_empty_when_no_base() -> None:
    assert (
        await github_ci.names_failing_on_base(owner="o", repo="r", base_sha="", token="t") == set()
    )
