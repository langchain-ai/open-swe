"""Controlled local testrepo bootstrap dry-run packet consumer.

This module consumes the controlled approval-runner output and renders the next
machine-readable dry-run checklist packet. It never executes bootstrap work,
starts servers, configures GitHub Apps/webhooks, pushes branches, deploys, edits
/erp, or echoes raw approval text/secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "hermes.controlled-bootstrap-dry-run-packet-consumer.v1"
APPROVAL_RUNNER_SCHEMA_VERSION = "hermes.controlled-bootstrap-approval-runner.v1"
_APPROVAL_READY_STATUS = "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_APPROVAL_PACKET_READY"
_DRY_RUN_ACTION = "dry_run_controlled_local_testrepo_bootstrap_preview"
_READY_NEXT_ACTION = "review controlled local testrepo bootstrap dry-run packet"
_DEFAULT_NEXT_ACTION = "resolve controlled local testrepo bootstrap dry-run packet blockers"

_LIVE_ACTION_KEYS = (
    "may_create_github_app",
    "may_configure_webhook",
    "may_push_or_pr",
    "may_start_server",
    "may_edit_erp",
    "may_deploy_prod",
    "may_run_bootstrap",
    "may_execute_bootstrap",
)
_LIVE_FLAG_KEYS = (
    "ALLOW_BOOTSTRAP_INSTALL",
    "ALLOW_WEBHOOK_SETUP",
    "ALLOW_GITHUB_APP_SETUP",
    "ALLOW_PROD_INSTALL",
    "ALLOW_DOCKER_BUILD",
    "BOOTSTRAP_EXECUTION_ALLOWED",
)
_SECRET_LIKE_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_RAW_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(approval_text|raw_approval|human_approval|comment_body|secret|token|password|private_key)"
)


def _normalize_repo(repo: Any) -> str:
    return str(repo or "").strip().lower()


def _normalized_allowlist(values: Any) -> list[str]:
    if not isinstance(values, list | tuple | set):
        return []
    return sorted({_normalize_repo(value) for value in values if _normalize_repo(value)})


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


def _safe_output_text(value: str) -> str:
    return "[REDACTED]" if _contains_secret_like_text(value) else value


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _has_raw_sensitive_input(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (
                _RAW_SENSITIVE_KEY_RE.search(str(key))
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


def _live_action_toggle_enabled(payload: dict[str, Any]) -> bool:
    for key in _LIVE_ACTION_KEYS:
        if _bool_true(payload.get(key)):
            return True
    for key in _LIVE_FLAG_KEYS:
        if key == "BOOTSTRAP_EXECUTION_ALLOWED":
            continue
        if _bool_true(payload.get(key)):
            return True
    nested_flags = payload.get("live_actions_enabled")
    if isinstance(nested_flags, dict) and any(_bool_true(value) for value in nested_flags.values()):
        return True
    return False


def _execution_checklist() -> list[dict[str, Any]]:
    return [
        {
            "id": "confirm_testrepo_scope",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "confirm_approval_packet_ready",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "render_local_bootstrap_preview",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
        {
            "id": "stop_before_live_bootstrap_execution",
            "mode": "dry_run",
            "live_action": False,
            "executor_command": None,
        },
    ]


def _block(*reasons: str, target_repo: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "gate": "FAIL",
        "DRY_RUN_PACKET_READY": False,
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": _safe_output_text(target_repo),
        "block_reasons": _dedupe([reason for reason in reasons if reason]),
        "NEXT_REQUIRED_HUMAN_ACTION": _DEFAULT_NEXT_ACTION,
        "side_effects": [],
    }


def build_controlled_bootstrap_dry_run_packet(
    *,
    approval_packet: Any = None,
    allowed_test_repos: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a sanitized dry-run checklist packet from an approval-runner packet.

    The returned packet is intentionally not executable. ``BOOTSTRAP_EXECUTION_ALLOWED``
    is always false and ``side_effects`` is always empty.
    """

    block_reasons: list[str] = []
    if approval_packet is None:
        approval_packet = {}
    elif not isinstance(approval_packet, dict):
        block_reasons.append("approval_packet_payload_not_object")
        approval_packet = {}
    else:
        approval_packet = dict(approval_packet)

    target_repo = _normalize_repo(approval_packet.get("target_repo"))
    allowlist = _normalized_allowlist(allowed_test_repos or [])

    if approval_packet.pop("__invalid_json__", False):
        block_reasons.append("approval_packet_json_invalid")
    if approval_packet.pop("__input_error__", False):
        block_reasons.append("approval_packet_input_unreadable")
    if not approval_packet:
        block_reasons.append("missing_approval_packet")
    elif approval_packet.get("schema_version") != APPROVAL_RUNNER_SCHEMA_VERSION:
        block_reasons.append("approval_packet_schema_mismatch")

    if approval_packet.get("status") != _APPROVAL_READY_STATUS:
        block_reasons.append("approval_packet_not_ready")
    if approval_packet.get("gate") != "PASS":
        block_reasons.append("approval_packet_gate_not_pass")
    if approval_packet.get("APPROVAL_PACKET_READY") is not True:
        block_reasons.append("approval_packet_not_ready")
    if approval_packet.get("BOOTSTRAP_EXECUTION_ALLOWED") is not False:
        block_reasons.append("approval_packet_execution_allowed")
    if approval_packet.get("side_effects") != []:
        block_reasons.append("approval_packet_has_side_effects")
    allowed_actions = approval_packet.get("allowed_actions", [])
    if not isinstance(allowed_actions, list):
        block_reasons.append("approval_packet_allows_non_dry_run_actions")
        allowed_actions = []
    if _DRY_RUN_ACTION not in allowed_actions:
        block_reasons.append("approval_packet_missing_dry_run_action")
    if any(not str(action).startswith("dry_run_") for action in allowed_actions):
        block_reasons.append("approval_packet_allows_non_dry_run_actions")
    if target_repo not in allowlist:
        block_reasons.append("target_repo_not_allowlisted_for_controlled_testrepo_bootstrap")
    if _live_action_toggle_enabled(approval_packet):
        block_reasons.append("live_action_toggle_enabled")
    if _has_raw_sensitive_input(approval_packet):
        block_reasons.append("unsafe_raw_approval_or_secret_material_detected")

    if block_reasons:
        return _block(*block_reasons, target_repo=target_repo)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_DRY_RUN_PACKET_READY",
        "gate": "PASS",
        "DRY_RUN_PACKET_READY": True,
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": _safe_output_text(target_repo),
        "source_approval_schema": approval_packet.get("schema_version"),
        "source_approval_gate": approval_packet.get("gate"),
        "source_readiness_schema": approval_packet.get("source_readiness_schema"),
        "source_readiness_gate": approval_packet.get("source_readiness_gate"),
        "execution_checklist": _execution_checklist(),
        "NEXT_REQUIRED_HUMAN_ACTION": _READY_NEXT_ACTION,
        "side_effects": [],
    }


def _load_json(path: str | None) -> Any:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"__invalid_json__": True}
    except OSError:
        return {"__input_error__": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a side-effect-free controlled local testrepo bootstrap dry-run packet."
    )
    parser.add_argument("--approval-packet")
    parser.add_argument("--allowed-test-repo", action="append", default=[])
    args = parser.parse_args(argv)

    result = build_controlled_bootstrap_dry_run_packet(
        approval_packet=_load_json(args.approval_packet),
        allowed_test_repos=args.allowed_test_repo,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("DRY_RUN_PACKET_READY") is True else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
