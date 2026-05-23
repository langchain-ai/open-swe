"""Side-effect-free dry-run bootstrap plan fan-in for Hermes Northstar harness.

This module consumes the bootstrap readiness aggregator output plus the testrepo
approval-gate output and renders a dry-run plan only. It never executes a
bootstrap, starts a server, configures GitHub Apps/webhooks, pushes branches,
deploys, edits /erp, or echoes raw approval text/secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "hermes.bootstrap-dry-run-plan.v1"
READINESS_SCHEMA_VERSION = "hermes.bootstrap-readiness-aggregator.v1"
APPROVAL_SCHEMA_VERSION = "hermes.testrepo-bootstrap-approval.v1"

_LIVE_ACTION_KEYS = (
    "may_create_github_app",
    "may_configure_webhook",
    "may_push_or_pr",
    "may_start_server",
    "may_edit_erp",
    "may_deploy_prod",
)
_SECRET_LIKE_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_RAW_SECRET_KEY_RE = re.compile(r"(?i)(secret|token|password|private_key)")


def _bool_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "yes", "true", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _contains_secret_like_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_LIKE_PATTERNS)


def _has_raw_sensitive_input(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (
                _RAW_SECRET_KEY_RE.search(str(key))
                and value not in (None, "", False)
                and not isinstance(value, bool)
            ):
                return True
            if _has_raw_sensitive_input(value):
                return True
        return False
    if isinstance(payload, list | tuple):
        return any(_has_raw_sensitive_input(item) for item in payload)
    if isinstance(payload, str):
        return _contains_secret_like_text(payload)
    return False


def _block(*reasons: str, target_repo: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "gate": "FAIL",
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": target_repo,
        "block_reasons": [reason for reason in reasons if reason],
        "NEXT_REQUIRED_HUMAN_ACTION": "resolve dry-run bootstrap plan blockers",
        "side_effects": [],
    }


def _planned_steps() -> list[dict[str, Any]]:
    return [
        {
            "id": "validate_testrepo_scope",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_github_app_setup_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_webhook_setup_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_local_testrepo_bootstrap_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
    ]


def _approval_packet_live_actions_enabled(approval_packet: dict[str, Any]) -> bool:
    packet = approval_packet.get("approval_packet")
    if not isinstance(packet, dict):
        return True
    return any(_bool_true(packet.get(key)) for key in _LIVE_ACTION_KEYS)


def _target_repo_values_match(
    readiness_decision: dict[str, Any], approval_packet: dict[str, Any]
) -> bool:
    readiness_target = str(readiness_decision.get("target_repo") or "").strip()
    approval_targets = [str(approval_packet.get("target_repo") or "").strip()]
    nested_packet = approval_packet.get("approval_packet")
    if isinstance(nested_packet, dict):
        approval_targets.append(str(nested_packet.get("target_repo") or "").strip())
    return bool(readiness_target) and all(target == readiness_target for target in approval_targets)


def build_bootstrap_dry_run_plan(
    readiness_decision: dict[str, Any] | None,
    approval_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a sanitized dry-run-only bootstrap plan.

    The plan is intentionally not executable. It is a transcript/checklist for a
    human and a future separately-approved live bootstrap slice.
    """

    readiness_decision = readiness_decision or {}
    approval_packet = approval_packet or {}
    target_repo = str(
        readiness_decision.get("target_repo") or approval_packet.get("target_repo") or ""
    ).strip()
    block_reasons: list[str] = []

    if readiness_decision.get("schema_version") != READINESS_SCHEMA_VERSION:
        block_reasons.append("readiness_schema_mismatch")
    if readiness_decision.get("BOOTSTRAP_READY") is not True:
        block_reasons.append("readiness_not_ready")
    if readiness_decision.get("side_effects") != []:
        block_reasons.append("readiness_has_side_effects")
    if approval_packet.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        block_reasons.append("approval_packet_schema_mismatch")
    if approval_packet.get("status") != "APPROVED_DRY_RUN_PLAN":
        block_reasons.append("approval_packet_not_approved_dry_run_plan")
    if approval_packet.get("side_effects") != []:
        block_reasons.append("approval_packet_has_side_effects")
    if _approval_packet_live_actions_enabled(approval_packet):
        block_reasons.append("approval_packet_live_actions_enabled")
    if not _target_repo_values_match(readiness_decision, approval_packet):
        block_reasons.append("target_repo_mismatch")
    if _has_raw_sensitive_input((readiness_decision, approval_packet)):
        block_reasons.append("unsafe_raw_approval_or_secret_material_detected")

    if block_reasons:
        return _block(*dict.fromkeys(block_reasons), target_repo=target_repo)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "DRY_RUN_BOOTSTRAP_PLAN_READY",
        "gate": "PASS",
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": target_repo,
        "planned_steps": _planned_steps(),
        "NEXT_REQUIRED_HUMAN_ACTION": "review dry-run bootstrap plan output",
        "side_effects": [],
    }


def _load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a sanitized side-effect-free Hermes bootstrap dry-run plan."
    )
    parser.add_argument("--readiness-decision")
    parser.add_argument("--approval-packet")
    args = parser.parse_args(argv)

    result = build_bootstrap_dry_run_plan(
        _load_json(args.readiness_decision),
        _load_json(args.approval_packet),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "DRY_RUN_BOOTSTRAP_PLAN_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
