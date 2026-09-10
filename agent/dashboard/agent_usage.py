"""PostgreSQL-backed Open SWE usage analytics."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from agent.analytics.emitter import (
    finding_transition,
    pr_opened,
    pr_state,
    review_published,
    run_cost,
    run_started,
    run_terminal,
)
from agent.analytics.queries import usage_leaderboard
from agent.config import ENV
from agent.utils.json_types import as_json_object
from agent.utils.run_usage import RunUsageSummary


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float) and not isinstance(value, bool):
        raw = float(value) / 1000 if value > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(raw, UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def _severity(value: object) -> Literal["low", "medium", "high", "critical"]:
    severity = str(value or "low").lower()
    if severity in {"low", "medium", "high", "critical"}:
        return severity
    return "low"


async def record_agent_run_usage(
    *,
    run_id: str,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
    github_user_id: str | int | None = None,
    repository: str | None = None,
) -> None:
    del effort
    from agent.analytics.directory import upsert_model, upsert_person

    await upsert_person(
        provider="github",
        immutable_person_key=github_user_id or github_login,
        github_login=github_login,
        email=user_email,
    )
    await upsert_model(model_id)
    await run_started(
        run_key=run_id,
        thread_key=thread_id,
        model=model_id,
        source=source,
        immutable_person_key=github_user_id or github_login,
        repository_key=repository,
    )


async def record_agent_run_completion(
    *, run_id: str, usage: RunUsageSummary | None, status: str = "success", thread_id: str = ""
) -> bool:
    await run_terminal(
        run_key=run_id,
        thread_key=thread_id or run_id,
        status=status,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
        producer_suffix="middleware",
    )
    return True


async def agent_run_needs_cost_refresh(*, run_id: str) -> bool:
    return bool(run_id and ENV.ANALYTICS_WORKSPACE_ID.optional())


agent_invocation_needs_cost_refresh = agent_run_needs_cost_refresh


async def mark_agent_cost_refresh_scheduled(*, run_id: str) -> None:
    del run_id


mark_agent_invocation_cost_refresh_scheduled = mark_agent_cost_refresh_scheduled


async def record_agent_run_cost(*, run_id: str, cost_usd: float) -> None:
    await run_cost(run_key=run_id, cost_usd=cost_usd)


async def record_agent_invocation_completion(
    *, invocation_id: str, usage: RunUsageSummary | None, status: str = "success"
) -> bool:
    return await record_agent_run_completion(
        run_id=invocation_id, usage=usage, status=status, thread_id=invocation_id
    )


async def record_agent_invocation_cost(*, invocation_id: str, cost_usd: float) -> None:
    await record_agent_run_cost(run_id=invocation_id, cost_usd=cost_usd)


async def record_agent_pr_usage(
    *,
    thread_id: str | None,
    github_login: str | None,
    user_email: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str | None,
    head: str,
    base: str,
    additions: int = 0,
    deletions: int = 0,
    changed_files: int = 0,
    state: str | None = None,
    merged: bool = False,
    created_at: object = None,
    merged_at: object = None,
    run_id: str | None = None,
    model_id: str | None = None,
    source: str | None = None,
    repository_private: bool | None = None,
) -> None:
    del github_login, user_email, pr_url, head, base, additions, deletions, changed_files
    opening_run = run_id or thread_id
    if not opening_run:
        return
    await pr_opened(
        owner=owner,
        repo=repo,
        number=pr_number,
        run_key=opening_run,
        model=model_id,
        source=source,
        repository_private=repository_private,
        occurred_at=_timestamp(created_at),
    )
    if merged or state == "closed":
        await pr_state(
            owner=owner,
            repo=repo,
            number=pr_number,
            action="closed",
            merged=merged,
            source_version=None,
            occurred_at=_timestamp(merged_at),
        )


async def update_agent_pr_usage_from_webhook(payload: dict[str, Any]) -> None:
    pr = payload.get("pull_request")
    repository = payload.get("repository")
    if not isinstance(pr, dict) or not isinstance(repository, dict):
        return
    owner_payload = repository.get("owner")
    owner = owner_payload.get("login") if isinstance(owner_payload, dict) else None
    repo = repository.get("name")
    number = pr.get("number") or payload.get("number")
    action = payload.get("action")
    if (
        not isinstance(owner, str)
        or not isinstance(repo, str)
        or not isinstance(number, int)
        or action not in {"closed", "reopened"}
    ):
        return
    updated_at = _timestamp(pr.get("updated_at") or pr.get("closed_at"))
    await pr_state(
        owner=owner,
        repo=repo,
        number=number,
        action=action,
        merged=bool(pr.get("merged")),
        source_version=int(updated_at.timestamp() * 1_000_000),
        occurred_at=updated_at,
    )


async def record_reviewer_publication(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings: Sequence[Mapping[str, Any]],
) -> None:
    await review_published(
        thread_key=thread_id,
        owner=owner,
        repo=repo,
        number=pr_number,
        head_sha=head_sha,
        finding_count=len(findings),
    )
    for finding in findings:
        if finding.get("surface_state") == "surfaced":
            await finding_transition(
                thread_key=thread_id,
                finding_key=str(finding.get("id") or ""),
                owner=owner,
                repo=repo,
                number=pr_number,
                state="surfaced",
                severity=_severity(finding.get("severity")),
                category=str(finding.get("category") or "unknown"),
                version=str(finding.get("last_confirmed_sha") or head_sha),
            )


async def record_reviewer_finding_state(thread_id: str, finding: Mapping[str, Any]) -> None:
    pr = as_json_object(finding.get("pr"))
    owner = str(pr.get("owner") or finding.get("owner") or "")
    repo = str(pr.get("name") or finding.get("repo") or "")
    number = pr.get("number") or finding.get("pr_number")
    if not owner or not repo or not isinstance(number, int):
        return
    status = str(finding.get("status") or "open")
    state = status if status in {"resolved", "dismissed"} else "reopened"
    await finding_transition(
        thread_key=thread_id,
        finding_key=str(finding.get("id") or ""),
        owner=owner,
        repo=repo,
        number=number,
        state=state,
        severity=_severity(finding.get("severity")),
        category=str(finding.get("category") or "unknown"),
        version=str(finding.get("last_confirmed_sha") or datetime.now(UTC).isoformat()),
    )


async def list_agent_usage_leaderboard(
    *,
    period: str | None,
    limit: int,
    current_login: str | None,
    current_email: str | None,
    admin: bool = False,
) -> dict[str, Any]:
    del current_email
    return await usage_leaderboard(
        period=period, limit=limit, current_login=current_login, admin=admin
    )
