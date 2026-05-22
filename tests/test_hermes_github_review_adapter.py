from __future__ import annotations

from docs.hermes.pseudo_router.github_review_adapter import route_github_review_comment


def base_event(**overrides: object) -> dict:
    event = {
        "repo": "ollehillbom1/north-star-erp",
        "comment_author": "ollehillbom1",
        "comment_body": "@openswe review",
        "issue_number": 42,
        "comment_id": 9001,
        "allowed_paths": ["apps/core-api/src/**", "tests/**"],
        "forbidden_paths": [".env", "**/.env", "**/secrets/**"],
        "required_tests": ["./b.sh --quick"],
    }
    event.update(overrides)
    return event


def test_openswe_review_comment_routes_to_contract_without_side_effects() -> None:
    result = route_github_review_comment(base_event())

    assert result["status"] == "OK"
    assert result["schema_version"] == "hermes.review-loop.v1"
    assert result["review_request"]["TASK_ID"] == "GH-REVIEW-42-9001"
    assert result["review_request"]["repo"] == "ollehillbom1/north-star-erp"
    assert result["review_request"]["trigger_user"] == "ollehillbom1"
    assert result["source_event"] == {
        "command": "review",
        "comment_id": 9001,
        "issue_number": 42,
        "pr_url": None,
    }
    assert result["side_effects"] == []
    assert "github_api_called" not in result
    assert "webhook_started" not in result


def test_open_swe_review_comment_with_pr_url_preserves_url_for_later_validation() -> None:
    pr_url = "https://github.com/ollehillbom1/north-star-erp/pull/123"
    result = route_github_review_comment(base_event(comment_body=f"@open-swe review {pr_url}"))

    assert result["status"] == "OK"
    assert result["source_event"]["pr_url"] == pr_url
    assert result["review_request"]["TASK_ID"] == "GH-REVIEW-42-9001"


def test_non_review_comment_is_ignored_without_generating_prompt() -> None:
    result = route_github_review_comment(
        base_event(comment_body="@openswe please do something vague")
    )

    assert result == {
        "status": "IGNORED",
        "gate": "PASS",
        "reason": "not_review_command",
        "side_effects": [],
    }


def test_unapproved_trigger_user_is_blocked_before_contract_routing() -> None:
    result = route_github_review_comment(base_event(comment_author="unknown-user"))

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "trigger_user_not_allowed"
    assert result["trigger_user"] == "unknown-user"
    assert "review_request" not in result


def test_unknown_repo_is_blocked_by_contract() -> None:
    result = route_github_review_comment(base_event(repo="someone/repo"))

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "repo_not_allowed"


def test_adapter_blocks_missing_required_event_fields() -> None:
    event = base_event()
    del event["comment_id"]

    result = route_github_review_comment(event)

    assert result["status"] == "BLOCKED"
    assert result["gate"] == "FAIL"
    assert result["block_reason"] == "missing_event_fields"
    assert result["missing_fields"] == ["comment_id"]
