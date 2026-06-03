"""Persist reviewer finding outcomes to a LangSmith dataset.

Every time a published finding is resolved (a later commit fixed it),
dismissed (false positive / human pushback), or gets a 👍/👎 reaction, we
upsert one labelled example into a single LangSmith dataset. The ``analyzer``
graph reads these per-repo to refine its review-style prompts: confirmed
findings teach what to hunt for, dismissed ones teach what to skip.

Writes are best-effort and deterministic (one example id per
``finding_id`` + ``label_source``) so re-processing the same transition
updates in place instead of duplicating.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from langsmith import Client as LangSmithClient

logger = logging.getLogger(__name__)

OUTCOMES_DATASET_NAME = os.environ.get("REVIEWER_OUTCOMES_DATASET", "openswe-reviewer-outcomes")

TRUE_POSITIVE = "true_positive"
FALSE_POSITIVE = "false_positive"

_DIFF_HUNK_MAX_CHARS = 4000


def outcome_from_status(
    status: str,
    *,
    first_seen_sha: str | None,
    head_sha: str | None,
) -> tuple[str, str] | None:
    """Map a finding status transition to ``(label, label_source)``.

    ``resolved`` is a positive signal: we flagged something and it was closed.
    When a commit landed between first sighting and resolution we tag it
    ``resolved_by_commit`` (a fix shipped); otherwise ``resolved_same_sha``.
    """
    if status == "resolved":
        if head_sha and first_seen_sha and head_sha != first_seen_sha:
            return TRUE_POSITIVE, "resolved_by_commit"
        return TRUE_POSITIVE, "resolved_same_sha"
    if status == "dismissed":
        return FALSE_POSITIVE, "dismissed"
    return None


def outcome_from_score(score: float | None, *, source: str) -> tuple[str, str] | None:
    """Map a reaction score (1.0 👍 / 0.0 👎) to ``(label, label_source)``."""
    if score is None:
        return None
    if score >= 1.0:
        return TRUE_POSITIVE, f"{source}_thumbs_up"
    return FALSE_POSITIVE, f"{source}_thumbs_down"


def _outcomes_client() -> LangSmithClient | None:
    """Build a single LangSmith client, preferring the prod tenant."""
    prod_key = os.environ.get("LANGSMITH_API_KEY_PROD")
    if prod_key:
        api_url = os.environ.get("LANGSMITH_ENDPOINT_PROD") or os.environ.get(
            "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
        )
        return LangSmithClient(api_key=prod_key, api_url=api_url)
    api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        return None
    api_url = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return LangSmithClient(api_key=api_key, api_url=api_url)


def _ensure_dataset(client: LangSmithClient) -> Any:
    existing = next((d for d in client.list_datasets(dataset_name=OUTCOMES_DATASET_NAME)), None)
    if existing is not None:
        return existing.id
    ds = client.create_dataset(
        dataset_name=OUTCOMES_DATASET_NAME,
        description=(
            "Open SWE reviewer finding outcomes (resolved / dismissed / 👍👎) "
            "captured in production for per-repo continual learning."
        ),
    )
    return ds.id


def _example_id(repo: str, finding_id: str, label_source: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"finding-outcome:{repo}:{finding_id}:{label_source}")


def _truncate(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def _create_or_update_example(
    client: LangSmithClient,
    *,
    dataset_id: Any,
    example_id: uuid.UUID,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    try:
        client.create_example(
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
            dataset_id=dataset_id,
            example_id=example_id,
        )
    except Exception:  # noqa: BLE001 — example already exists; update in place
        client.update_example(
            example_id=example_id,
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
        )


def upsert_finding_outcome(
    finding: dict[str, Any],
    *,
    label: str,
    label_source: str,
    repo: str,
    pr_number: int | None = None,
    pr_url: str = "",
    base_sha: str = "",
    head_sha: str = "",
    run_id: str | None = None,
    thread_id: str | None = None,
) -> bool:
    """Upsert one finding-level outcome example. Best-effort; never raises."""
    finding_id = str(finding.get("id") or "")
    if not finding_id or not repo:
        return False
    client = _outcomes_client()
    if client is None:
        logger.debug("No LangSmith client configured; skipping outcome example")
        return False
    try:
        dataset_id = _ensure_dataset(client)
        inputs = {
            "repo": repo,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "file": finding.get("file"),
            "start_line": finding.get("start_line"),
            "end_line": finding.get("end_line"),
            "side": finding.get("side", "RIGHT"),
            "diff_hunk": _truncate(finding.get("diff_hunk"), _DIFF_HUNK_MAX_CHARS),
            "base_sha": base_sha,
            "head_sha": head_sha,
        }
        outputs = {
            "label": label,
            "label_source": label_source,
            "finding": {
                "title": finding.get("title"),
                "description": _truncate(finding.get("description"), _DIFF_HUNK_MAX_CHARS),
                "severity": finding.get("severity"),
                "confidence": finding.get("confidence"),
                "category": finding.get("category"),
            },
            "resolution_note": finding.get("resolution_note"),
        }
        metadata = {
            "granularity": "finding",
            "repo": repo,
            "finding_id": finding_id,
            "label": label,
            "label_source": label_source,
            "run_id": run_id or finding.get("github_review_run_id"),
            "thread_id": thread_id,
            "first_seen_sha": finding.get("first_seen_sha"),
        }
        _create_or_update_example(
            client,
            dataset_id=dataset_id,
            example_id=_example_id(repo, finding_id, label_source),
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
        )
        return True
    except Exception:  # noqa: BLE001 — dataset writes must never break a review
        logger.exception("Failed to upsert reviewer finding outcome for %s", finding_id)
        return False


def upsert_run_outcome(
    *,
    label: str,
    label_source: str,
    run_id: str,
    repo: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Upsert a coarse run-level outcome (e.g. a Slack 👍/👎 on a review).

    These have no finding/diff anchor, so they are tagged
    ``granularity="run"`` and ignored by the per-repo analyzer reader.
    """
    if not run_id:
        return False
    client = _outcomes_client()
    if client is None:
        return False
    try:
        dataset_id = _ensure_dataset(client)
        inputs = {"repo": repo, "run_id": run_id, **(extra or {})}
        outputs = {"label": label, "label_source": label_source}
        metadata = {
            "granularity": "run",
            "repo": repo,
            "run_id": run_id,
            "label": label,
            "label_source": label_source,
        }
        _create_or_update_example(
            client,
            dataset_id=dataset_id,
            example_id=uuid.uuid5(uuid.NAMESPACE_URL, f"run-outcome:{run_id}:{label_source}"),
            inputs=inputs,
            outputs=outputs,
            metadata=metadata,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Failed to upsert run outcome for run %s", run_id)
        return False


def repo_full_name_from_config(configurable: dict[str, Any]) -> str:
    repo = configurable.get("repo") if isinstance(configurable, dict) else None
    if isinstance(repo, dict) and repo.get("owner") and repo.get("name"):
        return f"{repo['owner']}/{repo['name']}"
    return ""


def emit_finding_status_outcome(
    finding: dict[str, Any],
    status: str,
    *,
    configurable: dict[str, Any],
    thread_id: str | None = None,
) -> bool:
    """Map a resolve/dismiss transition to an outcome example and upsert it.

    Reads repo / PR / SHA context from the reviewer run ``configurable``.
    Best-effort: a no-op when the status isn't terminal or repo is unknown.
    """
    head_sha = str(configurable.get("head_sha") or "")
    mapping = outcome_from_status(
        status, first_seen_sha=finding.get("first_seen_sha"), head_sha=head_sha
    )
    if mapping is None:
        return False
    repo = repo_full_name_from_config(configurable)
    if not repo:
        return False
    label, label_source = mapping
    pr_number = configurable.get("pr_number")
    return upsert_finding_outcome(
        finding,
        label=label,
        label_source=label_source,
        repo=repo,
        pr_number=pr_number if isinstance(pr_number, int) else None,
        pr_url=str(configurable.get("pr_url") or ""),
        base_sha=str(configurable.get("base_sha") or ""),
        head_sha=head_sha,
        thread_id=thread_id,
    )


def read_outcomes_for_repo(repo: str, *, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
    """Return confirmed (true-positive) and dismissed (false-positive) findings
    for ``repo`` from the outcomes dataset. Best-effort; returns empty on error."""
    confirmed: list[dict[str, Any]] = []
    dismissed: list[dict[str, Any]] = []
    client = _outcomes_client()
    if client is None or not repo:
        return {"confirmed": confirmed, "dismissed": dismissed}
    try:
        existing = next((d for d in client.list_datasets(dataset_name=OUTCOMES_DATASET_NAME)), None)
        if existing is None:
            return {"confirmed": confirmed, "dismissed": dismissed}
        for example in client.list_examples(dataset_id=existing.id):
            metadata = getattr(example, "metadata", None) or {}
            if metadata.get("granularity") != "finding" or metadata.get("repo") != repo:
                continue
            outputs = getattr(example, "outputs", None) or {}
            inputs = getattr(example, "inputs", None) or {}
            finding = outputs.get("finding") or {}
            row = {
                "file": inputs.get("file"),
                "title": finding.get("title"),
                "description": finding.get("description"),
                "severity": finding.get("severity"),
                "category": finding.get("category"),
                "label_source": outputs.get("label_source"),
                "diff_hunk": inputs.get("diff_hunk"),
                "resolution_note": outputs.get("resolution_note"),
            }
            if outputs.get("label") == TRUE_POSITIVE:
                confirmed.append(row)
            elif outputs.get("label") == FALSE_POSITIVE:
                dismissed.append(row)
            if len(confirmed) + len(dismissed) >= limit:
                break
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read outcomes for repo %s", repo)
    return {"confirmed": confirmed, "dismissed": dismissed}
