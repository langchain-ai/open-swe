"""Pure testrepo bootstrap approval gate for Hermes Northstar harness.

This module consumes a side-effect-free review pipeline result and emits a
human-readable approval packet for the next bootstrap step. It never creates a
GitHub App, configures webhooks, pushes branches, starts servers, or writes to
/erp. The output is a JSON-serializable dry-run decision only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_VERSION = "hermes.testrepo-bootstrap-approval.v1"
NORTHSTAR_REPO = "ollehillbom1/north-star-erp"
REQUIRED_DISABLED_INTEGRATIONS = frozenset({"slack", "linear"})
REQUIRED_DISABLED_TOOLS = frozenset({"http_request", "fetch_url", "web_search"})
EXTERNAL_SETUP_ORDER = ["github_app", "webhook", "docker_build"]


def _normalize_repo(repo: object) -> str:
    return str(repo or "").strip().lower()


def _string_set(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _ordered_external_setup(values: object) -> list[str]:
    requested = _string_set(values)
    ordered = [name for name in EXTERNAL_SETUP_ORDER if name in requested]
    extras = sorted(requested - set(EXTERNAL_SETUP_ORDER))
    return ordered + extras


def _block(reason: str, *, task_id: object = None, **extra: object) -> dict:
    packet = {
        "status": "BLOCKED",
        "schema_version": SCHEMA_VERSION,
        "gate": "FAIL",
        "block_reason": reason,
        "TASK_ID": task_id,
        "side_effects": [],
    }
    packet.update(extra)
    return packet


def _required_approval(target_repo: str) -> str:
    return f"ALLOW_TESTREPO_BOOTSTRAP=YES repo={target_repo}"


def _approval_packet(pipeline_result: dict, profile: dict, target_repo: str) -> dict:
    external_setup = _ordered_external_setup(profile.get("external_setup_requested", []))
    return {
        "dry_run_only": True,
        "target_repo": target_repo,
        "allowed_trigger_users": sorted(_string_set(profile.get("allowed_trigger_users", []))),
        "sandbox_type": str(profile.get("sandbox_type", "")),
        "requested_external_setup": external_setup,
        "required_exact_approval": _required_approval(target_repo),
        "source_pipeline_schema": pipeline_result.get("schema_version"),
        "source_pipeline_gate": pipeline_result.get("gate"),
        "source_next_action": (pipeline_result.get("final_decision") or {}).get("next_action"),
        "verification_required_before_pass": (pipeline_result.get("final_decision") or {}).get(
            "verification_required_before_pass", []
        ),
        "may_create_github_app": False,
        "may_configure_webhook": False,
        "may_push_or_pr": False,
        "may_start_server": False,
        "may_edit_erp": False,
    }


def _validate_pipeline(pipeline_result: dict) -> dict | None:
    task_id = pipeline_result.get("TASK_ID")
    if pipeline_result.get("status") != "OK":
        return _block(
            "pipeline_not_ok",
            task_id=task_id,
            pipeline_status=pipeline_result.get("status"),
        )
    if pipeline_result.get("side_effects"):
        return _block("pipeline_must_be_side_effect_free", task_id=task_id)
    if not isinstance(pipeline_result.get("final_decision"), dict):
        return _block("missing_final_decision", task_id=task_id)
    if not (pipeline_result.get("final_decision") or {}).get("next_action"):
        return _block("missing_next_action", task_id=task_id)
    return None


def _validate_profile(profile: dict, target_repo: str, task_id: object) -> dict | None:
    allowed_repos = _string_set(profile.get("allowed_repos", []))
    disabled_integrations = _string_set(profile.get("disabled_integrations", []))
    disabled_tools = _string_set(profile.get("disabled_tools", []))
    sandbox_type = str(profile.get("sandbox_type", "")).strip().lower()

    if target_repo == NORTHSTAR_REPO:
        return _block("northstar_repo_not_allowed_for_testrepo_bootstrap", task_id=task_id)
    if not target_repo:
        return _block("missing_target_repo", task_id=task_id)
    if target_repo not in allowed_repos:
        return _block("target_repo_not_allowlisted", task_id=task_id, target_repo=target_repo)
    if sandbox_type == "local":
        return _block("local_sandbox_not_allowed_for_autonomy", task_id=task_id)
    if not sandbox_type:
        return _block("missing_sandbox_type", task_id=task_id)

    missing_integrations = sorted(REQUIRED_DISABLED_INTEGRATIONS - disabled_integrations)
    if missing_integrations:
        return _block(
            "required_integrations_not_disabled",
            task_id=task_id,
            missing_integrations=missing_integrations,
        )

    missing_tools = sorted(REQUIRED_DISABLED_TOOLS - disabled_tools)
    if missing_tools:
        return _block("required_tools_not_disabled", task_id=task_id, missing_tools=missing_tools)

    return None


def build_bootstrap_approval_packet(pipeline_result: dict, bootstrap_profile: dict) -> dict:
    """Return a side-effect-free approval packet for testrepo bootstrap.

    The function defaults to READY_FOR_HUMAN_APPROVAL. Even when the exact
    approval phrase is present, it only marks a dry-run plan as approved; it does
    not authorize this module to create apps, configure webhooks, push, or run
    external setup.
    """

    pipeline_block = _validate_pipeline(pipeline_result)
    if pipeline_block:
        return pipeline_block

    task_id = pipeline_result.get("TASK_ID")
    target_repo = _normalize_repo(bootstrap_profile.get("target_repo"))
    profile_block = _validate_profile(bootstrap_profile, target_repo, task_id)
    if profile_block:
        return profile_block

    approval_packet = _approval_packet(pipeline_result, bootstrap_profile, target_repo)
    approved = bootstrap_profile.get("human_approval") == approval_packet["required_exact_approval"]

    return {
        "status": "APPROVED_DRY_RUN_PLAN" if approved else "READY_FOR_HUMAN_APPROVAL",
        "schema_version": SCHEMA_VERSION,
        "gate": "PASS" if approved else "WARN",
        "TASK_ID": task_id,
        "target_repo": target_repo,
        "approval_packet": approval_packet,
        "side_effects": [],
    }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: testrepo_bootstrap_approval_gate.py <pipeline_result.json> <bootstrap_profile.json>",
            file=sys.stderr,
        )
        return 2
    result = build_bootstrap_approval_packet(_load_json(Path(argv[1])), _load_json(Path(argv[2])))
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
