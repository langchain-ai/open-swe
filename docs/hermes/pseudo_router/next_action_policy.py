"""Pure reviewer-result to next-action policy for Hermes Northstar harness.

The policy is deterministic and side-effect free. It does not write Kanban,
comment on GitHub, spawn agents, start servers, or touch /erp. It only turns an
independent reviewer packet into an auditable next-action decision.
"""

from __future__ import annotations

REQUIRED_REVIEW_FIELDS = frozenset(
    {
        "schema_version",
        "owner_agent_role",
        "reviewer_identity",
        "verdict",
        "evidence_checked",
    }
)
FINAL_PASS_VERDICTS = frozenset({"PASS", "WARN_NO_BLOCKERS"})
ALLOWED_ROLE_ACTIONS = {
    "builder_fix_agent": "START_BUILDER_FIX_AGENT",
    "qa_agent": "START_QA_AGENT",
    "security_agent": "START_SECURITY_AGENT",
    "accounting_domain_agent": "START_ACCOUNTING_DOMAIN_AGENT",
}
HARD_STOP_MARKERS = (
    "secret",
    "credential",
    "token",
    "private key",
    "unsafe github auth",
    "dirty scope",
    "missing reviewer",
)


def _blocked(reason: str, **extra: object) -> dict:
    return {"status": "BLOCKED", "gate": "FAIL", "block_reason": reason, **extra}


def _missing_review_fields(review_report: dict) -> list[str]:
    return sorted(REQUIRED_REVIEW_FIELDS - set(review_report))


def _allowed_actions(context: dict) -> set[str]:
    actions = context.get("allowed_next_actions") or []
    if not isinstance(actions, list):
        return set()
    return {str(action) for action in actions}


def _hard_stop(stop_conditions: object) -> str | None:
    if not isinstance(stop_conditions, list):
        return None
    for condition in stop_conditions:
        condition_text = str(condition).lower()
        if any(marker in condition_text for marker in HARD_STOP_MARKERS):
            return str(condition)
    return None


def _action_for_review(review_report: dict) -> tuple[str | None, str | None]:
    verdict = str(review_report.get("verdict", ""))
    if verdict == "PASS":
        return "MARK_TASK_DONE", None
    if verdict == "WARN_NO_BLOCKERS":
        return "CREATE_FOLLOWUP_TASK", None

    recommended_role = str(review_report.get("next_agent_role_recommendation", ""))
    action = ALLOWED_ROLE_ACTIONS.get(recommended_role)
    if action:
        return action, None
    return None, recommended_role


def _base_decision(review_report: dict, context: dict, next_action: str) -> dict:
    return {
        "next_action": next_action,
        "reviewer_identity": review_report["reviewer_identity"],
        "review_verdict": review_report["verdict"],
        "final_pass_eligible": review_report["verdict"] in FINAL_PASS_VERDICTS,
        "verification_required_before_pass": review_report.get(
            "verification_required_before_pass", []
        ),
        "allowed_next_actions": sorted(_allowed_actions(context)),
    }


def decide_next_action(review_report: dict, context: dict) -> dict:
    """Decide the next action from an independent review packet.

    The function is pure and returns only a JSON-serializable policy packet.
    """

    missing = _missing_review_fields(review_report)
    if missing:
        return _blocked("missing_review_fields", missing_fields=missing)

    if not review_report.get("evidence_checked"):
        return _blocked("missing_evidence_checked")

    task_id = context.get("TASK_ID")
    if not task_id:
        return _blocked("missing_task_id")

    allowed_actions = _allowed_actions(context)
    if not allowed_actions:
        return _blocked("missing_allowed_next_actions")

    hard_stop = _hard_stop(review_report.get("stop_conditions_hit", []))
    if hard_stop:
        return {
            **_blocked("stop_condition_hit", stop_condition=hard_stop),
            "TASK_ID": task_id,
            "decision": _base_decision(review_report, context, "BLOCK_AND_ESCALATE"),
            "side_effects": [],
        }

    requested_final_gate = str(context.get("requested_final_gate", "WARN"))
    verdict = str(review_report.get("verdict", ""))
    if requested_final_gate == "PASS" and verdict not in FINAL_PASS_VERDICTS:
        return _blocked("reviewer_not_final_pass", verdict=verdict, TASK_ID=task_id)

    next_action, unsupported_role = _action_for_review(review_report)
    if next_action is None:
        return _blocked(
            "unsupported_next_agent_role",
            recommended_role=unsupported_role,
            TASK_ID=task_id,
        )

    if next_action not in allowed_actions:
        return _blocked("next_action_not_allowed", next_action=next_action, TASK_ID=task_id)

    gate = "PASS" if verdict == "PASS" else "WARN"
    return {
        "status": "OK",
        "gate": gate,
        "TASK_ID": task_id,
        "decision": _base_decision(review_report, context, next_action),
        "side_effects": [],
    }
