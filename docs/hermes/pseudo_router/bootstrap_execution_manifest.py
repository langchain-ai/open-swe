"""Dry-run bootstrap execution manifest for Hermes Northstar harness.

The manifest is intentionally non-executable. It consumes the previous packet
writer result, verifies that the only completed side effect was writing a dry-run
Markdown packet under ``docs/bootstrap/``, and returns a human checklist for
future bootstrap actions that still require explicit ``ALLOW_BOOTSTRAP_INSTALL=YES``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

WRITER_SCHEMA_VERSION = "hermes.bootstrap-packet-writer.v1"
SCHEMA_VERSION = "hermes.bootstrap-execution-manifest.v1"
_ALLOWED_PREFIX = Path("docs/bootstrap")
_ALLOWED_WRITER_SIDE_EFFECTS = ["wrote_markdown_file"]


_MANUAL_STEPS_REQUIRING_APPROVAL = [
    "Create GitHub App with minimum issue/comment/metadata permissions for the testrepo only.",
    "Configure webhook delivery URL and signature secret after a non-public local/dev endpoint is chosen.",
    "Configure token encryption and secret storage without printing or copying secret values into logs.",
    "Start Open SWE server or worker processes in local dev mode after bootstrap install approval.",
    "Connect the allowlisted testrepo before any Northstar production repository connection.",
    "Push branch or open PR only after a separate source-diff, reviewer, and human approval gate.",
]


_HARD_DISABLED_ACTIONS = {
    "may_create_github_app": False,
    "may_configure_webhook": False,
    "may_start_server": False,
    "may_push_or_pr": False,
    "may_deploy_prod": False,
}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _block(reason: str, *, task_id: Any = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "schema_version": SCHEMA_VERSION,
        "gate": "FAIL",
        "block_reason": reason,
        "TASK_ID": task_id,
        "side_effects": [],
    }


def _validate_source_packet_path(
    writer_result: dict[str, Any], repo_root: Path
) -> tuple[str | None, dict[str, Any] | None]:
    raw_relative_path = str(writer_result.get("relative_path") or "").strip()
    if not raw_relative_path:
        return None, _block("missing_source_packet_path", task_id=writer_result.get("TASK_ID"))

    relative_path = Path(raw_relative_path)
    if relative_path.is_absolute():
        return None, _block(
            "source_packet_path_must_be_relative", task_id=writer_result.get("TASK_ID")
        )
    if relative_path.suffix.lower() != ".md":
        return None, _block("source_packet_must_be_markdown", task_id=writer_result.get("TASK_ID"))
    if any(part == ".." for part in relative_path.parts):
        return None, _block(
            "source_packet_must_stay_under_docs_bootstrap", task_id=writer_result.get("TASK_ID")
        )

    root = repo_root.resolve()
    source_path = (root / relative_path).resolve()
    allowed_root = (root / _ALLOWED_PREFIX).resolve()
    if not _is_relative_to(source_path, allowed_root):
        return None, _block(
            "source_packet_must_stay_under_docs_bootstrap", task_id=writer_result.get("TASK_ID")
        )

    erp_root = Path("/erp").resolve()
    raw_absolute_path = str(writer_result.get("absolute_path") or "").strip()
    if raw_absolute_path:
        absolute_path = Path(raw_absolute_path).resolve()
        if absolute_path == erp_root or _is_relative_to(absolute_path, erp_root):
            return None, _block(
                "source_packet_must_not_touch_erp", task_id=writer_result.get("TASK_ID")
            )
        if absolute_path != source_path:
            return None, _block(
                "source_packet_absolute_path_must_match_repo_root",
                task_id=writer_result.get("TASK_ID"),
            )

    return source_path.relative_to(root).as_posix(), None


def _render_markdown(*, task_id: Any, source_packet: str, writer_schema_version: str | None) -> str:
    steps = "\n".join(
        f"- [ ] {step} Requires: ALLOW_BOOTSTRAP_INSTALL=YES"
        for step in _MANUAL_STEPS_REQUIRING_APPROVAL
    )
    disabled = "\n".join(
        f"- {key}: {str(value).lower()}" for key, value in _HARD_DISABLED_ACTIONS.items()
    )
    return f"""# Hermes Bootstrap Execution Manifest

Verification evidence:
- Manifest schema: {SCHEMA_VERSION}
- Source writer schema: {writer_schema_version or "unknown"}
- Source writer status: WROTE_DRY_RUN_PACKET
- Source dry-run packet: {source_packet}
- Side effects performed by manifest: none
- Execution mode: dry-run human approval checklist only

Status: READY_FOR_HUMAN_APPROVAL_CHECKLIST
TASK_ID: {task_id}
Required exact approval before any install/bootstrap side effect: ALLOW_BOOTSTRAP_INSTALL=YES

Hard disabled actions in this manifest:
{disabled}

Manual steps still requiring ALLOW_BOOTSTRAP_INSTALL=YES:
{steps}

Explicitly not performed:
- No GitHub App created.
- No webhook configured.
- No server or worker started.
- No branch pushed and no PR opened.
- No production deploy performed.
- No /erp product code edited.

GATE=WARN
GATE_REASON=Dry-run execution manifest produced a human checklist only; bootstrap side effects remain blocked until explicit ALLOW_BOOTSTRAP_INSTALL=YES.
"""


def build_bootstrap_execution_manifest(
    writer_result: dict[str, Any], *, repo_root: str | Path = "."
) -> dict[str, Any]:
    """Build a non-executing bootstrap approval checklist from writer output."""

    task_id = writer_result.get("TASK_ID")
    if writer_result.get("schema_version") != WRITER_SCHEMA_VERSION:
        return _block("requires_bootstrap_packet_writer_result", task_id=task_id)
    if writer_result.get("status") != "WROTE_DRY_RUN_PACKET":
        return _block("requires_wrote_dry_run_packet_status", task_id=task_id)
    if writer_result.get("side_effects") != _ALLOWED_WRITER_SIDE_EFFECTS:
        return _block("writer_result_has_unexpected_side_effects", task_id=task_id)

    source_packet, path_block = _validate_source_packet_path(writer_result, Path(repo_root))
    if path_block:
        return path_block
    assert source_packet is not None

    markdown = _render_markdown(
        task_id=task_id,
        source_packet=source_packet,
        writer_schema_version=writer_result.get("schema_version"),
    )
    return {
        "status": "READY_FOR_HUMAN_APPROVAL_CHECKLIST",
        "schema_version": SCHEMA_VERSION,
        "gate": "WARN",
        "TASK_ID": task_id,
        "source_packet": source_packet,
        "required_exact_approval": "ALLOW_BOOTSTRAP_INSTALL=YES",
        "manual_steps_requiring_allow_bootstrap_install": list(_MANUAL_STEPS_REQUIRING_APPROVAL),
        "hard_disabled_actions": dict(_HARD_DISABLED_ACTIONS),
        "markdown": markdown,
        "side_effects": [],
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a dry-run Hermes bootstrap execution manifest."
    )
    parser.add_argument("writer_result_json")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    result = build_bootstrap_execution_manifest(
        _load_json(Path(args.writer_result_json)), repo_root=args.repo_root
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "READY_FOR_HUMAN_APPROVAL_CHECKLIST" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
