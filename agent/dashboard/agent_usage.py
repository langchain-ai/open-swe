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

<<<<<<< HEAD
AGENT_INVOCATION_NAMESPACE = ["usage", "v2", "agent_runs"]
AGENT_PR_NAMESPACE = ["usage", "v2", "agent_prs"]
REVIEW_NAMESPACE = ["usage", "v2", "reviews"]
REVIEW_FINDING_NAMESPACE = ["usage", "v2", "review_findings"]
=======
>>>>>>> 9c9f4458b (Wire lifecycle events and bounded analytics APIs)

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


<<<<<<< HEAD
def _normalize_period(period: str | None) -> Period:
    if period == "7d" or period == "all":
        return period
    return "30d"


def _period_cutoff_ms(period: Period) -> int:
    days = 7 if period == "7d" else 30 if period == "30d" else 0
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000) if days else 0


def _store_key(*parts: object) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _record(item: Any) -> dict[str, Any] | None:
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _write_lock(namespace: list[str], key: str) -> asyncio.Lock:
    lock_key = (tuple(namespace), key, id(asyncio.get_running_loop()))
    lock = _WRITE_LOCKS.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _WRITE_LOCKS[lock_key] = lock
    return lock


async def _get(namespace: list[str], key: str) -> dict[str, Any] | None:
    try:
        return _record(await _client().store.get_item(namespace, key))
    except APIStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


async def _mutate(
    namespace: list[str], key: str, update: Callable[[dict[str, Any] | None], dict[str, Any]]
) -> None:
    async with _write_lock(namespace, key):
        # An empty result means the callback found nothing to update; writing it
        # would insert a record with no identity that every reader must skip.
        value = update(await _get(namespace, key))
        if not value:
            return
        await _client().store.put_item(namespace, key, value)


async def _all(namespace: list[str]) -> list[dict[str, Any]]:
    started_at = time.monotonic()
    values: list[dict[str, Any]] = []
    offset = 0
    pages = 0
    try:
        while True:
            result = await _client().store.search_items(namespace, limit=_PAGE_SIZE, offset=offset)
            items = (
                result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
            )
            page = list(items or [])
            pages += 1
            values.extend(value for item in page if (value := _record(item)) is not None)
            if len(page) < _PAGE_SIZE:
                logger.info(
                    "Usage telemetry scan completed",
                    extra={
                        "usage_namespace": "/".join(namespace),
                        "usage_pages": pages,
                        "usage_records": len(values),
                        "usage_elapsed_ms": round((time.monotonic() - started_at) * 1000),
                    },
                )
                return values
            offset += len(page)
    except Exception:
        logger.exception(
            "Usage telemetry scan failed",
            extra={
                "usage_namespace": "/".join(namespace),
                "usage_pages": pages,
                "usage_records": len(values),
                "usage_offset": offset,
                "usage_elapsed_ms": round((time.monotonic() - started_at) * 1000),
            },
        )
        raise


