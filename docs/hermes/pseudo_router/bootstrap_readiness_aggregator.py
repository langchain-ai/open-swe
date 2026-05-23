"""Dry-run bootstrap readiness aggregator for Hermes Northstar harness.

This module is the final fan-in gate before a human may approve a controlled
local testrepo bootstrap. It is intentionally pure: it only reads JSON inputs
provided by the caller, emits one machine-readable decision, and performs no
server starts, GitHub App setup, webhook setup, push/PR, production deploy, or
/erp edits.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "hermes.bootstrap-readiness-aggregator.v1"
EXECUTION_MANIFEST_SCHEMA_VERSION = "hermes.bootstrap-execution-manifest.v1"
INSTALL_POLICY_SCHEMA_VERSION = "hermes.bootstrap-install-policy.v1"

_DEFAULT_NEXT_ACTION = "produce bootstrap readiness inputs"
_READY_NEXT_ACTION = "approve controlled local testrepo bootstrap"

_EXECUTION_LIVE_ACTION_KEYS = (
    "may_create_github_app",
    "may_configure_webhook",
    "may_start_server",
    "may_push_or_pr",
    "may_deploy_prod",
)

_TESTREPO_LIVE_FLAG_KEYS = (
    "ALLOW_BOOTSTRAP_INSTALL",
    "ALLOW_WEBHOOK_SETUP",
    "ALLOW_GITHUB_APP_SETUP",
    "ALLOW_PROD_INSTALL",
    "ALLOW_DOCKER_BUILD",
)

_SECRET_LIKE_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_RAW_TEXT_KEY_RE = re.compile(
    r"(?i)(approval_text|human_approval|raw_approval|comment_body|secret|token|password|private_key)"
)


def _empty_input() -> dict[str, Any]:
    return {}


def _normalize_repo(repo: Any) -> str:
    return str(repo or "").strip().lower()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _normalized_allowlist(*inputs: dict[str, Any]) -> list[str]:
    repos: set[str] = set()
    for payload in inputs:
        for key in ("allowed_test_repos", "allowed_repos"):
            for repo in _as_list(payload.get(key)):
                normalized = _normalize_repo(repo)
                if normalized:
                    repos.add(normalized)
    return sorted(repos)


def _target_repo(install_policy: dict[str, Any], testrepo_gate: dict[str, Any]) -> str:
    return _normalize_repo(install_policy.get("target_repo") or testrepo_gate.get("target_repo"))


def _bool_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "yes", "true", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _execution_manifest_ready(execution_manifest: dict[str, Any], block_reasons: list[str]) -> bool:
    if not execution_manifest:
        block_reasons.append("missing_execution_manifest")
        return False

    ready = True
    if execution_manifest.get("schema_version") != EXECUTION_MANIFEST_SCHEMA_VERSION:
        block_reasons.append("execution_manifest_schema_mismatch")
        ready = False
    if execution_manifest.get("status") != "READY_FOR_HUMAN_APPROVAL_CHECKLIST":
        block_reasons.append("execution_manifest_not_ready_checklist")
        ready = False
    if execution_manifest.get("gate") != "WARN":
        block_reasons.append("execution_manifest_gate_not_warn")
        ready = False
    if execution_manifest.get("side_effects") != []:
        block_reasons.append("execution_manifest_has_side_effects")
        ready = False

    hard_disabled = execution_manifest.get("hard_disabled_actions")
    if not isinstance(hard_disabled, dict):
        block_reasons.append("execution_manifest_missing_hard_disabled_actions")
        return False
    if any(key not in hard_disabled for key in _EXECUTION_LIVE_ACTION_KEYS):
        block_reasons.append("execution_manifest_missing_live_action_disable_evidence")
        ready = False
    live_keys = [key for key in _EXECUTION_LIVE_ACTION_KEYS if _bool_true(hard_disabled.get(key))]
    if live_keys:
        block_reasons.append("execution_manifest_live_actions_enabled")
        ready = False
    return ready


def _install_policy_allowed(install_policy: dict[str, Any], block_reasons: list[str]) -> bool:
    if not install_policy:
        block_reasons.append("missing_install_policy")
        return False

    allowed = True
    if install_policy.get("schema_version") != INSTALL_POLICY_SCHEMA_VERSION:
        block_reasons.append("install_policy_schema_mismatch")
        allowed = False
    if install_policy.get("status") != "POLICY_EVALUATED":
        block_reasons.append("install_policy_not_evaluated")
        allowed = False
    if install_policy.get("side_effects") != []:
        block_reasons.append("install_policy_has_side_effects")
        allowed = False
    if (
        install_policy.get("INSTALL_ALLOWED") is not True
        or install_policy.get("install_allowed") is not True
    ):
        block_reasons.append("install_policy_not_allowed")
        allowed = False

    allowed_actions = install_policy.get("allowed_actions")
    if not isinstance(allowed_actions, list) or not allowed_actions:
        block_reasons.append("install_policy_missing_allowed_actions")
        allowed = False
    elif any(not str(action).startswith("dry_run_") for action in allowed_actions):
        block_reasons.append("install_policy_allows_non_dry_run_actions")
        allowed = False
    return allowed


def _scanners_and_audits_green(readiness_status: dict[str, Any], block_reasons: list[str]) -> bool:
    if not readiness_status:
        block_reasons.append("missing_readiness_status")
        return False

    green = True
    if readiness_status.get("side_effects") not in (None, []):
        block_reasons.append("readiness_status_has_side_effects")
        green = False
    gate = str(readiness_status.get("gate") or readiness_status.get("status") or "").upper()
    if gate not in {"PASS", "OK", "GREEN"}:
        block_reasons.append("readiness_gate_not_pass")
        green = False

    for section_name in ("scanners", "audits"):
        section = readiness_status.get(section_name, {})
        if not isinstance(section, dict):
            block_reasons.append(f"{section_name}_status_invalid")
            green = False
            continue
        if not section:
            block_reasons.append(f"{section_name}_status_missing")
            green = False
            continue
        failing = [
            name
            for name, value in section.items()
            if str(value).upper() not in {"PASS", "OK", "GREEN"}
        ]
        if failing:
            block_reasons.append(f"{section_name}_not_green")
            green = False
    return green


def _testrepo_gate_ok(testrepo_gate: dict[str, Any], block_reasons: list[str]) -> bool:
    if not testrepo_gate:
        block_reasons.append("missing_testrepo_gate")
        return False

    ok = True
    gate = str(testrepo_gate.get("gate") or testrepo_gate.get("status") or "").upper()
    if gate not in {"PASS", "OK", "GREEN"}:
        block_reasons.append("testrepo_gate_not_pass")
        ok = False
    if testrepo_gate.get("side_effects") not in (None, []):
        block_reasons.append("testrepo_gate_has_side_effects")
        ok = False

    live_flags = testrepo_gate.get("live_actions_enabled", {})
    if not isinstance(live_flags, dict):
        block_reasons.append("testrepo_gate_live_actions_invalid")
        return False
    if any(key not in live_flags for key in _TESTREPO_LIVE_FLAG_KEYS):
        block_reasons.append("testrepo_gate_missing_live_action_disable_evidence")
        ok = False
    if [key for key in _TESTREPO_LIVE_FLAG_KEYS if _bool_true(live_flags.get(key))]:
        block_reasons.append("testrepo_gate_live_actions_enabled")
        ok = False
    return ok


def _target_repo_allowlisted(
    install_policy: dict[str, Any], testrepo_gate: dict[str, Any], block_reasons: list[str]
) -> bool:
    target_repo = _target_repo(install_policy, testrepo_gate)
    allowlist = _normalized_allowlist(install_policy, testrepo_gate)
    if not target_repo or target_repo not in allowlist:
        block_reasons.append("target_repo_not_allowlisted_for_testrepo_bootstrap")
        return False
    return True


def _contains_secret_like_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_LIKE_PATTERNS)


def _has_raw_sensitive_input(payload: Any, *, parent_key: str = "") -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if _RAW_TEXT_KEY_RE.search(key_text) and value not in (None, "", False):
                return True
            if _has_raw_sensitive_input(value, parent_key=key_text):
                return True
        return False
    if isinstance(payload, list | tuple):
        return any(_has_raw_sensitive_input(item, parent_key=parent_key) for item in payload)
    if isinstance(payload, str):
        return _contains_secret_like_text(payload)
    return False


def _no_secret_or_approval_echo(
    inputs: tuple[dict[str, Any], ...], block_reasons: list[str]
) -> bool:
    if any(_has_raw_sensitive_input(payload) for payload in inputs):
        block_reasons.append("unsafe_raw_approval_or_secret_material_detected")
        return False
    return True


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def aggregate_bootstrap_readiness(
    *,
    execution_manifest: dict[str, Any] | None = None,
    install_policy: dict[str, Any] | None = None,
    readiness_status: dict[str, Any] | None = None,
    testrepo_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one side-effect-free bootstrap readiness decision."""

    execution_manifest = execution_manifest or _empty_input()
    install_policy = install_policy or _empty_input()
    readiness_status = readiness_status or _empty_input()
    testrepo_gate = testrepo_gate or _empty_input()

    block_reasons: list[str] = []
    checks = {
        "execution_manifest_ready_checklist": _execution_manifest_ready(
            execution_manifest, block_reasons
        ),
        "install_allowed": _install_policy_allowed(install_policy, block_reasons),
        "target_repo_allowlisted": _target_repo_allowlisted(
            install_policy, testrepo_gate, block_reasons
        ),
        "scanners_and_audits_green": _scanners_and_audits_green(readiness_status, block_reasons),
        "live_actions_disabled": _testrepo_gate_ok(testrepo_gate, block_reasons),
        "no_secret_or_approval_echo": _no_secret_or_approval_echo(
            (execution_manifest, install_policy, readiness_status, testrepo_gate), block_reasons
        ),
    }
    block_reasons = _dedupe(block_reasons)
    ready = all(checks.values()) and not block_reasons

    target_repo = _target_repo(install_policy, testrepo_gate)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "BOOTSTRAP_READY_FOR_HUMAN_APPROVAL" if ready else "BOOTSTRAP_NOT_READY",
        "gate": "PASS" if ready else "WARN",
        "BOOTSTRAP_READY": ready,
        "target_repo": target_repo,
        "checks": checks,
        "block_reasons": block_reasons,
        "NEXT_REQUIRED_HUMAN_ACTION": _READY_NEXT_ACTION
        if ready
        else _next_required_action(block_reasons),
        "side_effects": [],
    }


