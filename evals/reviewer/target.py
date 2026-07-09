"""Target function for the reviewer eval.

Spawns the reviewer graph over `langgraph_sdk` for one PR, waits for
completion, and returns every `add_finding` tool call the agent made as the
structured output for the eval. Findings are normalized into the legacy
``{file, line, body, severity}`` shape so the judge prompt can stay the
verbatim form martian published.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Literal, cast

from langgraph_sdk import get_client

from agent.reviewer_findings import (
    REVIEW_FINDING_CAP,
    REVIEWER_EVAL_PUBLICATION_KEY,
    Finding,
    Severity,
)

logger = logging.getLogger(__name__)

DEFAULT_REVIEWER_ASSISTANT_ID = "reviewer"
DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
ScoreMode = Literal["all_findings", "surfaced_findings"]
_VALID_SCORE_MODES: set[ScoreMode] = {"all_findings", "surfaced_findings"}
_VALID_SEVERITIES: set[Severity] = {"low", "medium", "high", "critical"}

_THREAD_IDS: set[str] = set()
_THREAD_IDS_LOCK = threading.Lock()

_COMPLETED = 0
_COMPLETED_LOCK = threading.Lock()


def _record_completed() -> None:
    global _COMPLETED
    with _COMPLETED_LOCK:
        _COMPLETED += 1


def get_completed_count() -> int:
    """Number of examples that have finished so far in this process.

    Read by ``store_reporter`` to publish progress to the dashboard.
    """
    with _COMPLETED_LOCK:
        return _COMPLETED


def _record_thread_id(thread_id: str) -> None:
    with _THREAD_IDS_LOCK:
        _THREAD_IDS.add(thread_id)


def drain_thread_ids() -> set[str]:
    """Return and clear thread IDs created by ``review_pr`` so far.

    Used by ``run_eval`` to delete threads after the experiment finishes.
    Underlying provider sandboxes time out via their own TTL — deleting the
    LangGraph thread frees the checkpoint/metadata records, not the sandbox.
    """
    with _THREAD_IDS_LOCK:
        snapshot = set(_THREAD_IDS)
        _THREAD_IDS.clear()
    return snapshot


def get_langgraph_url() -> str:
    return os.getenv("LANGGRAPH_URL", DEFAULT_LANGGRAPH_URL)


def get_reviewer_assistant_id() -> str:
    return os.getenv("REVIEWER_ASSISTANT_ID", DEFAULT_REVIEWER_ASSISTANT_ID)


def get_score_mode() -> ScoreMode:
    value = os.getenv("REVIEWER_EVAL_SCORE_MODE", "surfaced_findings")
    if value in _VALID_SCORE_MODES:
        return cast(ScoreMode, value)
    return "surfaced_findings"


def get_reviewer_model_id() -> str | None:
    value = os.getenv("REVIEWER_EVAL_MODEL_ID")
    return value if value else None


def get_reviewer_reasoning_effort() -> str | None:
    value = os.getenv("REVIEWER_EVAL_REASONING_EFFORT")
    return value if value else None


def _build_user_message(inputs: dict[str, Any]) -> str:
    return (
        f"Review pull request {inputs['pr_url']}.\n\n"
        f"- repo: {inputs['repo']}\n"
        f"- pr_number: {inputs['pr_number']}\n"
        f"- title: {inputs.get('pr_title', '')}\n"
        f"- base_sha: {inputs['base_sha']}\n"
        f"- head_sha: {inputs['head_sha']}\n"
        f"- base_ref: {inputs.get('base_ref', '')}\n"
        f"- head_ref: {inputs.get('head_ref', '')}\n\n"
        f"Record each issue you find with the `add_finding` tool, then call "
        f"`publish_review` once at the end."
    )


def _build_configurable(inputs: dict[str, Any]) -> dict[str, Any]:
    repo = inputs.get("repo", "")
    owner, _, name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
    configurable: dict[str, Any] = {
        "__is_for_execution__": True,
        "reviewer_eval": True,
        "eval": True,
        "repo": {"owner": owner, "name": name},
        "pr_number": inputs.get("pr_number"),
        "pr_url": inputs.get("pr_url", ""),
        "base_sha": inputs.get("base_sha", ""),
        "head_sha": inputs.get("head_sha", ""),
        "branch_name": inputs.get("head_ref", ""),
        "reviewer_eval_severity_threshold": _score_severity_threshold(),
        "reviewer_eval_cap": _score_cap(),
    }
    model_id = get_reviewer_model_id()
    if model_id:
        configurable["reviewer_model_id"] = model_id
    reasoning_effort = get_reviewer_reasoning_effort()
    if reasoning_effort:
        configurable["reviewer_reasoning_effort"] = reasoning_effort
    return configurable


async def review_pr(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith target: run the reviewer agent on one PR."""
    repo = inputs.get("repo", "")
    pr_number = inputs.get("pr_number")
    pr_url = inputs.get("pr_url", "")
    logger.info(
        "Starting reviewer eval example: repo=%s pr=%s url=%s",
        repo,
        pr_number,
        pr_url,
    )
    client = get_client(url=get_langgraph_url())
    thread = await client.threads.create()
    thread_id: str = thread["thread_id"]
    _record_thread_id(thread_id)
    try:
        result = await client.runs.wait(
            thread_id,
            assistant_id=get_reviewer_assistant_id(),
            input={"messages": [{"role": "user", "content": _build_user_message(inputs)}]},
            config={"configurable": _build_configurable(inputs)},
        )
        score_mode = get_score_mode()
        publish_completed = True
        if score_mode == "surfaced_findings":
            comments, publish_completed = await _extract_surfaced_comments(client, thread_id)
        else:
            comments = _extract_comments(result)
        logger.info(
            "Finished reviewer eval example: repo=%s pr=%s comments=%d thread_id=%s",
            repo,
            pr_number,
            len(comments),
            thread_id,
        )
        _record_completed()
        return {
            "comments": comments,
            "score_mode": score_mode,
            "publish_completed": publish_completed,
            "score_cap": REVIEW_FINDING_CAP,
        }
    except Exception:
        logger.exception("Reviewer eval example failed: repo=%s pr=%s", repo, pr_number)
        raise


