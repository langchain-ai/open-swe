"""Collect platform-verified Drupal runtime gate evidence."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

CommandRunner = Callable[[str, str, int], Awaitable[dict[str, Any]]]

DEFAULT_GATE_TIMEOUT_SECONDS = 120


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _preview_url(result: Mapping[str, Any], runtime: Mapping[str, Any]) -> str:
    qa = _mapping(result.get("qa_evidence"))
    return _string(result.get("preview_url") or qa.get("preview_url") or runtime.get("preview_url"))


def _runtime_profile(item: Mapping[str, Any]) -> dict[str, Any]:
    sandbox_profile = _mapping(item.get("sandbox_profile"))
    runtime = _mapping(sandbox_profile.get("runtime"))
    if runtime:
        return runtime
    if _string(sandbox_profile.get("provider")).lower() == "ddev":
        return {
            "provider": "ddev",
            "project_path": sandbox_profile.get("project_path"),
            "preview_url": sandbox_profile.get("preview_url"),
            "gates": sandbox_profile.get("gates"),
        }
    return {}


def _artifact_bucket(artifact_type: str) -> str | None:
    normalized = artifact_type.lower()
    if normalized in {"screenshot", "screenshots"}:
        return "screenshots"
    if normalized in {"video", "videos"}:
        return "videos"
    if normalized in {"trace", "traces", "playwright_trace"}:
        return "traces"
    return None


def _format_command(
    command: str,
    *,
    project_path: str,
    preview_url: str,
    artifact_path: str,
) -> str:
    return (
        command.replace("{project_path}", shlex.quote(project_path))
        .replace("{preview_url}", shlex.quote(preview_url))
        .replace("{artifact_path}", shlex.quote(artifact_path))
    )


async def run_shell_gate_command(command: str, cwd: str, timeout_seconds: int) -> dict[str, Any]:
    """Run a configured platform gate command."""
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd or None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        stdout, _ = await process.communicate()
        output = stdout.decode(errors="replace")
        return {"exit_code": 124, "output": output, "timed_out": True}
    output = stdout.decode(errors="replace")
    return {"exit_code": process.returncode or 0, "output": output}


async def _run_gate(
    gate: Mapping[str, Any],
    *,
    project_path: str,
    preview_url: str,
    runner: CommandRunner,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    name = _string(gate.get("name") or gate.get("id")) or "unknown"
    command = _string(gate.get("command"))
    if not command:
        return (
            {
                "name": name,
                "status": "failed",
                "source": "platform",
                "platform_verified": True,
                "output": "Gate command is missing.",
            },
            {},
        )
    artifact_path = _string(gate.get("artifact_path") or gate.get("artifactPath"))
    command = _format_command(
        command,
        project_path=project_path,
        preview_url=preview_url,
        artifact_path=artifact_path,
    )
    timeout = gate.get("timeout_seconds") or gate.get("timeoutSeconds")
    timeout_seconds = (
        timeout if isinstance(timeout, int) and timeout > 0 else DEFAULT_GATE_TIMEOUT_SECONDS
    )
    cwd = _string(gate.get("cwd")) or project_path
    result = await runner(command, cwd, timeout_seconds)
    exit_code = result.get("exit_code")
    output = _string(result.get("output"))
    status = "passed" if exit_code == 0 else "failed"
    artifacts: dict[str, list[str]] = {}
    artifact_type = _string(gate.get("artifact_type") or gate.get("artifactType"))
    bucket = _artifact_bucket(artifact_type)
    if bucket and artifact_path:
        if Path(artifact_path).exists():
            artifacts[bucket] = [artifact_path]
        else:
            status = "failed"
            output = f"{output}\nExpected artifact not found: {artifact_path}".strip()
    return (
        {
            "name": name,
            "status": status,
            "source": "platform",
            "platform_verified": True,
            "command": command,
            "exit_code": exit_code,
            "output": output[-2000:],
        },
        artifacts,
    )


async def collect_drupal_runtime_evidence(
    item: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Augment a worker result with platform-collected Drupal runtime evidence."""
    runtime = _runtime_profile(item)
    gates = [gate for gate in _list(runtime.get("gates")) if isinstance(gate, Mapping)]
    if not gates:
        return dict(result)
    project_path = _string(runtime.get("project_path") or runtime.get("path"))
    if not project_path or not os.path.isdir(project_path):
        failed = {
            "name": "drupal_runtime",
            "status": "failed",
            "source": "platform",
            "platform_verified": True,
            "output": "Drupal runtime project_path is missing or not a directory.",
        }
        return {
            **dict(result),
            "executed_gates": [*_list(result.get("executed_gates")), failed],
        }
    preview_url = _preview_url(result, runtime)
    command_runner = runner or run_shell_gate_command
    platform_gates: list[dict[str, Any]] = []
    artifact_updates: dict[str, list[str]] = {"screenshots": [], "videos": [], "traces": []}
    for gate in gates:
        gate_result, artifacts = await _run_gate(
            gate,
            project_path=project_path,
            preview_url=preview_url,
            runner=command_runner,
        )
        platform_gates.append(gate_result)
        for bucket, paths in artifacts.items():
            artifact_updates.setdefault(bucket, []).extend(paths)

    return {
        **dict(result),
        "preview_url": _string(result.get("preview_url")) or preview_url,
        "executed_gates": [*_list(result.get("executed_gates")), *platform_gates],
        "screenshots": [*_list(result.get("screenshots")), *artifact_updates["screenshots"]],
        "videos": [*_list(result.get("videos")), *artifact_updates["videos"]],
        "traces": [*_list(result.get("traces")), *artifact_updates["traces"]],
        "platform_evidence": {
            "provider": _string(runtime.get("provider")) or "drupal",
            "project_path": project_path,
            "preview_url": preview_url,
            "gates": platform_gates,
            "artifacts": artifact_updates,
        },
    }
