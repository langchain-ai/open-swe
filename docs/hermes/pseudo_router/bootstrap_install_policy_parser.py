"""Dry-run ALLOW_BOOTSTRAP_INSTALL policy parser for Hermes Northstar harness.

This module only interprets human approval text that was already provided to it.
It does not read secrets, start services, create GitHub Apps, configure webhooks,
push branches, open PRs, or perform any other bootstrap side effect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

SCHEMA_VERSION = "hermes.bootstrap-install-policy.v1"
EXACT_APPROVAL_PHRASE = "ALLOW_BOOTSTRAP_INSTALL=YES"
_ALLOWED_ACTION = "dry_run_testrepo_bootstrap_install"

_SECRET_LIKE_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _normalize_repo(repo: Any) -> str:
    return str(repo or "").strip().lower()


def _has_exact_approval_phrase(approval_text: str) -> bool:
    """Require the approval phrase as an exact standalone line.

    This intentionally rejects casing changes, inserted spaces, suffixes, shell
    command tails, and semicolon-separated instructions.
    """

    return any(line.strip() == EXACT_APPROVAL_PHRASE for line in approval_text.splitlines())


def _contains_secret_like_material(approval_text: str) -> bool:
    return any(pattern.search(approval_text) for pattern in _SECRET_LIKE_PATTERNS)


def evaluate_bootstrap_install_policy(
    *, approval_text: str | None, target_repo: str, allowed_test_repos: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """Evaluate whether a dry-run testrepo bootstrap install is allowed.

    Default is always ``INSTALL_ALLOWED=false``. The only allow path is:
    1. human text contains an exact standalone ``ALLOW_BOOTSTRAP_INSTALL=YES`` line;
    2. target repo is in the explicit testrepo allowlist;
    3. approval text does not contain secret-like material.

    The returned object never echoes the raw human approval text.
    """

    raw_text = approval_text or ""
    normalized_target_repo = _normalize_repo(target_repo)
    normalized_allowlist = sorted(
        {_normalize_repo(repo) for repo in allowed_test_repos if _normalize_repo(repo)}
    )

    block_reasons: list[str] = []
    if not _has_exact_approval_phrase(raw_text):
        block_reasons.append("missing_exact_approval_phrase")
    if normalized_target_repo not in normalized_allowlist:
        block_reasons.append("target_repo_not_allowlisted_for_testrepo_bootstrap")
    if _contains_secret_like_material(raw_text):
        block_reasons.append("secret_like_approval_text_detected")

    install_allowed = not block_reasons
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "POLICY_EVALUATED",
        "gate": "PASS" if install_allowed else "WARN",
        "install_allowed": install_allowed,
        "INSTALL_ALLOWED": install_allowed,
        "target_repo": normalized_target_repo,
        "allowed_test_repos": normalized_allowlist,
        "required_exact_approval": EXACT_APPROVAL_PHRASE,
        "approval_text_echoed": False,
        "allowed_actions": [_ALLOWED_ACTION] if install_allowed else [],
        "block_reasons": block_reasons,
        "side_effects": [],
    }


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate dry-run Hermes bootstrap install approval policy."
    )
    parser.add_argument("policy_input_json")
    args = parser.parse_args(argv)

    payload = _load_json(args.policy_input_json)
    result = evaluate_bootstrap_install_policy(
        approval_text=payload.get("approval_text", ""),
        target_repo=payload.get("target_repo", ""),
        allowed_test_repos=payload.get("allowed_test_repos", []),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("install_allowed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
