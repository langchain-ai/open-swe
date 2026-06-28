from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

MergeStatus = Literal["merged", "blocked", "error"]

_PASSING_CHECK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
_TERMINAL_FINDING_STATUSES = frozenset({"resolved", "dismissed"})
_BLOCKING_FINDING_SEVERITIES = frozenset({"high", "critical"})


@dataclass(frozen=True)
class MergeDecision:
    allowed: bool
    reason: str
    blockers: tuple[str, ...] = ()
    head_sha: str = ""
    merge_method: str = "squash"
    pr_number: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MergeResult:
    success: bool
    status: MergeStatus
    reason: str
    sha: str | None = None
    http_status: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


MergeFunc = Callable[..., Awaitable[MergeResult]]


def evaluate_auto_merge(
    *,
    pr: Mapping[str, Any],
    expected_head_sha: str,
    implementation_thread_id: str | None,
    reviewer_thread_id: str | None,
    reviewed_sha: str | None,
    qa_evidence: Mapping[str, Any] | None,
    findings: Sequence[Mapping[str, Any]] = (),
    required_checks: Sequence[Mapping[str, Any]] = (),
    required_check_names: Sequence[str] | None = None,
    qa_checks: Sequence[Mapping[str, Any] | bool] = (),
    blocking_gates: Sequence[Mapping[str, Any] | bool] = (),
    merge_policy_enabled: bool,
    credential_available: bool,
    kill_switch: bool,
    merge_method: str = "squash",
) -> MergeDecision:
    """Return a fail-closed, deterministic auto-merge decision for one PR snapshot."""
    blockers: list[str] = []
    head_sha = _pr_head_sha(pr)
    pr_number = _pr_number(pr)

    if _lower(pr.get("state")) != "open" or pr.get("merged") is True:
        blockers.append("pr_not_open")
    if bool(pr.get("draft")):
        blockers.append("pr_is_draft")
    if not expected_head_sha or not head_sha or expected_head_sha != head_sha:
        blockers.append("head_sha_mismatch")
    if (
        not implementation_thread_id
        or not reviewer_thread_id
        or implementation_thread_id == reviewer_thread_id
    ):
        blockers.append("review_threads_not_distinct")
    if not reviewed_sha or not head_sha or reviewed_sha != head_sha:
        blockers.append("reviewed_sha_mismatch")

    for index, gate in enumerate(qa_checks):
        if not _gate_passed(gate):
            blockers.append(f"qa_check_failed:{_gate_name(gate, index, 'qa-check')}")

    for index, gate in enumerate(blocking_gates):
        if not _gate_passed(gate):
            blockers.append(f"blocking_gate_failed:{_gate_name(gate, index, 'gate')}")

    if not _qa_evidence_complete(qa_evidence):
        blockers.append("qa_evidence_missing")

    for finding in findings:
        if _is_unresolved_blocking_finding(finding):
            blockers.append(f"unresolved_blocking_finding:{_finding_label(finding)}")

    blockers.extend(_required_check_blockers(required_checks, required_check_names))

    if not merge_policy_enabled:
        blockers.append("merge_policy_disabled")
    if not credential_available:
        blockers.append("merge_credential_missing")
    if kill_switch:
        blockers.append("merge_kill_switch_enabled")

    allowed = not blockers
    return MergeDecision(
        allowed=allowed,
        reason="ready" if allowed else blockers[0],
        blockers=tuple(blockers),
        head_sha=head_sha,
        merge_method=merge_method,
        pr_number=pr_number,
    )


async def merge_pr(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
    decision: MergeDecision,
    merge_func: MergeFunc | None = None,
) -> MergeResult:
    if not decision.allowed:
        return MergeResult(
            success=False,
            status="blocked",
            reason=decision.reason,
            sha=decision.head_sha,
            details={"blockers": list(decision.blockers)},
        )
    if not token:
        return MergeResult(
            success=False,
            status="blocked",
            reason="merge_credential_missing",
            sha=decision.head_sha,
        )
    if merge_func is None:
        from .utils.github_merge import merge_pull_request

        merge_func = merge_pull_request
    return await merge_func(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
        sha=decision.head_sha,
        merge_method=decision.merge_method,
    )


def _pr_head_sha(pr: Mapping[str, Any]) -> str:
    head = pr.get("head")
    if isinstance(head, Mapping):
        sha = head.get("sha")
        if isinstance(sha, str) and sha:
            return sha
    for key in ("head_sha", "sha"):
        value = pr.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _pr_number(pr: Mapping[str, Any]) -> int | None:
    number = pr.get("number")
    return number if isinstance(number, int) else None


def _lower(value: Any) -> str:
    return value.lower() if isinstance(value, str) else ""


def _qa_evidence_complete(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("complete") is True:
        return True
    return _lower(value.get("status")) in {"complete", "completed", "passed", "success"}


def _gate_passed(gate: Mapping[str, Any] | bool) -> bool:
    if isinstance(gate, bool):
        return gate
    if not isinstance(gate, Mapping):
        return False
    if "passed" in gate:
        return gate.get("passed") is True
    if "ok" in gate:
        return gate.get("ok") is True
    return _lower(gate.get("status")) in {"pass", "passed", "success", "ok", "completed"}


def _gate_name(gate: Mapping[str, Any] | bool, index: int, prefix: str) -> str:
    if isinstance(gate, Mapping):
        for key in ("name", "id", "key"):
            value = gate.get(key)
            if isinstance(value, str) and value:
                return value
    return f"{prefix}-{index + 1}"


def _is_unresolved_blocking_finding(finding: Mapping[str, Any]) -> bool:
    if _lower(finding.get("status") or "open") in _TERMINAL_FINDING_STATUSES:
        return False
    surface = finding.get("surface")
    if isinstance(surface, Mapping) and _lower(surface.get("state")) == "resolved":
        return False
    if finding.get("blocking") is True:
        return True
    return _lower(finding.get("severity")) in _BLOCKING_FINDING_SEVERITIES


def _finding_label(finding: Mapping[str, Any]) -> str:
    for key in ("id", "title", "file"):
        value = finding.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _required_check_blockers(
    checks: Sequence[Mapping[str, Any]],
    required_names: Sequence[str] | None,
) -> list[str]:
    blockers: list[str] = []
    checks_by_name = {_check_name(check): check for check in checks if _check_name(check)}
    if required_names is not None:
        for name in required_names:
            check = checks_by_name.get(name)
            if check is None:
                blockers.append(f"required_check_missing:{name}")
            elif not _check_passing(check):
                blockers.append(f"required_check_not_passing:{name}")
        return blockers

    for index, check in enumerate(checks):
        if not _check_passing(check):
            name = _check_name(check) or f"required-check-{index + 1}"
            blockers.append(f"required_check_not_passing:{name}")
    return blockers


def _check_name(check: Mapping[str, Any]) -> str:
    for key in ("name", "context"):
        value = check.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _check_passing(check: Mapping[str, Any]) -> bool:
    state = _lower(check.get("state"))
    if state == "success":
        return True
    if state in {"failure", "error", "pending"}:
        return False

    status = _lower(check.get("status"))
    if status and status != "completed":
        return False

    conclusion = _lower(check.get("conclusion"))
    if status == "completed":
        return conclusion in _PASSING_CHECK_CONCLUSIONS
    if conclusion:
        return conclusion in _PASSING_CHECK_CONCLUSIONS
    return False
