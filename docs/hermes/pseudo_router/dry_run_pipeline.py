"""Side-effect-free dry-run pipeline for Hermes Northstar review automation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docs.hermes.pseudo_router.github_review_adapter import route_github_review_comment
from docs.hermes.pseudo_router.next_action_policy import decide_next_action

SCHEMA_VERSION = "hermes.review-pipeline.v1"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _blocked_from_stage(stage: str, result: dict, stages: dict) -> dict:
    return {
        "status": "BLOCKED",
        "schema_version": SCHEMA_VERSION,
        "gate": result.get("gate", "FAIL"),
        "block_stage": stage,
        "block_reason": result.get("block_reason", "unknown"),
        "TASK_ID": result.get("TASK_ID"),
        "stages": stages,
        "side_effects": [],
    }


def _policy_context(adapter_result: dict, requested_final_gate: str) -> dict:
    next_action_request = adapter_result["next_action_request"]
    return {
        "TASK_ID": next_action_request["TASK_ID"],
        "requested_final_gate": requested_final_gate,
        "allowed_next_actions": next_action_request["allowed_next_actions"],
    }


def _stage_summary(result: dict) -> dict:
    summary = {
        "status": result.get("status"),
        "gate": result.get("gate"),
    }
    if result.get("block_reason"):
        summary["block_reason"] = result["block_reason"]
    if result.get("TASK_ID"):
        summary["TASK_ID"] = result["TASK_ID"]
    return summary


def run_pipeline(payload: dict) -> dict:
    """Run comment -> review trigger -> next-action policy as a pure dry-run."""

    stages: dict[str, dict] = {}
    adapter_result = route_github_review_comment(payload.get("comment_event", {}))
    stages["github_comment_adapter"] = _stage_summary(adapter_result)

    if adapter_result.get("status") == "IGNORED":
        return {
            "status": "IGNORED",
            "schema_version": SCHEMA_VERSION,
            "gate": adapter_result.get("gate", "PASS"),
            "reason": adapter_result.get("reason"),
            "stages": stages,
            "side_effects": [],
        }

    if adapter_result.get("status") != "OK":
        return _blocked_from_stage("github_comment_adapter", adapter_result, stages)

    policy_context = _policy_context(
        adapter_result, str(payload.get("requested_final_gate", "WARN"))
    )
    policy_result = decide_next_action(payload.get("review_report", {}), policy_context)
    stages["next_action_policy"] = _stage_summary(policy_result)

    if policy_result.get("status") != "OK":
        blocked = _blocked_from_stage("next_action_policy", policy_result, stages)
        blocked["TASK_ID"] = policy_result.get("TASK_ID") or policy_context["TASK_ID"]
        return blocked

    return {
        "status": "OK",
        "schema_version": SCHEMA_VERSION,
        "gate": policy_result["gate"],
        "TASK_ID": policy_result["TASK_ID"],
        "source_event": adapter_result.get("source_event", {}),
        "stages": stages,
        "final_decision": policy_result["decision"],
        "side_effects": [],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: dry_run_pipeline.py <pipeline_input.json>", file=sys.stderr)
        return 2
    result = run_pipeline(_load_json(Path(argv[1])))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"OK", "IGNORED"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
