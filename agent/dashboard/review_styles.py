"""Per-repository review style profiles in LangGraph Store.

Each record holds a synthesized custom prompt (editable in the dashboard),
analysis metadata, and the status of the background style-analysis run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

REVIEW_STYLES_NAMESPACE: list[str] = ["review_styles"]

AnalysisStatus = Literal["idle", "running", "completed", "failed"]


def normalize_repo_full_name(raw: str) -> str:
    """Normalize user input to ``owner/repo``."""
    v = raw.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
    v = v.strip("/")
    if v.endswith(".git"):
        v = v[:-4]
    parts = [p for p in v.split("/") if p]
    if len(parts) != 2:
        raise ValueError("full_name must be owner/repo")
    return f"{parts[0]}/{parts[1]}"


class ReviewStyleCreate(BaseModel):
    full_name: str = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        return normalize_repo_full_name(v)


class ReviewStylePromptUpdate(BaseModel):
    custom_prompt: str

    @field_validator("custom_prompt")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("custom_prompt cannot be empty")
        return v


def _client():
    return get_client()


async def _get_value(key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(REVIEW_STYLES_NAMESPACE, key)
    except Exception as e:
        logger.debug("store get_item failed for %s: %s", key, e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_record(full_name: str, created_by: str) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": owner,
        "name": name,
        "status": "idle",
        "custom_prompt": None,
        "analysis_summary": None,
        "top_reviewers": [],
        "prs_sampled": 0,
        "reviews_sampled": 0,
        "analysis_thread_id": None,
        "analysis_run_id": None,
        "error": None,
        "created_by": created_by,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


async def get_review_style(full_name: str) -> dict[str, Any] | None:
    return await _get_value(full_name)


async def list_review_styles() -> list[dict[str, Any]]:
    result = await _client().store.search_items(REVIEW_STYLES_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    out.sort(key=lambda r: r.get("full_name", ""))
    return out


async def create_review_style(full_name: str, created_by: str) -> dict[str, Any]:
    existing = await get_review_style(full_name)
    if existing:
        return existing
    value = _default_record(full_name, created_by)
    await _client().store.put_item(REVIEW_STYLES_NAMESPACE, full_name, value)
    return value


async def update_review_style(full_name: str, patch: dict[str, Any]) -> dict[str, Any]:
    existing = await get_review_style(full_name) or _default_record(
        full_name, patch.get("created_by", "")
    )
    value = {**existing, **patch, "updated_at": _now_iso()}
    await _client().store.put_item(REVIEW_STYLES_NAMESPACE, full_name, value)
    return value


async def set_custom_prompt(full_name: str, custom_prompt: str) -> dict[str, Any]:
    return await update_review_style(full_name, {"custom_prompt": custom_prompt})


async def mark_analysis_running(
    full_name: str,
    *,
    thread_id: str,
    run_id: str | None,
    top_reviewers: list[str],
    prs_sampled: int,
    reviews_sampled: int,
) -> dict[str, Any]:
    return await update_review_style(
        full_name,
        {
            "status": "running",
            "analysis_thread_id": thread_id,
            "analysis_run_id": run_id,
            "top_reviewers": top_reviewers,
            "prs_sampled": prs_sampled,
            "reviews_sampled": reviews_sampled,
            "error": None,
        },
    )


async def mark_analysis_completed(
    full_name: str,
    *,
    custom_prompt: str,
    analysis_summary: str,
    top_reviewers: list[str],
    prs_sampled: int,
    reviews_sampled: int,
) -> dict[str, Any]:
    return await update_review_style(
        full_name,
        {
            "status": "completed",
            "custom_prompt": custom_prompt,
            "analysis_summary": analysis_summary,
            "top_reviewers": top_reviewers,
            "prs_sampled": prs_sampled,
            "reviews_sampled": reviews_sampled,
            "error": None,
        },
    )


async def mark_analysis_failed(full_name: str, error: str) -> dict[str, Any]:
    return await update_review_style(full_name, {"status": "failed", "error": error})


async def get_repo_custom_prompt(owner: str, repo: str) -> str | None:
    """Return the custom prompt supplement for a repo, if configured."""
    full_name = f"{owner}/{repo}"
    record = await get_review_style(full_name)
    if not record:
        return None
    prompt = record.get("custom_prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None