def _next_required_action(block_reasons: list[str]) -> str:
    if not block_reasons:
        return _READY_NEXT_ACTION
    if any(reason.startswith("missing_") for reason in block_reasons):
        return _DEFAULT_NEXT_ACTION
    if "unsafe_raw_approval_or_secret_material_detected" in block_reasons:
        return "remove raw approvals or secret-like material from readiness inputs"
    if "install_policy_not_allowed" in block_reasons:
        return "obtain exact dry-run install policy approval for the allowlisted testrepo"
    if "target_repo_not_allowlisted_for_testrepo_bootstrap" in block_reasons:
        return "select an allowlisted test repository"
    if any("live_actions" in reason for reason in block_reasons):
        return "disable all live bootstrap actions"
    if any(
        "scanner" in reason or "audit" in reason or "readiness" in reason
        for reason in block_reasons
    ):
        return "rerun readiness scanners until green"
    return "resolve bootstrap readiness blockers"


def _load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate dry-run Hermes bootstrap readiness JSON into one decision."
    )
    parser.add_argument("--execution-manifest")
    parser.add_argument("--install-policy")
    parser.add_argument("--readiness-status")
    parser.add_argument("--testrepo-gate")
    args = parser.parse_args(argv)

    result = aggregate_bootstrap_readiness(
        execution_manifest=_load_json(args.execution_manifest),
        install_policy=_load_json(args.install_policy),
        readiness_status=_load_json(args.readiness_status),
        testrepo_gate=_load_json(args.testrepo_gate),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("BOOTSTRAP_READY") is True else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
