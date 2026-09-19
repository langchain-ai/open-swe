"""Read app-managed policies and live GitHub evidence for shadow approval."""

import asyncio
import logging
from datetime import datetime
from typing import Literal

import httpx2
from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter

from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.thread_token import GitHubAuthError
from agent.review.approval import (
    ApprovalEvaluation,
    ApprovalEvidence,
    ApprovalFacts,
    Gate,
    PolicySnapshot,
    evaluate_policy,
    unavailable_evaluation,
)
from agent.review.approval_settings import load_policy_snapshot

logger = logging.getLogger(__name__)
_OBJECTS = TypeAdapter(list[dict[str, object]])
_TIMESTAMP = TypeAdapter(AwareDatetime)


class Revision(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class PullSnapshot(BaseModel):
    head: Revision
    base: Revision
    state: Literal["open", "closed"]
    draft: bool
    changed_files: int = Field(ge=0)


async def _get(
    client: httpx2.AsyncClient, path: str, params: dict[str, str | int] | None = None
) -> object:
    response = await github_request(client, "GET", f"{GITHUB_API_BASE}/{path}", params=params)
    if response.status_code == 401:
        raise GitHubAuthError("GitHub returned 401 while reading approval evidence")
    response.raise_for_status()
    return response.json()


async def _pages(
    client: httpx2.AsyncClient,
    path: str,
    key: str | None = None,
    params: dict[str, str | int] | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for page in range(1, 32):
        payload = await _get(client, path, {**(params or {}), "per_page": 100, "page": page})
        batch = _OBJECTS.validate_python(
            payload.get(key) if key and isinstance(payload, dict) else payload
        )
        records.extend(batch)
        if len(batch) < 100:
            return records
    raise ValueError("Approval evidence pagination was incomplete")


async def fetch_approval_policy(
    owner: str, repo: str, pr_number: int, token: str
) -> PolicySnapshot:
    root = f"repos/{owner}/{repo}"
    async with github_client(token=token) as client:
        pull = PullSnapshot.model_validate(await _get(client, f"{root}/pulls/{pr_number}"))
        return await load_policy_snapshot(
            f"{owner}/{repo}", base_sha=pull.base.sha, head_sha=pull.head.sha
        )


async def _checks(
    client: httpx2.AsyncClient, root: str, head_sha: str, required: list[str], own_id: int | None
) -> Gate:
    try:
        runs, statuses = await asyncio.gather(
            _pages(
                client, f"{root}/commits/{head_sha}/check-runs", "check_runs", {"filter": "latest"}
            ),
            _pages(client, f"{root}/commits/{head_sha}/status", "statuses"),
        )
        results: list[tuple[str, str]] = []
        for run in runs:
            if own_id is not None and run.get("id") == own_id:
                continue
            name = run.get("name")
            if not isinstance(name, str) or run.get("head_sha") != head_sha:
                raise ValueError("Check result lacks a name or matches another commit")
            conclusion = run.get("conclusion") if run.get("status") == "completed" else "pending"
            results.append((name, conclusion if isinstance(conclusion, str) else "unknown"))
        seen: set[str] = set()
        for status in statuses:
            name, state = status.get("context"), status.get("state")
            if not isinstance(name, str) or not isinstance(state, str):
                raise ValueError("Commit status is incomplete")
            if name not in seen:
                results.append((name, state))
                seen.add(name)
        missing = sorted(set(required) - {name for name, _ in results})
        failed = any(
            state in {"failure", "error", "timed_out", "action_required", "startup_failure"}
            for _, state in results
        )
        complete = bool(results) and not missing and all(state == "success" for _, state in results)
        return Gate(
            id="ci",
            title="Checks",
            status="fail" if failed else "pass" if complete else "unknown",
            evidence="; ".join(f"{name}: {state}" for name, state in results[:100])
            + ("; Missing required checks: " + ", ".join(missing) if missing else "")
            if results or missing
            else "No external check results are available.",
        )
    except httpx2.HTTPError, ValueError:
        logger.warning(
            "Approval check evidence unavailable", extra={"approval_repo": root}, exc_info=True
        )
        return Gate(
            id="ci",
            title="Checks",
            status="unknown",
            evidence="Could not read complete check results.",
        )


async def _reviews(client: httpx2.AsyncClient, root: str, number: int) -> Gate:
    try:
        reviews = await _pages(client, f"{root}/pulls/{number}/reviews")
        latest: dict[str, tuple[datetime, int, str]] = {}
        for review in reviews:
            state, user, review_id = review.get("state"), review.get("user"), review.get("id")
            if state in {"COMMENTED", "PENDING"}:
                continue
            login = user.get("login") if isinstance(user, dict) else None
            if (
                state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
                or not isinstance(login, str)
                or not isinstance(review_id, int)
            ):
                raise ValueError("Review state is incomplete")
            submitted = _TIMESTAMP.validate_python(review.get("submitted_at"))
            previous = latest.get(login.lower())
            if previous is None or (submitted, review_id) > previous[:2]:
                latest[login.lower()] = (submitted, review_id, str(state))
        requested = [
            login for login, (_, _, state) in latest.items() if state == "CHANGES_REQUESTED"
        ]
        return Gate(
            id="change_requests",
            title="Review state",
            status="fail" if requested else "pass",
            evidence="Changes requested by " + ", ".join(requested)
            if requested
            else "No outstanding change requests.",
        )
    except httpx2.HTTPError, ValueError:
        logger.warning(
            "Approval review evidence unavailable",
            extra={"approval_repo": root, "pr_number": number},
            exc_info=True,
        )
        return Gate(
            id="change_requests",
            title="Review state",
            status="unknown",
            evidence="Could not read complete review state.",
        )


async def _paths(
    client: httpx2.AsyncClient, root: str, number: int, expected: int
) -> list[str] | None:
    try:
        files = await _pages(client, f"{root}/pulls/{number}/files")
        if len(files) != expected:
            raise ValueError("Changed-file list is incomplete")
        paths: list[str] = []
        for file in files:
            path = file.get("filename")
            previous = file.get("previous_filename")
            if not isinstance(path, str) or (
                previous is not None and not isinstance(previous, str)
            ):
                raise ValueError("Changed-file path is unavailable")
            paths.append(path)
            if isinstance(previous, str):
                paths.append(previous)
        return paths
    except httpx2.HTTPError, ValueError:
        logger.warning(
            "Approval path evidence unavailable",
            extra={"approval_repo": root, "pr_number": number},
            exc_info=True,
        )
        return None


async def evaluate_pr_approval(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    head_sha: str,
    risk_score: int | None,
    confidence: Literal["low", "medium", "high"],
    limitations: list[str],
    open_findings: int,
    evidence: ApprovalEvidence | None,
    review_check_run_id: int | None,
) -> ApprovalEvaluation:
    root = f"repos/{owner}/{repo}"
    async with github_client(token=token) as client:
        try:
            pull = PullSnapshot.model_validate(await _get(client, f"{root}/pulls/{pr_number}"))
            policy = await load_policy_snapshot(
                f"{owner}/{repo}", base_sha=pull.base.sha, head_sha=pull.head.sha
            )
        except GitHubAuthError:
            raise
        except Exception:
            logger.warning(
                "Approval policy unavailable",
                extra={"approval_repo": root, "pr_number": pr_number},
                exc_info=True,
            )
            return unavailable_evaluation(
                "The approval settings could not be read or are invalid. No default was substituted."
            )
        ci, reviews, paths = await asyncio.gather(
            _checks(client, root, head_sha, policy.rules.required_checks, review_check_run_id),
            _reviews(client, root, pr_number),
            _paths(client, root, pr_number, pull.changed_files),
        )
        # PR-number endpoints are mutable; detect a push/base update during collection.
        try:
            current = PullSnapshot.model_validate(await _get(client, f"{root}/pulls/{pr_number}"))
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Approval revision recheck unavailable",
                extra={"approval_repo": root, "pr_number": pr_number},
                exc_info=True,
            )
            current = None
        try:
            latest_policy = await load_policy_snapshot(
                f"{owner}/{repo}", base_sha=pull.base.sha, head_sha=pull.head.sha
            )
        except Exception:
            logger.warning(
                "Approval policy recheck unavailable",
                extra={"approval_repo": root, "pr_number": pr_number},
                exc_info=True,
            )
            latest_policy = None
        if latest_policy is None or latest_policy.version != policy.version:
            return ApprovalEvaluation(
                policy=policy,
                decision="insufficient_evidence",
                criteria=[
                    Gate(
                        id="policy_current",
                        title="Current approval policy",
                        status="unknown",
                        evidence="Approval settings changed or could not be rechecked. Read the policy and review again.",
                    )
                ],
            )
        facts = ApprovalFacts(
            current_head_sha=current.head.sha if current else None,
            current_base_sha=current.base.sha if current else None,
            ready=current.state == "open" and not current.draft if current else None,
            changed_paths=paths,
            ci=ci,
            change_requests=reviews,
        )
    return evaluate_policy(
        policy=policy,
        evidence=evidence,
        facts=facts,
        head_sha=head_sha,
        risk_score=risk_score,
        confidence=confidence,
        limitations=limitations,
        open_findings=open_findings,
    )
