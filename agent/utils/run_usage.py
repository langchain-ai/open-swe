from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .langsmith import _build_prod_langsmith_client
from .tracing import AGENT_TRACING_PROJECT

logger = logging.getLogger(__name__)

USAGE_RUN_METADATA_KEY = "open_swe_run_id"


@dataclass(frozen=True)
class RunUsageSummary:
    models: tuple[str, ...]
    total_tokens: int | None
    total_cost: Decimal | None


def _value(run: Any, name: str) -> Any:
    if isinstance(run, dict):
        return run.get(name)
    return getattr(run, name, None)


def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _has_token_data(run: Any) -> bool:
    return any(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (
            _value(run, "total_tokens"),
            _value(run, "prompt_tokens"),
            _value(run, "completion_tokens"),
        )
    )


def _run_tokens(run: Any) -> int:
    total = _positive_int(_value(run, "total_tokens"))
    if total:
        return total
    return _positive_int(_value(run, "prompt_tokens")) + _positive_int(
        _value(run, "completion_tokens")
    )


def _run_cost(run: Any) -> Decimal | None:
    value = _value(run, "total_cost")
    if value is None:
        prompt = _value(run, "prompt_cost")
        completion = _value(run, "completion_cost")
        if prompt is None or completion is None:
            return None
        try:
            value = Decimal(str(prompt)) + Decimal(str(completion))
        except (InvalidOperation, TypeError, ValueError):
            return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return cost if cost >= 0 else None


def _run_model(run: Any) -> str | None:
    extra = _value(run, "extra")
    if not isinstance(extra, dict):
        return None
    metadata = extra.get("metadata")
    if not isinstance(metadata, dict):
        return None
    model = metadata.get("ls_model_name")
    return model.strip() if isinstance(model, str) and model.strip() else None


def aggregate_run_usage(runs: list[Any]) -> RunUsageSummary | None:
    seen: set[str] = set()
    models: set[str] = set()
    total_tokens = 0
    tokens_complete = True
    total_cost = Decimal(0)
    cost_complete = True
    has_cost = False
    found = False

    for run in runs:
        run_id = _value(run, "id")
        dedupe_key = str(run_id) if run_id is not None else ""
        if dedupe_key and dedupe_key in seen:
            continue
        if dedupe_key:
            seen.add(dedupe_key)
        found = True
        model = _run_model(run)
        if model:
            models.add(model)
        if not _has_token_data(run):
            tokens_complete = False
        tokens = _run_tokens(run)
        total_tokens += tokens
        cost = _run_cost(run)
        if cost is None:
            cost_complete = False
        else:
            total_cost += cost
            has_cost = True

    if not found:
        return None
    return RunUsageSummary(
        models=tuple(sorted(models)),
        total_tokens=total_tokens if tokens_complete and total_tokens else None,
        total_cost=total_cost if cost_complete and has_cost else None,
    )


def _metadata_filter(key: str, value: str) -> str:
    escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'and(eq(metadata_key, "{escaped_key}"), eq(metadata_value, "{escaped_value}"))'


async def fetch_run_usage(
    run_id: str, *, usage_run_id: str | None = None
) -> RunUsageSummary | None:
    client = _build_prod_langsmith_client()
    if client is None:
        return None

    def _fetch() -> RunUsageSummary | None:
        query: dict[str, Any] = {
            "project_name": AGENT_TRACING_PROJECT,
            "run_type": "llm",
            "select": [
                "id",
                "extra",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cost",
                "completion_cost",
                "total_cost",
            ],
        }
        if usage_run_id:
            query["filter"] = _metadata_filter(USAGE_RUN_METADATA_KEY, usage_run_id)
        else:
            root = client.read_run(run_id, load_child_runs=False)
            trace_id = _value(root, "trace_id") or _value(root, "id")
            if trace_id is None:
                return None
            query["trace_id"] = trace_id
        runs = list(client.list_runs(**query))
        return aggregate_run_usage(runs)

    try:
        return await asyncio.to_thread(_fetch)
    except Exception:
        logger.debug("Could not load LangSmith usage for run %s", run_id, exc_info=True)
        return None