def _extract_comments(result: Any) -> list[dict[str, Any]]:
    """Collect every ``add_finding`` tool call from the run's message stream.

    Normalizes the new finding shape (``start_line``/``end_line``/``description``)
    into the legacy ``{file, line, body, severity}`` shape the judge prompt
    consumes verbatim from martian's benchmark.
    """
    if not isinstance(result, dict):
        return []
    comments: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str]] = set()
    for msg in result.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if tc.get("name") != "add_finding":
                continue
            args = tc.get("args") or {}
            file = args.get("file")
            severity = args.get("severity")
            description = args.get("description") or args.get("body") or ""
            line = args.get("end_line")
            if line is None:
                line = args.get("start_line")
            if not file or not severity:
                continue
            key = (
                file,
                line if isinstance(line, int) else None,
                " ".join(description.casefold().split()),
            )
            if key in seen:
                continue
            seen.add(key)
            comments.append(
                {
                    "file": file,
                    "line": line,
                    "body": description,
                    "severity": severity,
                }
            )
    return comments


async def _extract_surfaced_comments(
    client: Any, thread_id: str
) -> tuple[list[dict[str, Any]], bool]:
    thread = await client.threads.get(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    findings_value = metadata.get("findings") if isinstance(metadata, dict) else None
    findings = _coerce_findings(findings_value)
    publication_value = (
        metadata.get(REVIEWER_EVAL_PUBLICATION_KEY) if isinstance(metadata, dict) else None
    )
    if not isinstance(publication_value, dict):
        logger.warning("Reviewer eval thread %s has no publication snapshot", thread_id)
        return [], False
    finding_ids = publication_value.get("finding_ids")
    if not isinstance(finding_ids, list) or not all(
        isinstance(finding_id, str) for finding_id in finding_ids
    ):
        logger.warning("Reviewer eval thread %s has an invalid publication snapshot", thread_id)
        return [], False
    by_id = {finding.get("id"): finding for finding in findings}
    surfaced = [by_id[finding_id] for finding_id in finding_ids if finding_id in by_id]
    return [_normalize_finding(finding) for finding in surfaced], True


def _coerce_findings(value: Any) -> list[Finding]:
    if not isinstance(value, list):
        return []
    findings: list[Finding] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("id"), str):
            continue
        findings.append(cast(Finding, item))
    return findings


def _normalize_finding(finding: Finding) -> dict[str, Any]:
    line = finding.get("end_line")
    if line is None:
        line = finding.get("start_line")
    return {
        "file": finding.get("file"),
        "line": line,
        "body": finding.get("description", ""),
        "severity": finding.get("severity"),
    }


def _score_severity_threshold() -> Severity:
    value = os.getenv("REVIEWER_EVAL_SEVERITY_THRESHOLD", "low")
    if value in _VALID_SEVERITIES:
        return cast(Severity, value)
    return "low"


def _score_cap() -> int:
    raw = os.getenv("REVIEWER_EVAL_CAP", str(REVIEW_FINDING_CAP))
    try:
        cap = int(raw)
    except ValueError:
        return REVIEW_FINDING_CAP
    return max(cap, 0)
