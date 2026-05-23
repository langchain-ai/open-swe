"""Controlled dry-run approval packet runner for local testrepo bootstrap.

This module consumes a previously produced bootstrap readiness aggregator output
and a human approval phrase that was already provided to the caller. It emits a
machine-readable dry-run approval packet only. It never executes bootstrap work,
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

from docs.hermes.pseudo_router.bootstrap_install_policy_parser import EXACT_APPROVAL_PHRASE

SCHEMA_VERSION = "hermes.controlled-bootstrap-approval-runner.v1"
READINESS_SCHEMA_VERSION = "hermes.bootstrap-readiness-aggregator.v1"
_ALLOWED_ACTION = "dry_run_controlled_local_testrepo_bootstrap_preview"
_DEFAULT_NEXT_ACTION = "resolve controlled local testrepo bootstrap approval blockers"
_READY_NEXT_ACTION = "review controlled local testrepo bootstrap dry-run packet"

_SECRET_LIKE_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_RAW_SECRET_KEY_RE = re.compile(r"(?i)(secret|token|password|private_key)")
_REQUIRED_READINESS_CHECKS = (
    "execution_manifest_ready_checklist",
    "install_allowed",
    "target_repo_allowlisted",
    "scanners_and_audits_green",
    "live_actions_disabled",
    "no_secret_or_approval_echo",
)


def _normalize_repo(repo: Any) -> str:
    return str(repo or "").strip().lower()


def _normalized_allowlist(values: Any) -> list[str]:
    if not isinstance(values, list | tuple | set):
        return []
    return sorted({_normalize_repo(value) for value in values if _normalize_repo(value)})


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


def _has_exact_approval_phrase(approval_text: str) -> bool:
    return approval_text.strip() == EXACT_APPROVAL_PHRASE


def _safe_output_text(value: str) -> str:
    return "[REDACTED]" if _contains_secret_like_text(value) else value


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _block(*reasons: str, target_repo: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "gate": "WARN",
        "APPROVAL_PACKET_READY": False,
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": _safe_output_text(target_repo),
        "approval_text_echoed": False,
        "approval_phrase_received": False,
        "block_reasons": _dedupe([reason for reason in reasons if reason]),
        "NEXT_REQUIRED_HUMAN_ACTION": _DEFAULT_NEXT_ACTION,
        "side_effects": [],
    }


def build_controlled_bootstrap_approval_packet(
    *,
    readiness_decision: Any = None,
    approval_text: str | None = None,
    allowed_test_repos: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a sanitized dry-run approval packet for an allowlisted testrepo.

    The exact approval phrase can make the packet ready for human review, but it
    never grants executor permission to this module. ``BOOTSTRAP_EXECUTION_ALLOWED``
    is always false and ``side_effects`` is always empty.
    """

    block_reasons: list[str] = []
    if readiness_decision is None:
        readiness_decision = {}
    elif not isinstance(readiness_decision, dict):
        block_reasons.append("readiness_payload_not_object")
        readiness_decision = {}

    raw_approval_text = approval_text or ""
    allowlist = _normalized_allowlist(allowed_test_repos or [])
    target_repo = _normalize_repo(readiness_decision.get("target_repo"))
    checks = readiness_decision.get("checks")

    if not readiness_decision:
        block_reasons.append("missing_readiness_decision")
    elif readiness_decision.get("schema_version") != READINESS_SCHEMA_VERSION:
        block_reasons.append("readiness_schema_mismatch")

    if readiness_decision.get("BOOTSTRAP_READY") is not True:
        block_reasons.append("readiness_not_ready")
    if readiness_decision.get("side_effects") != []:
        block_reasons.append("readiness_has_side_effects")

    if not isinstance(checks, dict):
        block_reasons.append("readiness_checks_missing")
    else:
        for check_name in _REQUIRED_READINESS_CHECKS:
            if checks.get(check_name) is not True:
                block_reasons.append(f"readiness_check_{check_name}_not_pass")
        if checks.get("install_allowed") is not True:
            block_reasons.append("install_not_allowed_by_readiness")
        if checks.get("target_repo_allowlisted") is not True:
            block_reasons.append("target_repo_not_allowlisted_by_readiness")

    if target_repo not in allowlist:
        block_reasons.append("target_repo_not_allowlisted_for_controlled_testrepo_bootstrap")
    if not _has_exact_approval_phrase(raw_approval_text):
        block_reasons.append("missing_exact_approval_phrase")
    if _has_raw_sensitive_input((readiness_decision, {"approval_text": raw_approval_text})):
        block_reasons.append("unsafe_raw_approval_or_secret_material_detected")

    if block_reasons:
        return _block(*block_reasons, target_repo=target_repo)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "CONTROLLED_LOCAL_TESTREPO_BOOTSTRAP_APPROVAL_PACKET_READY",
        "gate": "PASS",
        "APPROVAL_PACKET_READY": True,
        "BOOTSTRAP_EXECUTION_ALLOWED": False,
        "target_repo": _safe_output_text(target_repo),
        "approval_text_echoed": False,
        "approval_phrase_received": True,
        "allowed_actions": [_ALLOWED_ACTION],
        "source_readiness_schema": readiness_decision.get("schema_version"),
        "source_readiness_gate": readiness_decision.get("gate"),
        "source_next_required_human_action": readiness_decision.get("NEXT_REQUIRED_HUMAN_ACTION"),
        "NEXT_REQUIRED_HUMAN_ACTION": _READY_NEXT_ACTION,
        "side_effects": [],
    }


def _load_json(path: str | None) -> Any:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _append_block_reason(packet: dict[str, Any], reason: str) -> dict[str, Any]:
    packet["block_reasons"] = _dedupe([*packet.get("block_reasons", []), reason])
    return packet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a controlled side-effect-free local testrepo bootstrap approval packet."
    )
    parser.add_argument("--readiness-decision")
    parser.add_argument("--approval-input")
    args = parser.parse_args(argv)

    approval_input = _load_json(args.approval_input)
    approval_input_block_reason = ""
    if not isinstance(approval_input, dict):
        approval_input = {}
        approval_input_block_reason = "approval_input_payload_not_object"
    result = build_controlled_bootstrap_approval_packet(
        readiness_decision=_load_json(args.readiness_decision),
        approval_text=approval_input.get("approval_text", ""),
        allowed_test_repos=approval_input.get("allowed_test_repos", []),
    )
    if approval_input_block_reason:
        result = _append_block_reason(result, approval_input_block_reason)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("APPROVAL_PACKET_READY") is True else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