def _login(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _email(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _int(value: object, fallback: object = 0) -> int:
    return value if isinstance(value, int) else fallback if isinstance(fallback, int) else 0


async def _backfill_legacy_agent_records() -> None:
    legacy_threads, legacy_prs = await asyncio.gather(
        _all(LEGACY_THREAD_NAMESPACE), _all(LEGACY_PR_NAMESPACE)
    )
    for record in legacy_threads:
        thread_id = record.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        if record.get("source") not in _AGENT_SOURCES:
            continue
        key = _store_key("run", f"legacy:{thread_id}")
        if await _get(AGENT_INVOCATION_NAMESPACE, key):
            continue
        await _client().store.put_item(
            AGENT_INVOCATION_NAMESPACE,
            key,
            {
                "invocation_id": f"legacy:{thread_id}",
                "run_id": f"legacy:{thread_id}",
                "thread_id": thread_id,
                "github_login": _login(record.get("github_login")),
                "user_email": _email(record.get("user_email")),
                "model_id": record.get("model_id") or "",
                "effort": record.get("effort") or "",
                "source": record.get("source"),
                "created_at_ms": _timestamp_ms(record.get("created_at_ms"))
                or _timestamp_ms(record.get("updated_at_ms")),
            },
        )
    for record in legacy_prs:
        owner = record.get("owner")
        repo = record.get("repo")
        number = record.get("pr_number")
        if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
            continue
        key = _store_key("pr", owner.lower(), repo.lower(), number)
        if await _get(AGENT_PR_NAMESPACE, key):
            continue
        await _client().store.put_item(
            AGENT_PR_NAMESPACE,
            key,
            {
                **record,
                "github_login": _login(record.get("github_login")),
                "user_email": _email(record.get("user_email")),
                "created_at_ms": _timestamp_ms(record.get("created_at_ms"))
                or _timestamp_ms(record.get("updated_at_ms")),
                "merged_at_ms": 0,
            },
        )


async def _backfill_legacy_reviews() -> None:
    from agent.review.findings import REVIEWER_THREAD_KIND

    offset = 0
    while True:
        page = await _client().threads.search(
            metadata={"kind": REVIEWER_THREAD_KIND}, limit=_PAGE_SIZE, offset=offset
        )
        threads = list(page or [])
        for thread in threads:
            metadata = thread_metadata(thread)
            thread_id = thread.get("thread_id") if isinstance(thread, Mapping) else None
            if not isinstance(thread_id, str) or not thread_id:
                continue
            findings = [item for item in metadata.get("findings") or [] if isinstance(item, dict)]
            head_sha = metadata.get("last_reviewed_sha") or metadata.get("head_sha") or ""
            reviewed_at_ms = _timestamp_ms(
                metadata.get("created_at")
                or (thread.get("created_at") if isinstance(thread, Mapping) else None)
            )
            await _backfill_legacy_review(
                thread_id=thread_id,
                metadata=metadata,
                findings=findings,
                head_sha=str(head_sha),
                reviewed_at_ms=reviewed_at_ms,
            )
        if len(threads) < _PAGE_SIZE:
            return
        offset += len(threads)


async def _backfill_legacy_review(
    *,
    thread_id: str,
    metadata: dict[str, Any],
    findings: list[dict[str, Any]],
    head_sha: str,
    reviewed_at_ms: int,
) -> None:
    pr_meta = as_json_object(metadata.get("pr"))
    owner = str(pr_meta.get("owner") or "")
    repo = str(pr_meta.get("name") or "")
    pr_number = pr_meta.get("number")
    if not owner or not repo or not isinstance(pr_number, int):
        return
    review_key = _store_key("review", thread_id, head_sha)
    if not await _get(REVIEW_NAMESPACE, review_key):
        await _client().store.put_item(
            REVIEW_NAMESPACE,
            review_key,
            {
                "thread_id": thread_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "findings_recorded": len(findings),
                "published_at_ms": reviewed_at_ms,
            },
        )
    for finding in findings:
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        key = _store_key("finding", thread_id, finding_id)
        if await _get(REVIEW_FINDING_NAMESPACE, key):
            continue
        status = finding.get("status") or "open"
        surfaced = _finding_surfaced(finding)
        await _client().store.put_item(
            REVIEW_FINDING_NAMESPACE,
            key,
            {
                "thread_id": thread_id,
                "finding_id": finding_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "severity": finding.get("severity") or "",
                "category": finding.get("category") or "",
                "status": status,
                "first_seen_sha": finding.get("first_seen_sha") or "",
                "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
                "surfaced_at_ms": reviewed_at_ms if surfaced else 0,
                "human_replies": _human_reply_count(finding),
                "recorded_at_ms": reviewed_at_ms,
                "updated_at_ms": reviewed_at_ms,
                "resolved_at_ms": reviewed_at_ms if status == "resolved" else 0,
                "resolved_sha": finding.get("last_confirmed_sha") or ""
                if status == "resolved"
                else "",
            },
        )


async def _backfill_legacy_usage() -> None:
    """Migrate pre-v2 usage records into the event namespaces exactly once."""
    if await _get(BACKFILL_NAMESPACE, _BACKFILL_KEY):
        return
    async with _write_lock(BACKFILL_NAMESPACE, _BACKFILL_KEY):
        if await _get(BACKFILL_NAMESPACE, _BACKFILL_KEY):
            return
        try:
            await _backfill_legacy_agent_records()
            await _backfill_legacy_reviews()
        except Exception:  # noqa: BLE001
            logger.warning("Legacy usage backfill failed; retrying next read", exc_info=True)
            return
        await _client().store.put_item(
            BACKFILL_NAMESPACE, _BACKFILL_KEY, {"completed_at_ms": _now_ms()}
        )
=======
def _severity(value: object) -> Literal["low", "medium", "high", "critical"]:
    severity = str(value or "low").lower()
    if severity in {"low", "medium", "high", "critical"}:
        return severity
    return "low"
>>>>>>> 9c9f4458b (Wire lifecycle events and bounded analytics APIs)


async def record_agent_invocation_usage(
    *,
    invocation_id: str,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
    github_user_id: str | int | None = None,
    repository: str | None = None,
) -> None:
<<<<<<< HEAD
    """Record one actual agent invocation, idempotently."""
    if not invocation_id or not thread_id:
        return
    key = _store_key("run", invocation_id)
    now_ms = _now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        return existing or {
            "invocation_id": invocation_id,
            "run_id": invocation_id,
            "thread_id": thread_id,
            "github_login": _login(github_login),
            "user_email": _email(user_email),
            "model_id": model_id,
            "effort": effort or "",
            "source": source if source in _AGENT_SOURCES else "dashboard",
            "created_at_ms": now_ms,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": None,
            "cost_refresh_scheduled_at_ms": 0,
            "finished_at_ms": 0,
        }

    await _mutate(AGENT_INVOCATION_NAMESPACE, key, update)


async def record_agent_invocation_completion(
    *, invocation_id: str, usage: RunUsageSummary | None, status: str = "success"
) -> bool:
    """Complete an existing invocation record idempotently."""
    if not invocation_id:
        return False
    key = _store_key("run", invocation_id)
    async with _write_lock(AGENT_INVOCATION_NAMESPACE, key):
        existing = await _get(AGENT_INVOCATION_NAMESPACE, key)
        if not existing or existing.get("finished_at_ms"):
            return False
        value = {**existing, "invocation_id": invocation_id, "finished_at_ms": _now_ms()}
        if status != "success":
            value["status"] = status
        if usage is not None:
            counts = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            }
            value.update({field: count for field, count in counts.items() if count is not None})
        await _client().store.put_item(AGENT_INVOCATION_NAMESPACE, key, value)
    return True


async def agent_invocation_needs_cost_refresh(*, invocation_id: str) -> bool:
    """Return whether a completed invocation still needs cost enrichment scheduled."""
    if not invocation_id:
        return False
    existing = await _get(AGENT_INVOCATION_NAMESPACE, _store_key("run", invocation_id))
    return bool(
        existing
        and existing.get("finished_at_ms")
        and existing.get("cost_usd") is None
        and not existing.get("cost_refresh_scheduled_at_ms")
    )


async def mark_agent_invocation_cost_refresh_scheduled(*, invocation_id: str) -> None:
    """Persist that deferred invocation cost enrichment was scheduled."""
    if not invocation_id:
        return
    key = _store_key("run", invocation_id)

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        if not existing or existing.get("cost_refresh_scheduled_at_ms"):
            return existing or {}
        return {
            **existing,
            "invocation_id": invocation_id,
            "cost_refresh_scheduled_at_ms": _now_ms(),
        }

    await _mutate(AGENT_INVOCATION_NAMESPACE, key, update)


async def record_agent_invocation_cost(*, invocation_id: str, cost_usd: float) -> None:
    """Store the LangSmith cost for an existing invocation."""
    if not invocation_id or not math.isfinite(cost_usd) or cost_usd < 0:
        return
    key = _store_key("run", invocation_id)

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        return (
            {**existing, "invocation_id": invocation_id, "cost_usd": cost_usd} if existing else {}
        )

    await _mutate(AGENT_INVOCATION_NAMESPACE, key, update)
=======
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


async def mark_agent_cost_refresh_scheduled(*, run_id: str) -> None:
    del run_id


async def record_agent_run_cost(*, run_id: str, cost_usd: float) -> None:
    await run_cost(run_key=run_id, cost_usd=cost_usd)
>>>>>>> 9c9f4458b (Wire lifecycle events and bounded analytics APIs)


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
<<<<<<< HEAD
    key = _store_key("finding", thread_id, finding_id)
    now_ms = _now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        if not existing:
            return {}
        status = finding.get("status") or "open"
        value = {
            **existing,
            "status": status,
            "severity": finding.get("severity") or existing.get("severity", ""),
            "category": finding.get("category") or existing.get("category", ""),
            "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
            "human_replies": _human_reply_count(finding),
            "updated_at_ms": now_ms,
        }
        if _finding_surfaced(finding) and not value.get("surfaced_at_ms"):
            value["surfaced_at_ms"] = now_ms
        if status == "resolved" and not value.get("resolved_at_ms"):
            value["resolved_at_ms"] = now_ms
            value["resolved_sha"] = value["last_confirmed_sha"]
        return value

    async with _write_lock(REVIEW_FINDING_NAMESPACE, key):
        existing = await _get(REVIEW_FINDING_NAMESPACE, key)
        if existing:
            await _client().store.put_item(REVIEW_FINDING_NAMESPACE, key, update(existing))


def _aliases(records: list[dict[str, Any]]) -> dict[str, str]:
    return {
        email: login
        for record in records
        if (email := _email(record.get("user_email")))
        and (login := _login(record.get("github_login")))
    }


def _user_key(record: dict[str, Any], aliases: dict[str, str]) -> str | None:
    login = _login(record.get("github_login")) or aliases.get(_email(record.get("user_email")), "")
    if login:
        return f"github:{login}"
    email = _email(record.get("user_email"))
    return f"email:{email}" if email else None


def _new_user(key: str, record: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    login = _login(record.get("github_login")) or aliases.get(_email(record.get("user_email")), "")
    email = _email(record.get("user_email"))
    return {
        "key": key,
        "github_login": login,
        "email": email,
        "name": login or email.split("@", 1)[0],
        "invocations": 0,
        "prs_opened": 0,
        "merged_prs": 0,
        "agent_loc": 0,
        "additions": 0,
        "deletions": 0,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "invocation_duration_ms": 0,
        "finished_invocations": 0,
        "models": Counter(),
    }


def _limited_rows(
    rows: list[dict[str, Any]], current_row: dict[str, Any] | None, limit: int
) -> list[dict[str, Any]]:
    limited = rows[: min(max(limit, 1), 100)]
    if current_row and all(row["rank"] != current_row["rank"] for row in limited):
        return [*limited, current_row]
    return limited


def _in_period(record: dict[str, Any], field: str, cutoff_ms: int) -> bool:
    timestamp = _timestamp_ms(record.get(field))
    return timestamp > 0 and timestamp >= cutoff_ms
=======
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
>>>>>>> 9c9f4458b (Wire lifecycle events and bounded analytics APIs)


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
<<<<<<< HEAD
    cache_key = (normalized, _login(current_login) or _email(current_email))
    cached = _USAGE_CACHE.get(cache_key)
    if cached and _now_ms() - cached[0] < _USAGE_CACHE_TTL_MS:
        payload = dict(cached[1])
        payload["rows"] = _limited_rows(payload["rows"], cached[2], limit)
        logger.info(
            "Usage leaderboard cache hit",
            extra={
                "usage_period": normalized,
                "usage_rows": len(payload["rows"]),
                "usage_elapsed_ms": round((time.monotonic() - started_at) * 1000),
            },
        )
        return payload
    await _backfill_legacy_usage()
    cutoff_ms = _period_cutoff_ms(normalized)
    runs, prs, review_records, finding_records = await asyncio.gather(
        _all(AGENT_INVOCATION_NAMESPACE),
        _all(AGENT_PR_NAMESPACE),
        _all(REVIEW_NAMESPACE),
        _all(REVIEW_FINDING_NAMESPACE),
    )
    aliases = _aliases(runs + prs)
    users: dict[str, dict[str, Any]] = {}

    for record in runs:
        if not _in_period(record, "created_at_ms", cutoff_ms):
            continue
        key = _user_key(record, aliases)
        if not key:
            continue
        user = users.setdefault(key, _new_user(key, record, aliases))
        user["invocations"] += 1
        user["total_tokens"] += _int(record.get("total_tokens"))
        cost_usd = record.get("cost_usd")
        if (
            isinstance(cost_usd, int | float)
            and not isinstance(cost_usd, bool)
            and math.isfinite(cost_usd)
            and cost_usd >= 0
        ):
            user["total_cost_usd"] += float(cost_usd)
        created_at_ms = _timestamp_ms(record.get("created_at_ms"))
        finished_at_ms = _timestamp_ms(record.get("finished_at_ms"))
        if created_at_ms and finished_at_ms >= created_at_ms:
            user["invocation_duration_ms"] += finished_at_ms - created_at_ms
            user["finished_invocations"] += 1
        model = record.get("model_id")
        if isinstance(model, str) and model:
            user["models"][model] += 1

    for record in prs:
        if not _in_period(record, "created_at_ms", cutoff_ms):
            continue
        key = _user_key(record, aliases)
        if not key:
            continue
        user = users.setdefault(key, _new_user(key, record, aliases))
        additions = int(record.get("additions") or 0)
        deletions = int(record.get("deletions") or 0)
        user["prs_opened"] += 1
        user["merged_prs"] += int(bool(record.get("merged")))
        user["additions"] += additions
        user["deletions"] += deletions
        user["agent_loc"] += additions + deletions

    ordered = sorted(
        users.values(),
        key=lambda user: (
            -user["merged_prs"],
            -user["agent_loc"],
            -user["prs_opened"],
            -user["invocations"],
            user["name"],
        ),
    )
    current_keys = {
        f"github:{_login(current_login)}" if _login(current_login) else "",
        f"email:{_email(current_email)}" if _email(current_email) else "",
    }
    rows: list[dict[str, Any]] = []
    current_row: dict[str, Any] | None = None
    for rank, user in enumerate(ordered, 1):
        models: Counter[str] = user["models"]
        is_current = user["key"] in current_keys
        row = {
            "rank": rank,
            "user": {
                "name": user["name"] if is_current or user["github_login"] else "Open SWE user",
                "github_login": user["github_login"] or None,
                "email": (user["email"] or None) if is_current else None,
            },
            "favorite_model": models.most_common(1)[0][0] if models else "default",
            "avg_invocation_seconds": (
                user["invocation_duration_ms"] / user["finished_invocations"] / 1000
                if user["finished_invocations"]
                else 0.0
            ),
            "avg_run_seconds": (
                user["invocation_duration_ms"] / user["finished_invocations"] / 1000
                if user["finished_invocations"]
                else 0.0
            ),
            "agent_runs": user["invocations"],
            **{
                key: user[key]
                for key in (
                    "invocations",
                    "prs_opened",
                    "merged_prs",
                    "agent_loc",
                    "additions",
                    "deletions",
                    "total_tokens",
                    "total_cost_usd",
                )
            },
        }
        if is_current:
            current_row = row
        if len(rows) < 100:
            rows.append(row)

    reviews = [
        record for record in review_records if _in_period(record, "published_at_ms", cutoff_ms)
    ]
    findings = [
        record for record in finding_records if _in_period(record, "recorded_at_ms", cutoff_ms)
    ]
    surfaced = [
        record for record in finding_records if _in_period(record, "surfaced_at_ms", cutoff_ms)
    ]
    reviewed_prs = {(r.get("owner"), r.get("repo"), r.get("pr_number")) for r in reviews}
    prs_with_findings = {
        (r.get("owner"), r.get("repo"), r.get("pr_number"))
        for r in reviews
        if int(r.get("findings_recorded") or 0) > 0
    }
    addressed = [record for record in surfaced if record.get("status") == "resolved"]
    dismissed = [record for record in surfaced if record.get("status") == "dismissed"]
    unresolved = [record for record in surfaced if record.get("status") == "open"]
    severity = Counter(str(record.get("severity")) for record in findings if record.get("severity"))
    categories = Counter(
        str(record.get("category")) for record in findings if record.get("category")
    )
    now_ms = _now_ms()
    reviewer_stats = {
        "period": normalized,
        "reviewed_prs": len(reviewed_prs),
        "prs_with_findings": len(prs_with_findings),
        "findings_recorded": len(findings),
        "surfaced_findings": len(surfaced),
        "addressed_findings": len(addressed),
        "resolved_after_update": sum(
            1
            for record in addressed
            if record.get("resolved_sha")
            and record.get("resolved_sha") != record.get("first_seen_sha")
        ),
        "dismissed_findings": len(dismissed),
        "unresolved_surfaced_findings": len(unresolved),
        "resolution_rate": len(addressed) / len(surfaced) if surfaced else 0.0,
        "human_replies": sum(int(record.get("human_replies") or 0) for record in surfaced),
        "severity_counts": dict(severity),
        "top_categories": [
            {"name": name, "count": count} for name, count in categories.most_common(5)
        ],
        "generated_at_ms": now_ms,
    }
    payload = {
        "period": normalized,
        "rows": rows,
        "total_members": len(ordered),
        "current_user_rank": current_row["rank"] if current_row else None,
        "generated_at_ms": now_ms,
        "reviewer_stats": reviewer_stats,
    }
    _USAGE_CACHE[cache_key] = (now_ms, payload, current_row)
    result = dict(payload)
    result["rows"] = _limited_rows(rows, current_row, limit)
    logger.info(
        "Usage leaderboard aggregation completed",
        extra={
            "usage_period": normalized,
            "usage_invocations": len(runs),
            "usage_prs": len(prs),
            "usage_reviews": len(review_records),
            "usage_findings": len(finding_records),
            "usage_members": len(ordered),
            "usage_rows": len(result["rows"]),
            "usage_elapsed_ms": round((time.monotonic() - started_at) * 1000),
        },
    )
    return result
=======
>>>>>>> 9c9f4458b (Wire lifecycle events and bounded analytics APIs)
