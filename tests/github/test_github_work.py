"""Which of the backend's own jobs each GitHub delivery starts (``openswe.github.work``).

MDA's GitHub channel hands every delivery to the backend, which picks its work as
its own webhook did. The channel's agent runs are in ``mda/tests/test_github.py``.
"""

import pytest
from runtime_api.github import GitHubDeliveryFacts, GitHubWorkName

from openswe.github.work import delivery_work

_TAGS = ["@openswe", "@open-swe", "@openswe-dev"]
_FACTS = GitHubDeliveryFacts(routable=True, allowed=True, auto_review=True, tags=_TAGS)
_REPOSITORY = {"name": "api", "owner": {"login": "acme"}}
_SENDER = {"login": "ada", "id": 2}


def _facts(**changes: object) -> GitHubDeliveryFacts:
    return _FACTS.model_copy(update=changes)


def _issue(action: str = "opened", body: str = "@openswe fix it", **extra: object) -> dict:
    issue = {"id": 70, "number": 7, "title": "Crash", "body": body, "user": {"login": "bob"}}
    return {
        "action": action,
        "issue": issue,
        "repository": _REPOSITORY,
        "sender": _SENDER,
        **extra,
    }


def _comment(body: str, *, on_pr: bool, action: str = "created") -> dict:
    issue: dict[str, object] = {"id": 70, "number": 7, "title": "Crash", "body": ""}
    if on_pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/acme/api/pulls/7"}
    return {
        "action": action,
        "issue": issue,
        "comment": {"id": 5, "body": body, "user": _SENDER, "created_at": "2026-01-01"},
        "repository": _REPOSITORY,
        "sender": _SENDER,
    }


def _review(action: str = "submitted", body: str = "") -> dict:
    return {
        "action": action,
        "review": {"body": body, "state": "commented"},
        "pull_request": {"number": 7},
        "repository": _REPOSITORY,
    }


