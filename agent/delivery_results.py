"""Persist delivery worker results and verify PR QA evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .delivery_queue import read_delivery_queue_item, transition_delivery_queue_status
from .drupal_runtime_gates import CommandRunner, collect_drupal_runtime_evidence

_PASSING_GATE_STATUSES = frozenset({"pass", "passed", "success", "ok", "completed"})
_FAILING_GATE_STATUSES = frozenset({"fail", "failed", "failure", "error"})
_BROWSER_FILE_SUFFIXES = (
    ".css",
    ".scss",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".twig",
    ".html",
    ".vue",
    ".svelte",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return bool(value)
    if isinstance(value, Mapping):
        return bool(value)
    return value is not None


def _gate_name(gate: Mapping[str, Any]) -> str:
    for key in ("name", "id", "key"):
        value = _string(gate.get(key))
        if value:
            return value
    return "unknown"


def _gate_status(gate: Mapping[str, Any]) -> str:
    if gate.get("passed") is True or gate.get("ok") is True:
        return "passed"
    if gate.get("passed") is False or gate.get("ok") is False:
        return "failed"
    return _string(gate.get("status")).lower()


def _gate_passed(gate: Mapping[str, Any]) -> bool:
    return _gate_status(gate) in _PASSING_GATE_STATUSES


def _gate_failed(gate: Mapping[str, Any]) -> bool:
    return _gate_status(gate) in _FAILING_GATE_STATUSES


def _platform_verified(gate: Mapping[str, Any]) -> bool:
    evidence = _mapping(gate.get("evidence"))
    return (
        gate.get("platform_verified") is True
        or gate.get("verified") is True
        or evidence.get("verified") is True
        or _string(gate.get("source")) == "platform"
    )


def _gate_names(values: Any) -> set[str]:
    names: set[str] = set()
    for value in _list(values):
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
        elif isinstance(value, Mapping):
            name = _gate_name(value)
            if name != "unknown":
                names.add(name)
    return names


def _policy_gate_names(gate_policy: Mapping[str, Any]) -> set[str]:
    return _gate_names(gate_policy.get("blocking_gates")) | _gate_names(
        gate_policy.get("advisory_gates")
    )


def _without_blocking_gates(
    gate_policy: Mapping[str, Any],
    names: set[str],
) -> dict[str, Any]:
    policy = dict(gate_policy)
    blocking_gates: list[Any] = []
    for value in _list(gate_policy.get("blocking_gates")):
        if isinstance(value, str):
            gate_name = value.strip()
        elif isinstance(value, Mapping):
            gate_name = _gate_name(value)
        else:
            gate_name = ""
        if gate_name and gate_name in names:
            continue
        blocking_gates.append(value)
    policy["blocking_gates"] = blocking_gates
    return policy


def _artifact_urls(result: Mapping[str, Any], *keys: str, artifact_type: str) -> list[str]:
    urls: list[str] = []
    for key in keys:
        for value in _list(result.get(key)):
            if isinstance(value, str) and value.strip():
                urls.append(value.strip())
            elif isinstance(value, Mapping):
                url = _string(value.get("url") or value.get("path"))
                if url:
                    urls.append(url)
    for artifact in _list(result.get("artifacts")):
        if not isinstance(artifact, Mapping):
            continue
        if _string(artifact.get("type")).lower() != artifact_type:
            continue
        url = _string(artifact.get("url") or artifact.get("path"))
        if url:
            urls.append(url)
    return urls


def _preview_url(result: Mapping[str, Any]) -> str:
    qa = _mapping(result.get("qa_evidence"))
    return _string(result.get("preview_url") or qa.get("preview_url") or qa.get("previewUrl"))


def _changed_files(result: Mapping[str, Any]) -> list[str]:
    files = result.get("changed_files")
    if not isinstance(files, list):
        files = result.get("changedFiles")
    return [value for value in files or [] if isinstance(value, str) and value.strip()]


def _browser_relevant(result: Mapping[str, Any]) -> bool:
    if result.get("browser_relevant") is True or result.get("browserRelevant") is True:
        return True
    return any(file.lower().endswith(_BROWSER_FILE_SUFFIXES) for file in _changed_files(result))


def _gate_rollup(gates: list[dict[str, Any]]) -> dict[str, int | str]:
    passed = sum(1 for gate in gates if _gate_passed(gate))
    failed = sum(1 for gate in gates if _gate_failed(gate))
    pending = max(len(gates) - passed - failed, 0)
    status = "failed" if failed else "passed" if gates and pending == 0 else "pending"
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "total": len(gates),
    }


def verify_worker_result_evidence(
    result: Mapping[str, Any],
    *,
    gate_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    gate_policy = gate_policy or {}
    executed_gates = [
        dict(gate) for gate in _list(result.get("executed_gates")) if isinstance(gate, Mapping)
    ]
    blocking_gate_names = _gate_names(gate_policy.get("blocking_gates"))
    advisory_gate_names = _gate_names(gate_policy.get("advisory_gates"))
    gates_by_name = {_gate_name(gate): gate for gate in executed_gates}
    blockers: list[dict[str, str]] = []

    required_fields = (
        "cause",
        "changed_files",
        "before_proof",
        "after_proof",
        "executed_gates",
        "pull_request_summary",
    )
    for field in required_fields:
        if not _non_empty(result.get(field)):
            blockers.append(
                {
                    "code": f"worker_result_missing_{field}",
                    "message": f"Worker result is missing {field}.",
                }
            )

    if not executed_gates:
        blockers.append(
            {
                "code": "qa_evidence_missing_gate_status",
                "message": "QA Evidence is missing executed gate status.",
            }
        )

    for gate_name in sorted(blocking_gate_names):
        gate = gates_by_name.get(gate_name)
        if gate is None:
            blockers.append(
                {
                    "code": f"blocking_gate_missing:{gate_name}",
                    "message": f"Blocking gate {gate_name} did not run.",
                }
            )
            continue
        if not _platform_verified(gate):
            blockers.append(
                {
                    "code": f"blocking_gate_unverified:{gate_name}",
                    "message": f"Blocking gate {gate_name} is not platform verified.",
                }
            )
        if not _gate_passed(gate):
            blockers.append(
                {
                    "code": f"blocking_gate_failed:{gate_name}",
                    "message": f"Blocking gate {gate_name} failed.",
                }
            )

    screenshots = _artifact_urls(result, "screenshots", artifact_type="screenshot")
    videos = _artifact_urls(result, "videos", artifact_type="video")
    traces = _artifact_urls(result, "traces", artifact_type="trace")
    preview_url = _preview_url(result)
    browser_relevant = _browser_relevant(result)
    if browser_relevant:
        if not preview_url:
            blockers.append(
                {
                    "code": "qa_evidence_missing_preview_url",
                    "message": "Browser-relevant work is missing a preview URL.",
                }
            )
        if not screenshots:
            blockers.append(
                {
                    "code": "qa_evidence_missing_screenshot",
                    "message": "Browser-relevant work is missing a screenshot.",
                }
            )
        if not videos and not traces:
            blockers.append(
                {
                    "code": "qa_evidence_missing_video_or_trace",
                    "message": "Browser-relevant work is missing a video or trace.",
                }
            )

    for gate in executed_gates:
        gate_name = _gate_name(gate)
        if gate_name in advisory_gate_names:
            continue
        if _gate_failed(gate) and gate_name not in blocking_gate_names:
            blockers.append(
                {
                    "code": f"gate_failed:{gate_name}",
                    "message": f"Gate {gate_name} failed.",
                }
            )

    qa_evidence = {
        "complete": not blockers,
        "preview_url": preview_url,
        "screenshots": screenshots,
        "videos": videos,
        "traces": traces,
        "browser_relevant": browser_relevant,
        "gates": executed_gates,
        "gate_rollup": _gate_rollup(executed_gates),
        "blockers": blockers,
    }
    return {
        "ready_for_review": not blockers,
        "blockers": blockers,
        "qa_evidence": qa_evidence,
    }


def _normalise_worker_result(
    result: Mapping[str, Any], qa_evidence: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "cause": result.get("cause"),
        "changed_files": _changed_files(result),
        "before_proof": result.get("before_proof"),
        "after_proof": result.get("after_proof"),
        "executed_gates": _list(result.get("executed_gates")),
        "blockers": _list(result.get("blockers")),
        "risks": _list(result.get("risks")),
        "pull_request_summary": result.get("pull_request_summary"),
        "pr": _mapping(result.get("pr")),
        "qa_evidence": dict(qa_evidence),
        "pull_request_evidence": _mapping(result.get("pull_request_evidence")),
        "platform_evidence": _mapping(result.get("platform_evidence")),
    }


def _pr_details(result: Mapping[str, Any]) -> dict[str, Any]:
    pr = _mapping(result.get("pr"))
    return pr or _mapping(result.get("pull_request"))


def _markdown_link(value: str) -> str:
    return f"- {value}"


def _render_pull_request_evidence(
    result: Mapping[str, Any],
    qa_evidence: Mapping[str, Any],
) -> str:
    lines = ["## QA Evidence"]
    preview_url = _string(qa_evidence.get("preview_url"))
    if preview_url:
        lines.extend(["", f"- Preview: {preview_url}"])
    screenshots = [value for value in _list(qa_evidence.get("screenshots")) if _string(value)]
    videos = [value for value in _list(qa_evidence.get("videos")) if _string(value)]
    traces = [value for value in _list(qa_evidence.get("traces")) if _string(value)]
    if screenshots:
        lines.extend(["", "### Screenshots", *[_markdown_link(value) for value in screenshots]])
    if videos:
        lines.extend(["", "### Videos", *[_markdown_link(value) for value in videos]])
    if traces:
        lines.extend(["", "### Traces", *[_markdown_link(value) for value in traces]])
    gates = [gate for gate in _list(qa_evidence.get("gates")) if isinstance(gate, Mapping)]
    if gates:
        lines.append("")
        lines.append("### Gates")
        for gate in gates:
            lines.append(f"- {_gate_name(gate)}: {_gate_status(gate) or 'unknown'}")
    blockers = [
        blocker for blocker in _list(qa_evidence.get("blockers")) if isinstance(blocker, Mapping)
    ]
    if blockers:
        lines.append("")
        lines.append("### Blockers")
        for blocker in blockers:
            code = _string(blocker.get("code")) or "unknown"
            message = _string(blocker.get("message"))
            lines.append(f"- {code}: {message}" if message else f"- {code}")
    summary = _string(result.get("pull_request_summary"))
    if summary:
        lines.extend(["", "### Summary", summary])
    return "\n".join(lines).strip()


def _pull_request_evidence_gate(
    result: Mapping[str, Any],
    qa_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    pr = _pr_details(result)
    body = _render_pull_request_evidence(result, qa_evidence)
    missing: list[str] = []
    if not pr or not (_string(pr.get("url")) or isinstance(pr.get("number"), int)):
        missing.append("pull request")
    if qa_evidence.get("complete") is not True:
        missing.append("complete QA evidence")
    if not _string(qa_evidence.get("preview_url")) and qa_evidence.get("browser_relevant") is True:
        missing.append("preview URL")
    if not _list(qa_evidence.get("screenshots")) and qa_evidence.get("browser_relevant") is True:
        missing.append("screenshot")
    if (
        not _list(qa_evidence.get("videos"))
        and not _list(qa_evidence.get("traces"))
        and qa_evidence.get("browser_relevant") is True
    ):
        missing.append("video or trace")
    status = "failed" if missing else "passed"
    output = (
        "PR QA evidence is ready."
        if status == "passed"
        else "Missing PR QA evidence: " + ", ".join(missing) + "."
    )
    return {
        "name": "pr_qa_evidence",
        "status": status,
        "source": "platform",
        "platform_verified": True,
        "output": output,
        "body": body,
    }


def _with_pull_request_evidence(
    result: Mapping[str, Any],
    qa_evidence: Mapping[str, Any],
    gate_policy: Mapping[str, Any],
) -> dict[str, Any]:
    if "pr_qa_evidence" not in _policy_gate_names(gate_policy):
        return dict(result)
    gates = [
        dict(gate) for gate in _list(result.get("executed_gates")) if isinstance(gate, Mapping)
    ]
    if not any(_gate_name(gate) == "pr_qa_evidence" for gate in gates):
        gates.append(_pull_request_evidence_gate(result, qa_evidence))
    evidence = {
        "body": _render_pull_request_evidence(result, qa_evidence),
        "preview_url": qa_evidence.get("preview_url"),
        "screenshots": _list(qa_evidence.get("screenshots")),
        "videos": _list(qa_evidence.get("videos")),
        "traces": _list(qa_evidence.get("traces")),
        "gates": _list(qa_evidence.get("gates")),
        "blockers": _list(qa_evidence.get("blockers")),
    }
    return {
        **dict(result),
        "executed_gates": gates,
        "pull_request_evidence": evidence,
    }


def _count_artifacts(result: Mapping[str, Any], evidence: Mapping[str, Any]) -> int:
    return (
        len(_list(result.get("artifacts")))
        + len(_list(evidence.get("screenshots")))
        + len(_list(evidence.get("videos")))
        + len(_list(evidence.get("traces")))
    )


def _required_checks(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = result.get("required_checks")
    if not isinstance(checks, list):
        checks = result.get("requiredChecks")
    return [dict(check) for check in checks or [] if isinstance(check, Mapping)]


def _updated_runs(
    item: Mapping[str, Any],
    *,
    latest_run: Mapping[str, Any],
    worker_result: Mapping[str, Any],
    qa_evidence: Mapping[str, Any],
    queue_status: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated_latest = {
        **dict(latest_run),
        "status": queue_status,
        "worker_result": dict(worker_result),
        "qa_evidence": dict(qa_evidence),
        "gate_rollup": dict(qa_evidence.get("gate_rollup") or {}),
    }
    runs: list[dict[str, Any]] = []
    replaced = False
    latest_run_id = updated_latest.get("run_id")
    for run in _list(item.get("runs")):
        if not isinstance(run, Mapping):
            continue
        if latest_run_id and run.get("run_id") == latest_run_id:
            runs.append(updated_latest)
            replaced = True
        else:
            runs.append(dict(run))
    if not replaced:
        runs.append(updated_latest)
    return updated_latest, runs


async def ingest_delivery_worker_result(
    item_id: str,
    result: Mapping[str, Any],
    *,
    gate_policy: Mapping[str, Any] | None = None,
    platform_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    item = await read_delivery_queue_item(item_id)
    if item is None:
        raise KeyError(f"delivery queue item not found: {item_id}")

    policy = gate_policy or _mapping(item.get("gate_policy"))
    verified_result = await collect_drupal_runtime_evidence(item, result, runner=platform_runner)
    if "pr_qa_evidence" in _policy_gate_names(policy):
        provisional_policy = _without_blocking_gates(policy, {"pr_qa_evidence"})
        provisional = verify_worker_result_evidence(verified_result, gate_policy=provisional_policy)
        verified_result = _with_pull_request_evidence(
            verified_result,
            provisional["qa_evidence"],
            policy,
        )
    verification = verify_worker_result_evidence(verified_result, gate_policy=policy)
    qa_evidence = verification["qa_evidence"]
    worker_result = _normalise_worker_result(verified_result, qa_evidence)
    ready = verification["ready_for_review"]
    queue_status = "review" if ready else "blocked"
    blocker_reason = (
        "worker result accepted"
        if ready
        else verification["blockers"][0]["code"]
        if verification["blockers"]
        else "worker_result_blocked"
    )
    latest_run, runs = _updated_runs(
        item,
        latest_run=_mapping(item.get("latest_run")),
        worker_result=worker_result,
        qa_evidence=qa_evidence,
        queue_status=queue_status,
    )
    pr = _mapping(verified_result.get("pr"))
    required_checks = _required_checks(verified_result)
    extra = {
        "worker_result": worker_result,
        "qa_evidence": qa_evidence,
        "blockers": verification["blockers"],
        "blocker_reason": None if ready else blocker_reason,
        "gate_rollup": qa_evidence["gate_rollup"],
        "preview_count": 1 if qa_evidence["preview_url"] else 0,
        "artifact_count": _count_artifacts(verified_result, qa_evidence),
        "latest_run": latest_run,
        "runs": runs,
        "delivery": {
            **_mapping(item.get("delivery")),
            "queue_status": queue_status,
            "blocker_reason": None if ready else blocker_reason,
            "preview_count": 1 if qa_evidence["preview_url"] else 0,
            "artifact_count": _count_artifacts(verified_result, qa_evidence),
            "gate_rollup": qa_evidence["gate_rollup"],
        },
    }
    if required_checks:
        extra["required_checks"] = required_checks
    if pr:
        extra["pr"] = pr
        if isinstance(pr.get("number"), int):
            extra["pr_number"] = pr["number"]
        if _string(pr.get("url")):
            extra["pr_url"] = _string(pr["url"])
    return await transition_delivery_queue_status(
        item_id,
        queue_status,
        reason=blocker_reason,
        extra=extra,
    )
