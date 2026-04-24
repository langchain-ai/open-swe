"""Read-only LangSmith trace tools scoped to a single project.

All tools here filter every query by `get_scoped_langsmith_project_id()` so
the agent can only see traces from its own tracing project, even though the
underlying API key is workspace-wide. This mirrors how smith-issues-agent
scopes every CLI call to the per-run `session_id` from its configurable —
scope is enforced in code, not trusted from tool arguments.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..utils.langsmith import (
    get_scoped_langsmith_client,
    get_scoped_langsmith_project_id,
)

logger = logging.getLogger(__name__)

_MAX_LIMIT = 100
_STATS_MAX_LIMIT = 1000
_RUN_SELECT_FIELDS = [
    "id",
    "name",
    "run_type",
    "status",
    "start_time",
    "end_time",
    "error",
    "session_id",
    "trace_id",
    "parent_run_id",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_cost",
    "tags",
]


def _parse_iso(value: str | None, *, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp: {exc}") from exc


def _run_summary(run: Any) -> dict[str, Any]:
    """Serialize a Run to a compact, JSON-safe dict for tool output."""
    latency_s: float | None = None
    if getattr(run, "start_time", None) and getattr(run, "end_time", None):
        latency_s = (run.end_time - run.start_time).total_seconds()
    return {
        "id": str(run.id),
        "name": getattr(run, "name", None),
        "run_type": getattr(run, "run_type", None),
        "status": getattr(run, "status", None),
        "start_time": run.start_time.isoformat() if getattr(run, "start_time", None) else None,
        "end_time": run.end_time.isoformat() if getattr(run, "end_time", None) else None,
        "latency_seconds": latency_s,
        "error": getattr(run, "error", None),
        "trace_id": str(run.trace_id) if getattr(run, "trace_id", None) else None,
        "parent_run_id": str(run.parent_run_id) if getattr(run, "parent_run_id", None) else None,
        "total_tokens": getattr(run, "total_tokens", None),
        "prompt_tokens": getattr(run, "prompt_tokens", None),
        "completion_tokens": getattr(run, "completion_tokens", None),
        "total_cost": float(run.total_cost)
        if getattr(run, "total_cost", None) is not None
        else None,
        "tags": list(getattr(run, "tags", None) or []),
    }


def langsmith_list_runs(
    start_time_iso: str | None = None,
    end_time_iso: str | None = None,
    run_type: str | None = None,
    is_root: bool | None = True,
    error: bool | None = None,
    limit: int = 25,
    filter: str | None = None,
) -> dict[str, Any]:
    """List runs from this agent's own LangSmith tracing project.

    The project is fixed by the `LANGSMITH_TRACING_PROJECT_ID_PROD` env var
    and cannot be overridden from tool arguments. Use this to inspect past
    runs of this deployment — for example, to answer questions about run
    volume, latency, token spend, or error rates.

    Args:
        start_time_iso: Inclusive ISO-8601 lower bound on run start_time
            (e.g. "2025-01-01T00:00:00Z"). Defaults to whatever the server
            uses (typically last 7 days).
        end_time_iso: Inclusive ISO-8601 upper bound, applied as a
            LangSmith filter expression.
        run_type: Filter by run type — one of "llm", "chain", "tool",
            "retriever", "embedding", "prompt", "parser".
        is_root: If True (default), only return root runs (one per thread
            invocation). Set False to include child runs.
        error: If True, only errored runs. If False, only successful runs.
            If None (default), both.
        limit: Max runs to return (capped at 100).
        filter: Advanced LangSmith filter expression (e.g.
            `and(eq(status, "error"), gt(total_tokens, 10000))`). Combined
            with the other filter args via AND at the API layer.

    Returns:
        Dict with `runs` (list of summary dicts), `count`, and
        `project_id` (the scoped project).
    """
    try:
        project_id = get_scoped_langsmith_project_id()
        client = get_scoped_langsmith_client()
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        start = _parse_iso(start_time_iso, field="start_time_iso")
    except ValueError as e:
        return {"error": str(e)}

    end_filter = None
    if end_time_iso:
        try:
            _parse_iso(end_time_iso, field="end_time_iso")
        except ValueError as e:
            return {"error": str(e)}
        end_filter = f'lt(start_time, "{end_time_iso}")'

    combined_filter = None
    parts = [p for p in (filter, end_filter) if p]
    if len(parts) == 1:
        combined_filter = parts[0]
    elif len(parts) > 1:
        combined_filter = f"and({', '.join(parts)})"

    capped = max(1, min(limit, _MAX_LIMIT))

    try:
        runs_iter = client.list_runs(
            project_id=project_id,
            start_time=start,
            run_type=run_type,
            is_root=is_root,
            error=error,
            filter=combined_filter,
            limit=capped,
            select=_RUN_SELECT_FIELDS,
        )
        runs = [_run_summary(r) for r in runs_iter]
    except Exception as e:  # noqa: BLE001
        logger.exception("langsmith_list_runs failed")
        return {"error": f"{type(e).__name__}: {e}"}

    return {"runs": runs, "count": len(runs), "project_id": project_id}


def langsmith_get_run(run_id: str) -> dict[str, Any]:
    """Fetch a single run by ID from this agent's tracing project.

    Returns an error (not the run) if the run belongs to a different
    project — this defends against an agent being tricked into echoing
    a run ID from another workspace project.

    Args:
        run_id: The LangSmith run UUID.

    Returns:
        Dict with `run` (full run dict including inputs/outputs) and
        `project_id`, or `error`.
    """
    try:
        project_id = get_scoped_langsmith_project_id()
        client = get_scoped_langsmith_client()
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        run = client.read_run(run_id, load_child_runs=False)
    except Exception as e:  # noqa: BLE001
        logger.exception("langsmith_get_run failed")
        return {"error": f"{type(e).__name__}: {e}"}

    run_session_id = str(run.session_id) if getattr(run, "session_id", None) else None
    if run_session_id != project_id:
        return {
            "error": (
                f"Run {run_id} does not belong to the scoped tracing project "
                f"({project_id}); refusing to return it."
            )
        }

    summary = _run_summary(run)
    summary["inputs"] = getattr(run, "inputs", None)
    summary["outputs"] = getattr(run, "outputs", None)
    return {"run": summary, "project_id": project_id}


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def langsmith_project_stats(
    start_time_iso: str | None = None,
    end_time_iso: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Aggregate stats over root runs in this agent's tracing project.

    Computes run count, error count, token totals (prompt/completion/total),
    total cost, and latency percentiles (p50/p90/p99) over the matched
    window. Only root runs are included so each agent invocation is
    counted once.

    Args:
        start_time_iso: Inclusive ISO-8601 lower bound on run start_time.
        end_time_iso: Inclusive ISO-8601 upper bound.
        limit: Max runs to scan (capped at 1000). Stats are computed over
            the scanned set, so widen this if you expect more traffic.

    Returns:
        Dict with aggregated counters and percentiles, plus `scanned`
        (how many runs contributed) and `project_id`.
    """
    try:
        project_id = get_scoped_langsmith_project_id()
        client = get_scoped_langsmith_client()
    except RuntimeError as e:
        return {"error": str(e)}

    try:
        start = _parse_iso(start_time_iso, field="start_time_iso")
    except ValueError as e:
        return {"error": str(e)}

    end_filter = None
    if end_time_iso:
        try:
            _parse_iso(end_time_iso, field="end_time_iso")
        except ValueError as e:
            return {"error": str(e)}
        end_filter = f'lt(start_time, "{end_time_iso}")'

    capped = max(1, min(limit, _STATS_MAX_LIMIT))

    try:
        runs_iter = client.list_runs(
            project_id=project_id,
            start_time=start,
            is_root=True,
            filter=end_filter,
            limit=capped,
            select=_RUN_SELECT_FIELDS,
        )
        runs = list(runs_iter)
    except Exception as e:  # noqa: BLE001
        logger.exception("langsmith_project_stats failed")
        return {"error": f"{type(e).__name__}: {e}"}

    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_cost = 0.0
    errors = 0
    latencies: list[float] = []

    for r in runs:
        if getattr(r, "error", None):
            errors += 1
        total_tokens += int(getattr(r, "total_tokens", 0) or 0)
        prompt_tokens += int(getattr(r, "prompt_tokens", 0) or 0)
        completion_tokens += int(getattr(r, "completion_tokens", 0) or 0)
        cost = getattr(r, "total_cost", None)
        if cost is not None:
            total_cost += float(cost)
        start_t = getattr(r, "start_time", None)
        end_t = getattr(r, "end_time", None)
        if start_t and end_t:
            latencies.append((end_t - start_t).total_seconds())

    latencies.sort()
    count = len(runs)
    return {
        "project_id": project_id,
        "scanned": count,
        "scan_limit": capped,
        "scan_limit_reached": count >= capped,
        "run_count": count,
        "error_count": errors,
        "error_rate": (errors / count) if count else 0.0,
        "tokens": {
            "total": total_tokens,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "avg_total_per_run": (total_tokens / count) if count else 0,
        },
        "cost_usd": {
            "total": round(total_cost, 4),
            "avg_per_run": round(total_cost / count, 6) if count else 0.0,
        },
        "latency_seconds": {
            "p50": _percentile(latencies, 0.50),
            "p90": _percentile(latencies, 0.90),
            "p99": _percentile(latencies, 0.99),
            "max": latencies[-1] if latencies else None,
        },
    }
