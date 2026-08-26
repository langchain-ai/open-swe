"""Pinned outputs for every deterministic thread-id derivation.

These ids are persisted and re-derived across processes, so a formula change
must be a deliberate act that updates these literals — not a silent refactor.
"""

from agent.thread_ids import (
    baby_sit_lock_thread_id,
    github_issue_thread_id,
    linear_issue_thread_id,
    pr_comment_thread_id,
    review_style_thread_id,
    reviewer_thread_id,
    slack_thread_id,
    thread_id_from_branch,
)


def test_reviewer_thread_id() -> None:
    assert reviewer_thread_id("acme", "repo", 7) == "0e0feb04-05d3-5925-969e-1051f3741622"


def test_pr_comment_thread_id() -> None:
    assert pr_comment_thread_id("acme", "repo", 7) == "57ba1274-081d-5cad-a53e-d5f7610f2191"


def test_review_style_thread_id() -> None:
    assert review_style_thread_id("acme", "repo") == "7bdeb197-4322-52f1-a815-b485aeae5fdb"


def test_slack_thread_id_with_nonce() -> None:
    assert (
        slack_thread_id("C123", "1700000000.000100", "n-1")
        == "f5f18c6c-d944-5ede-8c62-23e7c8e493fe"
    )


def test_slack_thread_id_without_nonce() -> None:
    assert slack_thread_id("C123", "1700000000.000100") == "e624d56b-70ab-5512-b966-215798b600a2"
    assert slack_thread_id("C123", "1700000000.000100", None) == slack_thread_id(
        "C123", "1700000000.000100", ""
    )


def test_baby_sit_lock_thread_id() -> None:
    assert baby_sit_lock_thread_id("acme/repo#7") == "c81483d2-06cc-51e1-a06d-1d466d3fe3a1"


def test_linear_issue_thread_id() -> None:
    assert linear_issue_thread_id("ISSUE-123") == "0a442a54-e37c-4c56-ce50-ef11d08cd616"


def test_github_issue_thread_id() -> None:
    assert github_issue_thread_id("12345") == "37936ca4-38ca-563a-ffdd-d4bb7934bb91"


def test_issue_thread_ids_are_namespaced_apart() -> None:
    assert linear_issue_thread_id("12345") != github_issue_thread_id("12345")


def test_thread_id_from_branch() -> None:
    assert (
        thread_id_from_branch("open-swe/0e0feb04-05d3-5925-969e-1051f3741622")
        == "0e0feb04-05d3-5925-969e-1051f3741622"
    )
    assert (
        thread_id_from_branch("open-swe/0E0FEB04-05D3-5925-969E-1051F3741622")
        == "0E0FEB04-05D3-5925-969E-1051F3741622"
    )
    assert thread_id_from_branch("feature/no-uuid-here") is None