@pytest.mark.parametrize(
    ("event", "payload", "facts", "work"),
    [
        pytest.param("issues", _issue(), _facts(routable=False), (), id="unowned-repository"),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _facts(allowed=False),
            (),
            id="outside-the-allowlist",
        ),
        pytest.param(
            "merge_group",
            {"action": "checks_requested"},
            _FACTS,
            ("wake_merge_queue",),
            id="merge-group",
        ),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _FACTS,
            (
                "wake_merge_queue",
                "walkthrough_pull_request",
                "track_pull_request_state",
                "track_pull_request_usage",
                "evaluate_approval_policy",
                "review_pull_request",
            ),
            id="pr-opened-by-the-agent",
        ),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _facts(gated=True),
            ("wake_merge_queue",),
            id="pr-opened-by-a-sender-the-public-repo-gate-turns-away",
        ),
        pytest.param(
            "pull_request_review_thread",
            {"action": "resolved"},
            _FACTS,
            ("sync_review_thread",),
            id="review-thread-resolved",
        ),
        pytest.param(
            "pull_request",
            {"action": "ready_for_review"},
            _facts(auto_review=False),
            (
                "wake_merge_queue",
                "walkthrough_pull_request",
                "track_pull_request_state",
                "track_pull_request_usage",
                "evaluate_approval_policy",
                "settle_human_reviews",
            ),
            id="pr-ready-without-review",
        ),
        pytest.param(
            "pull_request",
            {"action": "synchronize"},
            _facts(auto_review=False),
            (
                "wake_merge_queue",
                "walkthrough_pull_request",
                "track_pull_request_state",
                "withdraw_superseded_approvals",
                "notify_pr_updated",
                "review_fork_push",
                "track_pull_request_usage",
                "evaluate_approval_policy",
                "settle_human_reviews",
            ),
            id="pr-pushed",
        ),
        pytest.param(
            "pull_request",
            {"action": "closed"},
            _FACTS,
            (
                "wake_merge_queue",
                "track_pull_request_state",
                "track_pull_request_usage",
                "settle_review_on_close",
                "stop_preview_runs_on_close",
                "delete_app_documents_on_close",
                "update_reviewer_watch",
            ),
            id="pr-closed",
        ),
        pytest.param(
            "pull_request",
            {"action": "edited"},
            _FACTS,
            ("wake_merge_queue", "track_pull_request_state", "evaluate_approval_policy"),
            id="pr-edited",
        ),
        pytest.param("push", {}, _FACTS, ("wake_merge_queue", "review_push"), id="push"),
        pytest.param(
            "push", {}, _facts(auto_review=False), ("wake_merge_queue",), id="push-without-review"
        ),
        pytest.param(
            "check_run",
            {"action": "completed"},
            _FACTS,
            ("ci_event", "review_apps_on_preview"),
            id="check-finished",
        ),
        pytest.param("check_run", {"action": "created"}, _FACTS, ("ci_event",), id="check-started"),
        pytest.param(
            "workflow_run", {"action": "completed"}, _FACTS, ("ci_event",), id="workflow-finished"
        ),
        pytest.param("status", {}, _facts(allowed=False), (), id="ci-outside-the-allowlist"),
        pytest.param(
            "pull_request_review",
            _review(),
            _FACTS,
            (
                "wake_merge_queue",
                "settle_human_reviews",
                "record_completed_review",
                "evaluate_approval_policy",
            ),
            id="review-submitted",
        ),
        pytest.param(
            "pull_request_review",
            _review(body="@openswe address these"),
            _FACTS,
            (
                "wake_merge_queue",
                "settle_human_reviews",
                "record_completed_review",
                "evaluate_approval_policy",
            ),
            id="the-agents-own-review",
        ),
        pytest.param(
            "pull_request_review",
            _review("dismissed"),
            _FACTS,
            ("wake_merge_queue", "settle_human_reviews", "evaluate_approval_policy"),
            id="review-dismissed",
        ),
        pytest.param("issues", _issue(), _FACTS, (), id="issue-mention"),
        pytest.param("issues", _issue(body="no handle"), _FACTS, (), id="issue-without-mention"),
        pytest.param("issues", _issue(), _FACTS, (), id="issue-the-agent-opened"),
        pytest.param(
            "issues",
            _issue("edited", changes={"labels": {}}),
            _FACTS,
            (),
            id="issue-edit-without-text",
        ),
        pytest.param(
            "issues",
            _issue("reopened"),
            _facts(allowed=False),
            (),
            id="issue-outside-the-allowlist",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe try again", on_pr=False),
            _FACTS,
            (),
            id="issue-comment-mention",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe-preview try again", on_pr=False),
            _FACTS,
            (),
            id="another-deployments-handle",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe try again", on_pr=False),
            _FACTS,
            (),
            id="the-agents-own-comment",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe fix CI", on_pr=True),
            _FACTS,
            (),
            id="pr-comment-mention",
        ),
        pytest.param(
            "issue_comment",
            _comment("CI is failing", on_pr=True, action="edited"),
            _FACTS,
            (),
            id="pr-comment-untagged",
        ),
        pytest.param(
            "issue_comment",
            _comment("!merge", on_pr=True),
            _FACTS,
            (),
            id="untagged-workflow-command",
        ),
        pytest.param(
            "installation_repositories",
            {"action": "added", "repositories_added": [], "repositories_removed": []},
            _facts(routable=True),
            ("forget_cached_repos",),
            id="installation-changed",
        ),
        pytest.param("issues", _issue("closed"), _FACTS, (), id="issue-closed-only-logged"),
        pytest.param(
            "pull_request",
            {"action": "labeled"},
            _FACTS,
            ("wake_merge_queue",),
            id="pr-labeled-wakes-the-queue",
        ),
        pytest.param(
            "pull_request_review_comment",
            {
                "action": "created",
                "comment": {"body": "The finding is wrong", "in_reply_to_id": 1},
                "repository": _REPOSITORY,
            },
            _FACTS,
            (),
            id="review-comment-reply",
        ),
    ],
)
def test_a_delivery_starts_the_backends_work_its_webhook_did(
    event: str, payload: dict, facts: GitHubDeliveryFacts, work: tuple[GitHubWorkName, ...]
) -> None:
    assert delivery_work(event, payload, facts) == work
