#!/usr/bin/env python3
"""Minimal dry-run router for Hermes review-loop documentation examples."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REVIEW_TEMPLATE = ROOT / "docs/hermes/templates/INDEPENDENT_REVIEW_RUNTIME_PROMPT.md"
NEXT_TEMPLATE = ROOT / "docs/hermes/templates/NEXT_ACTION_RUNTIME_PROMPT.md"
SIM_REVIEW = ROOT / "docs/hermes/examples/independent_review_report.example.json"
SIM_NEXT = ROOT / "docs/hermes/examples/next_action_prompt_report.example.json"

REQUIRED_INPUT_KEYS = {
    "event",
    "input_context",
    "allowed_paths",
    "forbidden_paths",
    "required_tests",
    "stop_conditions",
}
REQUIRED_CONTEXT_KEYS = {"reality_state", "task_card", "git_diff_summary", "evidence_log"}
SCHEMA_VERSION = "hermes.review-loop.v1"
DEFAULT_ALLOWED_REPOS = frozenset({"ollehillbom1/north-star-erp"})
DEFAULT_ALLOWED_TOOLS = frozenset(
    {
        "execute",
        "read_file",
        "write_file",
        "edit_file",
        "ls",
        "glob",
        "grep",
        "todos",
        "task",
        "subagent",
        "github_gh_proxy",
    }
)
ALLOWED_NEXT_ACTIONS = [
    "MARK_TASK_DONE",
    "CREATE_FOLLOWUP_TASK",
    "START_BUILDER_FIX_AGENT",
    "START_QA_AGENT",
    "START_SECURITY_AGENT",
    "START_ACCOUNTING_DOMAIN_AGENT",
    "BLOCK_AND_ESCALATE",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def validate(payload: dict) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_INPUT_KEYS - set(payload))
    if missing:
        errors.append(f"missing top-level keys: {', '.join(missing)}")
    context = payload.get("input_context")
    if not isinstance(context, dict):
        errors.append("input_context must be an object")
        return errors
    missing_context = sorted(REQUIRED_CONTEXT_KEYS - set(context))
    if missing_context:
        errors.append(f"missing input_context keys: {', '.join(missing_context)}")
    event = payload.get("event", {})
    if not isinstance(event, dict) or event.get("type") != "independent review recommended":
        errors.append("event.type must be 'independent review recommended'")
    return errors


def render_review_prompt(payload: dict) -> str:
    context = payload["input_context"]
    template = REVIEW_TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{REALITY_STATE}}": _dump(context["reality_state"]),
        "{{TASK_CARD}}": _dump(context["task_card"]),
        "{{GIT_DIFF_SUMMARY}}": _dump(context["git_diff_summary"]),
        "{{EVIDENCE_LOG}}": _dump(context["evidence_log"]),
        "{{ALLOWED_PATHS}}": _dump(payload["allowed_paths"]),
        "{{FORBIDDEN_PATHS}}": _dump(payload["forbidden_paths"]),
        "{{REQUIRED_TESTS}}": _dump(payload["required_tests"]),
        "{{STOP_CONDITIONS}}": _dump(payload["stop_conditions"]),
    }
    for old, new in replacements.items():
        template = template.replace(old, new)
    return template


def render_next_prompt(payload: dict, review_report: dict) -> str:
    context = payload["input_context"]
    template = NEXT_TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{REALITY_STATE}}": _dump(context["reality_state"]),
        "{{INDEPENDENT_REVIEW_REPORT}}": _dump(review_report),
        "{{PRODUCT_GOALS}}": _dump(
            {
                "strategic_direction": "make Northstar ERP operationally excellent; not an agent trigger by itself"
            }
        ),
        "{{EPIC_MAP}}": _dump({"vat_reporting": "tax/compliance evidence completeness"}),
        "{{TASK_BACKLOG}}": _dump(
            [{"id": "followup-vat-uat", "title": "Run missing VAT/reporting UAT evidence"}]
        ),
    }
    for old, new in replacements.items():
        template = template.replace(old, new)
    return template


def _blocked(reason: str, **extra: object) -> dict:
    return {"status": "BLOCKED", "gate": "FAIL", "block_reason": reason, **extra}


def _task_id(payload: dict) -> str | None:
    context = payload.get("input_context", {})
    if not isinstance(context, dict):
        return None
    reality_state = context.get("reality_state", {})
    task_card = context.get("task_card", {})
    if isinstance(reality_state, dict) and isinstance(reality_state.get("TASK_ID"), str):
        return reality_state["TASK_ID"]
    if isinstance(task_card, dict) and isinstance(task_card.get("id"), str):
        return task_card["id"]
    return None


def _find_forbidden_path_overlap(payload: dict) -> list[str]:
    allowed_paths = payload.get("allowed_paths") or []
    forbidden_paths = payload.get("forbidden_paths") or []
    if not isinstance(allowed_paths, list) or not isinstance(forbidden_paths, list):
        return []
    forbidden = {str(path).strip() for path in forbidden_paths}
    return sorted(str(path).strip() for path in allowed_paths if str(path).strip() in forbidden)


def _find_blocked_tools(payload: dict) -> list[str]:
    requested_tools = payload.get("requested_tools") or []
    if not isinstance(requested_tools, list):
        return []
    return [
        str(tool)
        for tool in requested_tools
        if str(tool).strip().lower() not in DEFAULT_ALLOWED_TOOLS
    ]


def route_review_trigger(payload: dict) -> dict:
    """Pure dry-run contract for the Hermes Northstar review loop.

    This does not start a server, call GitHub, spawn agents, or touch /erp. It
    validates the candidate review event and returns the exact structured
    review/next-action request a later Open SWE integration can route to agents.
    """

    errors = validate(payload)
    if errors:
        return _blocked("invalid_payload", errors=errors)

    event = payload["event"]
    repo = str(event.get("repo", ""))
    if repo not in DEFAULT_ALLOWED_REPOS:
        return _blocked("repo_not_allowed", repo=repo, allowed_repos=sorted(DEFAULT_ALLOWED_REPOS))

    blocked_paths = _find_forbidden_path_overlap(payload)
    if blocked_paths:
        return _blocked("forbidden_path_allowed", blocked_paths=blocked_paths)

    blocked_tools = _find_blocked_tools(payload)
    if blocked_tools:
        return _blocked("tool_not_allowed", blocked_tools=blocked_tools)

    context = payload["input_context"]
    if not context.get("evidence_log"):
        return _blocked("missing_evidence")

    task_id = _task_id(payload)
    if not task_id:
        return _blocked("missing_task_id")

    review_report = _load_json(SIM_REVIEW)
    return {
        "status": "OK",
        "schema_version": SCHEMA_VERSION,
        "gate": "PASS",
        "review_request": {
            "TASK_ID": task_id,
            "repo": repo,
            "trigger_user": event.get("trigger_user"),
            "allowed_paths": payload["allowed_paths"],
            "forbidden_paths": payload["forbidden_paths"],
            "required_tests": payload["required_tests"],
            "stop_conditions": payload["stop_conditions"],
            "prompt": render_review_prompt(payload),
        },
        "next_action_request": {
            "TASK_ID": task_id,
            "allowed_next_actions": ALLOWED_NEXT_ACTIONS,
            "prompt": render_next_prompt(payload, review_report),
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: review_trigger_router.py <review_trigger_input.json>", file=sys.stderr)
        return 2
    payload_path = Path(argv[1])
    payload = _load_json(payload_path)
    errors = validate(payload)
    if errors:
        print(json.dumps({"status": "INVALID", "errors": errors}, indent=2), file=sys.stderr)
        return 1
    review_report = _load_json(SIM_REVIEW)
    next_report = _load_json(SIM_NEXT)
    output = {
        "status": "OK",
        "input_event": payload["event"],
        "generated_review_prompt": render_review_prompt(payload),
        "simulated_review_report": review_report,
        "generated_next_action_prompt": render_next_prompt(payload, review_report),
        "expected_next_task": next_report,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
