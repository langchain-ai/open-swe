"""File-only bootstrap packet renderer for Hermes Northstar harness.

This module renders the side-effect-free output from
``testrepo_bootstrap_approval_gate`` into a human-readable Markdown approval
packet. It never creates a GitHub App, configures webhooks, writes files, pushes
branches, starts servers, or touches /erp. The CLI prints Markdown to stdout so a
separate explicit command can choose where to store it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = "hermes.bootstrap-packet-renderer.v1"
_TITLE = "Hermes Northstar Testrepo Bootstrap Packet"


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false"


def _safe_task_id(task_id: Any) -> str:
    raw = str(task_id or "UNKNOWN").strip() or "UNKNOWN"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw)[:80]


def _recommended_filename(packet: dict[str, Any]) -> str:
    return f"docs/bootstrap/BOOTSTRAP_PACKET_{_safe_task_id(packet.get('TASK_ID'))}.md"


def _bullet_list(items: list[str]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {item}" for item in items]


def _blocked_markdown(packet: dict[str, Any], reason: str) -> str:
    task_id = packet.get("TASK_ID") or "UNKNOWN"
    lines = [
        f"# {_TITLE}",
        "",
        "Verification evidence:",
        f"- Renderer schema: {SCHEMA_VERSION}",
        "- Input packet status: BLOCKED or unsafe",
        "- Side effects performed by renderer: none",
        "",
        "Status: BLOCKED",
        f"TASK_ID: {task_id}",
        f"Block reason: {reason}",
        "",
        "No bootstrap approval phrase is emitted for blocked packets.",
        "Do not create a GitHub App, configure webhooks, start servers, push, or edit /erp.",
        "",
        "GATE=FAIL",
        f"GATE_REASON={reason}",
    ]
    return "\n".join(lines) + "\n"


def _approval_markdown(packet: dict[str, Any]) -> str:
    approval = cast(
        dict[str, Any],
        packet.get("approval_packet") if isinstance(packet.get("approval_packet"), dict) else {},
    )
    final_status = str(packet.get("status", "UNKNOWN"))
    task_id = packet.get("TASK_ID") or "UNKNOWN"
    target_repo = str(packet.get("target_repo") or approval.get("target_repo") or "UNKNOWN")
    required_approval = str(approval.get("required_exact_approval", ""))
    requested_external_setup = _as_list(approval.get("requested_external_setup"))
    allowed_trigger_users = _as_list(approval.get("allowed_trigger_users"))
    verification_required = _as_list(approval.get("verification_required_before_pass"))

    lines = [
        f"# {_TITLE}",
        "",
        "Verification evidence:",
        f"- Renderer schema: {SCHEMA_VERSION}",
        f"- Source approval schema: {packet.get('schema_version')}",
        f"- Source pipeline schema: {approval.get('source_pipeline_schema')}",
        "- Side effects performed by renderer: none",
        "- Output type: file-only Markdown approval packet",
        "",
        f"Status: {final_status}",
        f"TASK_ID: {task_id}",
        f"Target repo: {target_repo}",
        f"Source pipeline gate: {approval.get('source_pipeline_gate')}",
        f"Source next action: {approval.get('source_next_action')}",
        f"Sandbox type: {approval.get('sandbox_type')}",
        "",
        "Required exact approval:",
        required_approval,
        "",
        "Allowed trigger users:",
        *_bullet_list(allowed_trigger_users),
        "",
        "Requested external setup (not executed by this packet):",
        *_bullet_list(requested_external_setup),
        "",
        "Verification required before final PASS:",
        *_bullet_list(verification_required),
        "",
        "Hard disabled actions:",
        f"- may_create_github_app: {_bool_text(approval.get('may_create_github_app'))}",
        f"- may_configure_webhook: {_bool_text(approval.get('may_configure_webhook'))}",
        f"- may_push_or_pr: {_bool_text(approval.get('may_push_or_pr'))}",
        f"- may_start_server: {_bool_text(approval.get('may_start_server'))}",
        f"- may_edit_erp: {_bool_text(approval.get('may_edit_erp'))}",
        "",
        "Recommended next prompt:",
        "Efter separat approval, kör endast testrepo bootstrap dry-run mot allowlistat testrepo. ",
        "Skapa ingen GitHub App/webhook/push/PR/server utan explicit ALLOW_BOOTSTRAP_INSTALL=YES.",
        "",
        f"GATE={packet.get('gate')}",
        "GATE_REASON=File-only bootstrap approval packet rendered without side effects; external setup remains disabled.",
    ]
    return "\n".join(lines) + "\n"


def render_bootstrap_packet_markdown(approval_packet: dict[str, Any]) -> dict[str, Any]:
    """Render an approval-gate packet into safe Markdown and metadata.

    Returns a JSON-serializable dict for tests/automation. The renderer blocks any
    input that already claims side effects, because approval packets must be pure
    decision artifacts.
    """

    if approval_packet.get("side_effects"):
        reason = "input_packet_must_be_side_effect_free"
        return {
            "status": "BLOCKED",
            "schema_version": SCHEMA_VERSION,
            "gate": "FAIL",
            "block_reason": reason,
            "TASK_ID": approval_packet.get("TASK_ID"),
            "recommended_filename": _recommended_filename(approval_packet),
            "markdown": _blocked_markdown(approval_packet, reason),
            "side_effects": [],
        }

    if approval_packet.get("status") == "BLOCKED":
        reason = str(approval_packet.get("block_reason") or "blocked_input_packet")
        return {
            "status": "BLOCKED",
            "schema_version": SCHEMA_VERSION,
            "gate": "FAIL",
            "block_reason": reason,
            "TASK_ID": approval_packet.get("TASK_ID"),
            "recommended_filename": _recommended_filename(approval_packet),
            "markdown": _blocked_markdown(approval_packet, reason),
            "side_effects": [],
        }

    return {
        "status": approval_packet.get("status"),
        "schema_version": SCHEMA_VERSION,
        "gate": approval_packet.get("gate"),
        "TASK_ID": approval_packet.get("TASK_ID"),
        "recommended_filename": _recommended_filename(approval_packet),
        "markdown": _approval_markdown(approval_packet),
        "side_effects": [],
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: bootstrap_packet_renderer.py <approval_packet.json>", file=sys.stderr)
        return 2
    rendered = render_bootstrap_packet_markdown(_load_json(Path(argv[1])))
    print(rendered["markdown"], end="")
    return 0 if rendered.get("status") != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
