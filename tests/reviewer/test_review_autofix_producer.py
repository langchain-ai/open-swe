"""Focused contract tests for the review-publish auto-fix producer."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.review.findings import Finding, Severity, new_finding
from agent.review.publish import render_inline_comment_body

_THREAD_ID = "123e4567-e89b-12d3-a456-426614174000"
_BRANCH = f"open-swe/{_THREAD_ID}-fix-review"
_SLUG_BRANCH = "open-swe/fix-review"
_PENDING_KEY = (("autofix", _THREAD_ID), "pending_event")


class _Store:
    def __init__(self, *, enabled: bool = True, threshold: str = "medium") -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.cycle_count = 0
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
        self.puts: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []
        self.deleted: list[tuple[tuple[str, ...], str]] = []
        self.fail_put_namespace: tuple[str, ...] | None = None

    async def aput(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        if namespace == self.fail_put_namespace:
            raise RuntimeError("store unavailable")
        self.items[(namespace, key)] = value
        self.puts.append((namespace, key, value))

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self.items.pop((namespace, key), None)
        self.deleted.append((namespace, key))

    async def get_cycle_count(self, owner: str, repo: str, pr_number: int) -> int:
        return self.cycle_count

    async def set_cycle_count(
        self, owner: str, repo: str, pr_number: int, cycle_count: int
    ) -> None:
        self.cycle_count = cycle_count


def _finding(*, severity: Severity = "high") -> Finding:
    return new_finding(
        severity=severity,
        confidence="high",
        category="correctness",
        file="src/foo.py",
        start_line=10,
        end_line=10,
        title="Broken guard",
        description="boom",
        suggestion="return early",
        sha="head-sha",
        finding_id="finding-1",
    )


def test_review_autofix_finding_detail_handles_legacy_title() -> None:
    from agent.tools.publish_review import _review_autofix_finding_detail

    finding = _finding()
    finding.pop("title")
    assert _review_autofix_finding_detail(finding) == (
        "[HIGH] src/foo.py:10 — Code review finding: boom"
    )


def _client(*, branch_name: str = _BRANCH, search_result: bool = True) -> MagicMock:
    client = MagicMock()
    thread = {
        "thread_id": _THREAD_ID,
        "metadata": {
            "branch_name": branch_name,
            "source": "linear",
            "github_login": "octocat",
            "triggering_user_email": "octocat@example.com",
            "source_context": {
                "linear_issue": {
                    "linear_project_id": "OSWE",
                    "linear_issue_number": "56",
                }
            },
        },
    }
    client.threads.search = AsyncMock(return_value=[thread] if search_result else [])
    client.threads.get = AsyncMock(return_value=thread)
    return client


@pytest.fixture(autouse=True)
def _autofix_profile_enabled() -> Iterator[None]:
    with patch(
        "agent.tools.publish_review.load_profile",
        AsyncMock(return_value={"auto_fix_ci": True}),
    ):
        yield


@pytest.fixture(autouse=True)
def _pr_head_verified() -> Iterator[None]:
    with patch(
        "agent.tools.publish_review._verify_pr_head_is_local_branch",
        AsyncMock(return_value=None),
    ):
        yield


@contextmanager
def _autofix_dependencies(store: _Store) -> Iterator[None]:
    with (
        patch(
            "agent.tools.publish_review.get_team_autofix_settings",
            AsyncMock(return_value=(store.enabled, store.threshold)),
        ),
        patch(
            "agent.tools.publish_review.get_pr_autofix_cycle_count",
            AsyncMock(side_effect=store.get_cycle_count),
        ),
        patch(
            "agent.tools.publish_review.set_pr_autofix_cycle_count",
            AsyncMock(side_effect=store.set_cycle_count),
        ),
    ):
        yield


@pytest.mark.parametrize(
    ("case", "enabled", "threshold", "severity", "repo_enabled", "pr_disabled", "findings"),
    [
        ("autofix-disabled", False, "medium", "high", True, False, 1),
        ("below-threshold", True, "high", "medium", True, False, 1),
        ("repo-not-enrolled", True, "medium", "high", False, False, 1),
        ("pr-opted-out", True, "medium", "high", True, True, 1),
        ("zero-newly-surfaced", True, "medium", "high", True, False, 0),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
async def test_review_autofix_trigger_truth_table_false_cases(
    case: str,
    enabled: bool,
    threshold: str,
    severity: Severity,
    repo_enabled: bool,
    pr_disabled: bool,
    findings: int,
) -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    del case
    store = _Store(enabled=enabled, threshold=threshold)
    dispatch = AsyncMock()
    status = AsyncMock()
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=repo_enabled),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=pr_disabled),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding(severity=severity)] if findings else [],
        )

    assert _PENDING_KEY not in store.items
    assert store.cycle_count == 0
    dispatch.assert_not_awaited()
    status.assert_not_awaited()


async def test_review_autofix_profile_disabled_skips_dispatch() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    store = _Store()
    client = _client()
    dispatch = AsyncMock()
    load_profile = AsyncMock(return_value={"auto_fix_ci": False})
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=client),
        patch("agent.tools.publish_review.load_profile", load_profile),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    load_profile.assert_awaited_once_with("octocat")
    assert _PENDING_KEY not in store.items
    assert store.cycle_count == 0
    dispatch.assert_not_awaited()


async def test_review_autofix_slug_branch_enqueues_event_and_dispatches_minimal_nudge() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    store = _Store()
    client = _client(branch_name=_SLUG_BRANCH)
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    status = AsyncMock(return_value=True)
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=client),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_SLUG_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    client.threads.search.assert_awaited_once_with(
        metadata={"branch_name": _SLUG_BRANCH},
        limit=10,
    )
    client.threads.get.assert_not_awaited()
    assert store.items[_PENDING_KEY] == {
        "reason": "Open SWE Review surfaced findings for auto-fix.",
        "details": [
            "PR: https://github.com/o/r/pull/7",
            "Head SHA: head-sha",
            "[HIGH] src/foo.py:10 — Broken guard: boom",
        ],
    }
    assert store.cycle_count == 1
    dispatch.assert_awaited_once_with(
        _THREAD_ID,
        "Process the pending Open SWE review auto-fix event for this pull request.",
        {
            "thread_id": _THREAD_ID,
            "source": "linear",
            "repo": {"owner": "o", "name": "r"},
            "pr_number": 7,
            "pr_url": "https://github.com/o/r/pull/7",
            "head_sha": "head-sha",
            "github_login": "octocat",
            "user_email": "octocat@example.com",
            "linear_issue": {
                "linear_project_id": "OSWE",
                "linear_issue_number": "56",
            },
        },
        source="review_autofix",
        client=client,
    )
    dispatch_args = dispatch.await_args
    assert dispatch_args is not None
    assert "boom" not in dispatch_args.args[1]
    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix cycle 1 dispatched"


async def test_review_autofix_cycle_cap_stops_third_publish_and_survives_opt_out() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    store = _Store()
    dispatch = AsyncMock(return_value={"run_id": "run-1"})
    status = AsyncMock(return_value=True)
    pr_disabled = AsyncMock(side_effect=[False, False, True, False])
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch("agent.tools.publish_review.is_pr_autofix_disabled", pr_disabled),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        for _ in range(4):
            await _maybe_dispatch_review_autofix(
                owner="o",
                repo="r",
                pr_number=7,
                head_sha="head-sha",
                branch_name=_BRANCH,
                token="token",
                surfaced_findings=[_finding()],
            )

    assert dispatch.await_count == 2
    assert store.cycle_count == 2
    assert [call.kwargs["title"] for call in status.await_args_list] == [
        "Auto-fix cycle 1 dispatched",
        "Auto-fix cycle 2 dispatched",
        "Auto-fix cycle limit reached",
    ]


@pytest.mark.parametrize("failure", ["thread-resolution", "store-write"])
async def test_review_autofix_producer_errors_are_neutral_and_do_not_raise(failure: str) -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    store = _Store()
    if failure == "store-write":
        store.fail_put_namespace = ("autofix", _THREAD_ID)
    dispatch = AsyncMock()
    status = AsyncMock(return_value=True)
    client = _client(search_result=failure != "thread-resolution")
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=client),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name="open-swe/no-thread-id" if failure == "thread-resolution" else _BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix dispatch failed"


async def test_review_autofix_cycle_read_failure_skips_dispatch() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    dispatch = AsyncMock()
    status = AsyncMock(return_value=True)
    with (
        patch(
            "agent.tools.publish_review.get_team_autofix_settings",
            AsyncMock(return_value=(True, "medium")),
        ),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch(
            "agent.tools.publish_review.get_pr_autofix_cycle_count",
            AsyncMock(side_effect=RuntimeError("store unavailable")),
        ),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    dispatch.assert_not_awaited()
    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix dispatch failed"
    assert status_args.kwargs["summary"].endswith(": RuntimeError")


# Captured before the autouse fixture patches the module attribute.
from agent.tools.publish_review import (  # noqa: E402
    _verify_pr_head_is_local_branch as _real_verify_pr_head,
)


def _pr_payload(full_name: str, ref: str) -> dict[str, Any]:
    return {"head": {"repo": {"full_name": full_name}, "ref": ref}}


def _patched_pr_fetch(payload: dict[str, Any]):  # noqa: ANN202
    from contextlib import asynccontextmanager

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)

    @asynccontextmanager
    async def fake_client(**_kwargs: Any):  # noqa: ANN202
        yield MagicMock()

    return (
        patch("agent.utils.github_http.github_client", fake_client),
        patch("agent.utils.github_http.github_request", AsyncMock(return_value=response)),
    )


async def test_verify_pr_head_accepts_local_branch() -> None:
    client_patch, request_patch = _patched_pr_fetch(_pr_payload("o/r", _BRANCH))
    with client_patch, request_patch:
        await _real_verify_pr_head(owner="o", repo="r", pr_number=7, branch_name=_BRANCH, token="t")


async def test_verify_pr_head_rejects_fork_head() -> None:
    # A fork can carry any branch name, including a copy of a victim thread's.
    client_patch, request_patch = _patched_pr_fetch(_pr_payload("attacker/fork", _BRANCH))
    with client_patch, request_patch:
        with pytest.raises(RuntimeError, match="fork-headed"):
            await _real_verify_pr_head(
                owner="o", repo="r", pr_number=7, branch_name=_BRANCH, token="t"
            )


async def test_verify_pr_head_rejects_mismatched_ref() -> None:
    client_patch, request_patch = _patched_pr_fetch(_pr_payload("o/r", "other-branch"))
    with client_patch, request_patch:
        with pytest.raises(RuntimeError, match="does not match branch"):
            await _real_verify_pr_head(
                owner="o", repo="r", pr_number=7, branch_name=_BRANCH, token="t"
            )


async def test_review_autofix_unverifiable_pr_head_skips_dispatch() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    dispatch = AsyncMock()
    status = AsyncMock(return_value=True)
    with (
        patch(
            "agent.tools.publish_review.get_team_autofix_settings",
            AsyncMock(return_value=(True, "medium")),
        ),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch(
            "agent.tools.publish_review._verify_pr_head_is_local_branch",
            AsyncMock(side_effect=RuntimeError("head repo mismatch")),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    dispatch.assert_not_awaited()
    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix dispatch failed"


def test_thread_match_rejects_forged_and_foreign_threads() -> None:
    from agent.tools.publish_review import _thread_matches_review_pr

    matching = {"metadata": {"branch_name": _BRANCH, "source": "linear"}}
    assert _thread_matches_review_pr(matching, "o", "r", _BRANCH)

    wrong_branch = {"metadata": {"branch_name": "open-swe/other", "source": "linear"}}
    assert not _thread_matches_review_pr(wrong_branch, "o", "r", _BRANCH)

    reviewer = {"metadata": {"branch_name": _BRANCH, "kind": "reviewer"}}
    assert not _thread_matches_review_pr(reviewer, "o", "r", _BRANCH)

    foreign_repo = {
        "metadata": {"branch_name": _BRANCH, "repo": {"owner": "other", "name": "repo"}}
    }
    assert not _thread_matches_review_pr(foreign_repo, "o", "r", _BRANCH)

    same_repo = {"metadata": {"branch_name": _BRANCH, "repo": {"owner": "O", "name": "R"}}}
    assert _thread_matches_review_pr(same_repo, "o", "r", _BRANCH)

    assert not _thread_matches_review_pr({"metadata": None}, "o", "r", _BRANCH)


async def test_review_autofix_rejects_thread_that_does_not_match_branch() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    # The PR branch embeds a valid thread UUID, but that thread's own metadata
    # names a different branch: a forged branch must not select a dispatch target.
    dispatch = AsyncMock()
    status = AsyncMock(return_value=True)
    with (
        patch(
            "agent.tools.publish_review.get_team_autofix_settings",
            AsyncMock(return_value=(True, "medium")),
        ),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch(
            "agent.tools.publish_review.dispatch_client",
            return_value=_client(branch_name="open-swe/some-other-branch"),
        ),
        patch(
            "agent.tools.publish_review.get_pr_autofix_cycle_count",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    dispatch.assert_not_awaited()
    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix dispatch failed"


async def test_review_autofix_optout_read_failure_skips_dispatch() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    dispatch = AsyncMock()
    status = AsyncMock(return_value=True)
    with (
        patch(
            "agent.tools.publish_review.get_team_autofix_settings",
            AsyncMock(return_value=(True, "medium")),
        ),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(side_effect=RuntimeError("store unavailable")),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    # An unreadable opt-out record must abort dispatch, not read as opted-in.
    dispatch.assert_not_awaited()
    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix dispatch failed"


async def test_review_autofix_dispatch_failure_clears_pending_event() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    store = _Store()
    store.cycle_count = 1
    status = AsyncMock(return_value=True)
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch(
            "agent.tools.publish_review.dispatch_agent_run",
            AsyncMock(side_effect=RuntimeError("dispatch unavailable")),
        ),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    assert _PENDING_KEY not in store.items
    assert store.deleted == [_PENDING_KEY]
    assert store.cycle_count == 1
    status.assert_awaited_once()
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["summary"] == (
        "Open SWE could not dispatch auto-fix for https://github.com/o/r/pull/7: RuntimeError"
    )


async def test_review_autofix_dispatch_cancellation_clears_pending_event() -> None:
    from agent.tools.publish_review import _maybe_dispatch_review_autofix

    store = _Store()
    status = AsyncMock(return_value=True)
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch(
            "agent.tools.publish_review.dispatch_agent_run",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
        pytest.raises(asyncio.CancelledError),
    ):
        await _maybe_dispatch_review_autofix(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            branch_name=_BRANCH,
            token="token",
            surfaced_findings=[_finding()],
        )

    assert _PENDING_KEY not in store.items
    assert store.deleted == [_PENDING_KEY]
    assert store.cycle_count == 0
    status.assert_not_awaited()


async def test_publish_review_config_off_preserves_literal_outcome() -> None:
    from agent.tools.publish_review import _publish_review_async

    finding = _finding()
    store = _Store(enabled=False)
    post_review = AsyncMock(return_value={"id": 555})
    dispatch = AsyncMock()
    status = AsyncMock()
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="reviewer"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=[finding]),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch("agent.tools.publish_review._record_review_publication", AsyncMock()),
        patch(
            "agent.tools.publish_review._missing_comment_ids_for_published_findings",
            return_value=False,
        ),
        patch("agent.tools.publish_review._store_thread_ids_on_findings", AsyncMock()),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()),
        patch("agent.tools.publish_review.settle_review_check_run", AsyncMock()),
        patch("agent.tools.publish_review.dispatch_agent_run", dispatch),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            token="token",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
            branch_name=_BRANCH,
        )

    assert result == {
        "success": True,
        "review_id": 555,
        "surfaced_count": 1,
        "hidden_count": 0,
        "resolved_thread_count": 0,
    }
    post_review_args = post_review.await_args
    assert post_review_args is not None
    assert (
        post_review_args.kwargs["head_sha"],
        post_review_args.kwargs["inline_comments"],
    ) == (
        "head-sha",
        [
            {
                "path": "src/foo.py",
                "line": 10,
                "side": "RIGHT",
                "body": render_inline_comment_body(finding),
            }
        ],
    )
    assert store.puts == []
    dispatch.assert_not_awaited()
    status.assert_not_awaited()


async def test_publish_review_succeeds_when_autofix_store_write_raises() -> None:
    from agent.tools.publish_review import _publish_review_async

    finding = _finding()
    store = _Store()
    store.fail_put_namespace = ("autofix", _THREAD_ID)
    status = AsyncMock(return_value=True)
    with (
        _autofix_dependencies(store),
        patch("agent.tools.publish_review.get_store", return_value=store),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="reviewer"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=[finding]),
        ),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 555}),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch("agent.tools.publish_review._record_review_publication", AsyncMock()),
        patch(
            "agent.tools.publish_review._missing_comment_ids_for_published_findings",
            return_value=False,
        ),
        patch("agent.tools.publish_review._store_thread_ids_on_findings", AsyncMock()),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()),
        patch("agent.tools.publish_review.settle_review_check_run", AsyncMock()),
        patch(
            "agent.tools.publish_review.is_review_repo_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.is_pr_autofix_disabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.dispatch_client", return_value=_client()),
        patch("agent.tools.publish_review.dispatch_agent_run", AsyncMock()),
        patch("agent.tools.publish_review.post_autofix_status_check", status),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head-sha",
            token="token",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
            branch_name=_BRANCH,
        )

    assert result == {
        "success": True,
        "review_id": 555,
        "surfaced_count": 1,
        "hidden_count": 0,
        "resolved_thread_count": 0,
    }
    status_args = status.await_args
    assert status_args is not None
    assert status_args.kwargs["title"] == "Auto-fix dispatch failed"
