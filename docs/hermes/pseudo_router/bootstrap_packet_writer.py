"""Dry-run bootstrap packet writer for Hermes Northstar harness.

This module is intentionally narrow: it accepts an already-rendered bootstrap
packet JSON object and writes only that Markdown under ``docs/bootstrap/`` in the
current harness copy. It never creates a GitHub App, configures webhooks, starts
servers, pushes branches, deploys, or touches /erp.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

RENDERER_SCHEMA_VERSION = "hermes.bootstrap-packet-renderer.v1"
SCHEMA_VERSION = "hermes.bootstrap-packet-writer.v1"
_ALLOWED_PREFIX = Path("docs/bootstrap")


def _block(reason: str, *, task_id: Any = None, output_path: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "schema_version": SCHEMA_VERSION,
        "gate": "FAIL",
        "block_reason": reason,
        "TASK_ID": task_id,
        "side_effects": [],
    }
    if output_path is not None:
        result["output_path"] = str(output_path)
    return result


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_rendered_packet(rendered_packet: dict[str, Any]) -> dict[str, Any] | None:
    if rendered_packet.get("schema_version") != RENDERER_SCHEMA_VERSION:
        return _block("requires_rendered_packet_json", task_id=rendered_packet.get("TASK_ID"))
    if not isinstance(rendered_packet.get("markdown"), str) or not rendered_packet.get("markdown"):
        return _block("requires_rendered_packet_json", task_id=rendered_packet.get("TASK_ID"))
    if rendered_packet.get("side_effects"):
        return _block(
            "rendered_packet_must_be_side_effect_free", task_id=rendered_packet.get("TASK_ID")
        )
    return None


def _validate_output_path(
    raw_output_path: Any, repo_root: Path
) -> tuple[Path | None, dict[str, Any] | None]:
    output_path = str(raw_output_path or "").strip()
    if not output_path:
        return None, _block("missing_output_path", output_path=raw_output_path)

    relative_path = Path(output_path)
    if relative_path.is_absolute():
        return None, _block("output_path_must_be_relative", output_path=output_path)

    if relative_path.suffix.lower() != ".md":
        return None, _block("output_path_must_be_markdown", output_path=output_path)

    normalized_relative = Path(*relative_path.parts)
    if any(part == ".." for part in normalized_relative.parts):
        return None, _block("output_path_must_stay_under_docs_bootstrap", output_path=output_path)

    target_path = (repo_root / normalized_relative).resolve()
    allowed_root = (repo_root / _ALLOWED_PREFIX).resolve()
    if not _is_relative_to(target_path, allowed_root):
        return None, _block("output_path_must_stay_under_docs_bootstrap", output_path=output_path)

    erp_root = Path("/erp").resolve()
    if target_path == erp_root or _is_relative_to(target_path, erp_root):
        return None, _block("output_path_must_not_touch_erp", output_path=output_path)

    return target_path, None


def write_bootstrap_packet_markdown(
    rendered_packet: dict[str, Any],
    *,
    repo_root: str | Path = ".",
    output_path: str | None = None,
) -> dict[str, Any]:
    """Write rendered Markdown to ``docs/bootstrap`` under ``repo_root`` only."""

    packet_block = _validate_rendered_packet(rendered_packet)
    if packet_block:
        return packet_block

    raw_output_path = output_path or rendered_packet.get("recommended_filename")
    root = Path(repo_root).resolve()
    target_path, path_block = _validate_output_path(raw_output_path, root)
    if path_block:
        path_block["TASK_ID"] = rendered_packet.get("TASK_ID")
        return path_block
    assert target_path is not None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(str(rendered_packet["markdown"]), encoding="utf-8")

    relative_path = target_path.relative_to(root).as_posix()
    return {
        "status": "WROTE_DRY_RUN_PACKET",
        "schema_version": SCHEMA_VERSION,
        "gate": "PASS",
        "TASK_ID": rendered_packet.get("TASK_ID"),
        "relative_path": relative_path,
        "absolute_path": str(target_path),
        "bytes_written": target_path.stat().st_size,
        "side_effects": ["wrote_markdown_file"],
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print(
            "usage: bootstrap_packet_writer.py <rendered_packet.json> [docs/bootstrap/output.md]",
            file=sys.stderr,
        )
        return 2
    output_path = argv[2] if len(argv) == 3 else None
    result = write_bootstrap_packet_markdown(_load_json(Path(argv[1])), output_path=output_path)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "WROTE_DRY_RUN_PACKET" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
