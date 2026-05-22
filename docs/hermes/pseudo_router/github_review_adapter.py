"""Pure adapter from parsed GitHub comments to the Hermes review-loop contract.

This module intentionally performs no I/O: no webhook registration, no GitHub API
calls, no agent spawning, and no server start. It maps an already-received
comment payload into the dry-run router contract so the integration can be tested
before any live Open SWE wiring is enabled.
"""

from __future__ import annotations

from agent.utils.github_comments import parse_github_review_command
from docs.hermes.pseudo_router.review_trigger_router import route_review_trigger

ALLOWED_TRIGGER_USERS = frozenset({"ollehillbom1"})
REQUIRED_EVENT_FIELDS = frozenset(
    {
        "repo",
        "comment_author",
        "comment_body",
        "issue_number",
        "comment_id",
        "allowed_paths",
        "forbidden_paths",
        "required_tests",
    }
)
DEFAULT_STOP_CONDITIONS = [
    "secrets",
    "missing reviewer",
    "runtime claim without evidence",
    "dirty scope",
    "unsafe GitHub auth",
]


def _blocked(reason: str, **extra: object) -> dict:
    return {"status": "BLOCKED", "gate": "FAIL", "block_reason": reason, **extra}


def _missing_fields(event: dict) -> list[str]:
    return sorted(REQUIRED_EVENT_FIELDS - set(event))


def _task_id(event: dict) -> str:
    return f"GH-REVIEW-{event['issue_number']}-{event['comment_id']}"


def _payload_from_event(event: dict, pr_url: str | None) -> dict:
    task_id = _task_id(event)
    return {
        "event": {
            "type": "independent review recommended",
            "source": "github_comment_dry_run",
            "repo": event["repo"],
            "trigger_user": event["comment_author"],
            "issue_number": event["issue_number"],
            "comment_id": event["comment_id"],
            "pr_url": pr_url,
        },
        "input_context": {
            "reality_state": {
                "TASK_ID": task_id,
                "OWNER_SESSION": "github-comment-adapter-dry-run",
                "HERMES_TASK_ID": task_id,
            },
            "task_card": {
                "id": task_id,
                "title": f"Independent review for GitHub issue/PR #{event['issue_number']}",
                "source": "github_comment_dry_run",
            },
            "git_diff_summary": {
                "source": "not_fetched_in_dry_run",
                "reason": "adapter is intentionally no-network",
                "pr_url": pr_url,
            },
            "evidence_log": [
                {
                    "command": "parse_github_review_command",
                    "status": "PASS",
                    "detail": "recognized @openswe/@open-swe review command without network calls",
                }
            ],
        },
        "allowed_paths": event["allowed_paths"],
        "forbidden_paths": event["forbidden_paths"],
        "required_tests": event["required_tests"],
        "stop_conditions": event.get("stop_conditions", DEFAULT_STOP_CONDITIONS),
        "requested_tools": event.get("requested_tools", []),
    }


def route_github_review_comment(event: dict) -> dict:
    """Map one GitHub comment event into the pure review-loop contract."""

    missing = _missing_fields(event)
    if missing:
        return _blocked("missing_event_fields", missing_fields=missing)

    is_review_command, pr_url = parse_github_review_command(str(event["comment_body"]))
    if not is_review_command:
        return {
            "status": "IGNORED",
            "gate": "PASS",
            "reason": "not_review_command",
            "side_effects": [],
        }

    trigger_user = str(event["comment_author"])
    if trigger_user not in ALLOWED_TRIGGER_USERS:
        return _blocked("trigger_user_not_allowed", trigger_user=trigger_user)

    result = route_review_trigger(_payload_from_event(event, pr_url))
    result["source_event"] = {
        "command": "review",
        "comment_id": event["comment_id"],
        "issue_number": event["issue_number"],
        "pr_url": pr_url,
    }
    result["side_effects"] = []
    return result
